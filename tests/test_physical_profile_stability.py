"""体型目录改版只影响新实例；旧资产价值、派遣和战斗能力保持捕获快照。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pig_catcher.assets import AssetCatalogStorage
from pig_catcher.config.model import CatchingSection, CookingSection, EconomySection
from pig_catcher.domain.dispatch import REGIONS_BY_ID, team_bonus
from pig_catcher.domain.enums import Rarity
from pig_catcher.domain.gameplay import generate_pig_attributes
from pig_catcher.domain.special_content import SUKUNA_PIG_TEMPLATE_ID
from pig_catcher.infrastructure.repositories.battle import BattleRepository
from pig_catcher.infrastructure.repositories.dispatch import DispatchRepository, iso_ms, timestamp_ms
from pig_catcher.infrastructure.repositories.gameplay import GameplayRepository
from pig_catcher.services import AssetCatalogService, EconomyService, FrameworkService, GameplayService

from .test_dispatch import NOW, seed_pigs
from .test_economy import _insert_food
from .test_gameplay import (
    MutableClock,
    SequenceRandom,
    _catch_rolls,
    _database_with_catalog,
    _food_entry,
    _identity,
    _pig_entry,
)

DUKE_ID = "pig-r5-dragonfish-duke"
RANGES = (
    pytest.param((4.0, 16.0, 0.35, 6.0, "mini"), id="mini"),
    pytest.param((180.0, 550.0, 600.0, 6500.0, "giant"), id="tonne-giant"),
    pytest.param((1200.0, 3600.0, 20000.0, 100000.0, "giant"), id="cosmic-game-scale"),
    pytest.param((34.0, 118.0, 0.05, 2.0, "standard"), id="flat-lightweight"),
)
RANGE_KEYS = ("length_min_cm", "length_max_cm", "weight_min_kg", "weight_max_kg", "stature_profile")


@pytest.mark.parametrize("rarity", list(Rarity))
@pytest.mark.parametrize("percentile", [0.0, 0.5, 0.92, 0.999])
def test_same_rarity_and_percentiles_keep_official_value_across_physical_scales(rarity, percentile):
    """同一相对品质不能因为 kg 改为吨级而额外铸造价值。"""
    scales = (
        (4.0, 16.0, 0.35, 6.0),
        (30.0, 70.0, 20.0, 120.0),
        (180.0, 550.0, 600.0, 6500.0),
        (1200.0, 3600.0, 20000.0, 100000.0),
        (34.0, 118.0, 0.05, 2.0),
    )
    for fat_profile in ("lean", "balanced", "fatty"):
        generated = [
            generate_pig_attributes(
                rarity=rarity,
                length_min=low_length,
                length_max=high_length,
                weight_min=low_weight,
                weight_max=high_weight,
                fat_profile=fat_profile,
                random_values=(percentile,) * 5,
            )
            for low_length, high_length, low_weight, high_weight in scales
        ]
        assert len({pig.size_value for pig in generated}) == len(scales)
        assert len({pig.weight_value for pig in generated}) == len(scales)
        assert {pig.size_percentile for pig in generated} == {percentile}
        assert {pig.weight_percentile for pig in generated} == {percentile}
        assert len({pig.fat_ratio for pig in generated}) == 1
        assert len({pig.official_value for pig in generated}) == 1


@pytest.mark.parametrize("new_range", RANGES)
@pytest.mark.parametrize("percentile", [0.25, 0.749, 0.75, 0.9])
async def test_catalog_reimport_preserves_old_assets_dispatch_and_battle(
    tmp_path: Path, new_range, percentile: float
):
    """通过正式导入服务改模板；比较指定旧实例，而不是脆弱的全库快照。"""
    entries = [
        _pig_entry("pig-r1-basic", rarity=1),
        _pig_entry(SUKUNA_PIG_TEMPLATE_ID, rarity=5, display_name="宿傩猪"),
    ]
    database = await _database_with_catalog(tmp_path, entries)
    identity = _identity(message_id="physical-baseline")
    try:
        low_id = (await seed_pigs(database, identity, template_id="pig-r1-basic", count=1, value=431))[0]
        battle_id = (
            await seed_pigs(database, identity, template_id=SUKUNA_PIG_TEMPLATE_ID, count=1, value=1937)
        )[0]
        async with database.transaction() as session:
            # 仅布置离线实例：绝对数值与原目录百分位一致，旧价值故意不同于新生成公式。
            await session.execute(
                """UPDATE pig_instances SET size_value=?,size_percentile=?,
                weight_value=?,weight_percentile=?,fat_ratio=57.5
                WHERE pig_instance_id IN (?,?)""",
                (30 + 40 * percentile, percentile, 20 + 100 * percentile, percentile, low_id, battle_id),
            )
        before_assets = {
            pig_id: dict(await database.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (pig_id,)))
            for pig_id in (low_id, battle_id)
        }
        async with database.transaction(immediate=False) as session:
            before_members = [
                await DispatchRepository().member(session, identity.player_id, pig_id)
                for pig_id in (low_id, battle_id)
            ]
            before_battle = await BattleRepository().member(session, identity.player_id, battle_id)
        before_bonuses = {
            region_id: team_bonus(before_members, region) for region_id, region in REGIONS_BY_ID.items()
        }
        assert before_battle["trait_bonus"] == int(percentile >= 0.75)

        manifest = tmp_path / "source/assets.json"
        document = json.loads(manifest.read_text(encoding="utf-8"))
        changed_fields = dict(zip(RANGE_KEYS, new_range, strict=True))
        for entry in document["entries"]:
            entry.update(changed_fields)
        manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        await AssetCatalogService(
            database,
            AssetCatalogStorage(tmp_path / "data"),
            min_image_side=32,
            max_image_bytes=1024 * 1024,
        ).import_manifest(manifest)
        template = await database.fetch_one(
            "SELECT length_min,length_max,weight_min,weight_max,template_version "
            "FROM pig_templates WHERE template_id=?",
            (SUKUNA_PIG_TEMPLATE_ID,),
        )
        assert tuple(template) == (*new_range[:4], 2)

        for restart in (False, True):
            if restart:
                await database.close()
                await database.open()
            after_assets = {
                pig_id: dict(
                    await database.fetch_one("SELECT * FROM pig_instances WHERE pig_instance_id=?", (pig_id,))
                )
                for pig_id in (low_id, battle_id)
            }
            assert after_assets == before_assets
            async with database.transaction(immediate=False) as session:
                after_members = [
                    await DispatchRepository().member(session, identity.player_id, pig_id)
                    for pig_id in (low_id, battle_id)
                ]
                after_battle = await BattleRepository().member(session, identity.player_id, battle_id)
            for member in after_members:
                assert member["size_q"] == pytest.approx(percentile)
                assert member["weight_q"] == pytest.approx(percentile)
            assert {
                region_id: team_bonus(after_members, region) for region_id, region in REGIONS_BY_ID.items()
            } == before_bonuses
            assert after_battle["trait_bonus"] == before_battle["trait_bonus"]
            assert after_battle["level"] == before_battle["level"]
    finally:
        await database.close()


@pytest.mark.parametrize(
    "rarity_roll,group_id,expected_template,expected_star",
    [
        (0.69, "100", "pig-r4-z-ordinary", 4),
        (0.85, "100", DUKE_ID, 5),
        (0.995, "100", "pig-r6-z-authorized-normal", 6),
        (0.995, "200", DUKE_ID, 5),
    ],
    ids=["four-no-template-bias", "five-duke-biased", "six-no-template-bias", "foreign-scope-six-redistributes"],
)
async def test_giant_food_bias_uses_reviewed_duke_only_inside_authorized_five_star_pool(
    tmp_path: Path, rarity_roll: float, group_id: str, expected_template: str, expected_star: int
):
    """真实吃菜、抓猪链路验证巨物模板权重不跨星级或绕过群授权。"""
    definitions = json.loads(
        (Path(__file__).resolve().parents[1] / "catalogs/formal/pig-and-food-definitions.json").read_text(
            encoding="utf-8"
        )
    )["entries"]
    reviewed_duke = next(entry for entry in definitions if entry["template_id"] == DUKE_ID)
    assert reviewed_duke["rarity"] == 5 and reviewed_duke["stature_profile"] == "giant"
    duke = _pig_entry(DUKE_ID, rarity=5, display_name="猪龙鱼公爵")
    duke.update({key: reviewed_duke[key] for key in RANGE_KEYS})
    food_params = {"five_star_multiplier": 3.0, "stature_bias": 0.5, "giant_template_multiplier": 4.0}
    food_id = "food-giant-bias"
    food_name = "黑猪麻汤圆"
    entries = [
        *(_pig_entry(f"pig-r{star}-ordinary", rarity=star) for star in (1, 2, 3)),
        _pig_entry("pig-r4-a-giant", rarity=4, stature_profile="giant"),
        _pig_entry("pig-r4-z-ordinary", rarity=4),
        duke,
        _pig_entry("pig-r5-z-normal", rarity=5),
        _pig_entry("pig-r6-a-authorized-giant", rarity=6, group_id="100", stature_profile="giant"),
        _pig_entry("pig-r6-z-authorized-normal", rarity=6, group_id="100"),
        _pig_entry("pig-r6-b-foreign-giant", rarity=6, group_id="999", stature_profile="giant"),
        {
            **_pig_entry("pig-r6-c-revoked-giant", rarity=6, group_id="100", stature_profile="giant"),
            "consent_status": "revoked",
        },
        _pig_entry("pig-r6-d-disabled-giant", rarity=6, group_id="100", stature_profile="giant"),
        _food_entry(
            food_id,
            effect_id="next-giant-five-star-catch",
            effect_params=food_params,
            display_name=food_name,
            rarity=5,
            group_id=None,
        ),
    ]
    database = await _database_with_catalog(tmp_path, entries)
    clock = MutableClock(NOW)
    identity = _identity(group_id=group_id, message_id="eat-giant-bias")
    try:
        await FrameworkService(database, clock=clock).touch_identity(identity)
        async with database.transaction() as session:
            await session.execute("UPDATE pig_templates SET enabled=0 WHERE template_id='pig-r6-d-disabled-giant'")
            visible = await GameplayRepository().list_drawable_pig_templates(session, scope_id=identity.scope.value)
        visible_ids = {entry["template_id"] for entry in visible}
        assert {
            "pig-r6-b-foreign-giant",
            "pig-r6-c-revoked-giant",
            "pig-r6-d-disabled-giant",
        }.isdisjoint(visible_ids)
        six_ids = {entry["template_id"] for entry in visible if entry["rarity"] == 6}
        assert six_ids == (
            {"pig-r6-a-authorized-giant", "pig-r6-z-authorized-normal"} if group_id == "100" else set()
        )
        ordinary_five = GameplayService._select_template(GameplayService._template_buckets(visible)[Rarity.FIVE], 0.6)
        assert ordinary_five["template_id"] == "pig-r5-z-normal"

        await _insert_food(
            database,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            template_id=food_id,
            display_name=food_name,
            official_value=800,
            short_code="GIANT001",
            instance_id="giant-food-instance",
            rarity=5,
            effect_id="next-giant-five-star-catch",
            effect_params=food_params,
            now=iso_ms(timestamp_ms(NOW)),
        )
        eat = await EconomyService(database, CookingSection(), EconomySection(), clock=clock).eat(
            identity, f"{food_name}#GIANT001"
        )
        assert eat.effect.queued_effect_id == "next-giant-five-star-catch"
        # 保留可被转移的低星权重，避免100%单星夹具让提升效果判为不适用而保留。
        catching = CatchingSection(
            cooldown_seconds=0,
            rarity_1_weight=60,
            rarity_2_weight=10,
            rarity_3_weight=10,
            rarity_4_weight=10,
            rarity_5_weight=8,
            rarity_6_weight=2,
        )
        draws = SequenceRandom(*_catch_rolls(rarity_roll=rarity_roll, template_roll=0.6))
        result = await GameplayService(database, catching, random_source=draws, clock=clock).catch(
            replace(identity, message_id="catch-with-giant-bias")
        )
        assert result.pig.rarity == expected_star
        assert result.pig.template_id == expected_template
        assert result.pig.template_id in visible_ids
        assert not draws.values
        effect = await database.fetch_one(
            "SELECT granted_uses,consumed_uses FROM player_food_effects WHERE source_food_instance_id=?",
            ("giant-food-instance",),
        )
        assert tuple(effect) == (1, 1)
    finally:
        await database.close()
