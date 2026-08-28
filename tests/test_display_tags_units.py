"""独立展示标签、Schema 41 兼容迁移和吨/米单位的精度、隐私回归。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from pig_catcher.assets import AssetCatalogStorage
from pig_catcher.assets.models import AssetManifestEntry
from pig_catcher.config.model import CatchingSection
from pig_catcher.domain.dispatch_views import DispatchPigCard, DispatchView
from pig_catcher.domain.display import (
    display_tags_from_json,
    format_length,
    format_measurement,
    format_weight,
    normalize_display_tags,
)
from pig_catcher.domain.errors import MigrationError
from pig_catcher.infrastructure.database import PigCatcherDatabase
from pig_catcher.rendering import PigCatcherRenderer
from pig_catcher.rendering.adapters import catalog_view, inventory_view, pig_card_view
from pig_catcher.rendering.models import CatalogItemViewModel, CatalogViewModel
from pig_catcher.services import AssetCatalogService, GameplayService
from pig_catcher.services.dispatch import DispatchService
from pig_catcher.services.gameplay import format_catalog_summary, format_pig_detail_summary
from pig_catcher.version import SCHEMA_VERSION
from tools.build_asset_package import manifest_entry

from .helpers import FakeRender
from .test_activity_acceptance import legacy_fixture
from .test_gameplay import MutableClock, SequenceRandom, _catch_rolls, _database_with_catalog, _identity, _pig_entry
from .test_rendering import _options


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.35, "0.35 kg"),
        (999.99, "999.99 kg"),
        (1000, "1 t"),
        (1000.01, "1.00001 t"),
        (75770.78, "75.77078 t"),
        (100000, "100 t"),
    ],
)
def test_weight_display_preserves_centikilogram_precision(value, expected):
    assert format_weight(value) == expected
    rendered = format_weight(value)
    amount, unit = rendered.split()
    converted = float(amount) * (1000 if unit == "t" else 1)
    assert converted == pytest.approx(value, abs=0.000001)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(4, "4.0 cm"), (999.9, "999.9 cm"), (1000, "10 m"), (1000.1, "10.001 m"), (2694.7, "26.947 m")],
)
def test_length_display_preserves_millimetres(value, expected):
    assert format_length(value) == expected
    amount, unit = format_length(value).split()
    assert float(amount) * (100 if unit == "m" else 1) == pytest.approx(value, abs=0.000001)


def test_units_detail_base_and_invalid_values():
    assert format_weight(1234.56, include_base=True) == "1.23456 t（1234.56 kg）"
    assert format_length(1234.5, include_base=True) == "12.345 m（1234.5 cm）"
    assert format_measurement(75770.78, "kg") == "75.77078 t"
    assert format_measurement(2694.7, "cm") == "26.947 m"
    with pytest.raises(ValueError):
        format_measurement(1, "mystery")
    for value in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError):
            format_weight(value)
        with pytest.raises(ValueError):
            format_length(value)


def test_display_tag_validation_is_separate_from_recipe_affinity():
    entry = _pig_entry("tag-pig", rarity=4)
    old = AssetManifestEntry.model_validate(entry)
    assert old.display_tags == []
    entry["display_tags"] = [" 效率曲 ", "EXIST", "MyGO!!!!!", "效率曲"]
    current = AssetManifestEntry.model_validate(entry)
    assert current.display_tags == ["效率曲", "EXIST", "MyGO!!!!!"]
    assert current.recipe_tags == old.recipe_tags == ["测试"]
    assert display_tags_from_json(None) == ()
    assert normalize_display_tags(["渚打S", "SOS!", "谱面梗"]) == ("渚打S", "SOS!", "谱面梗")
    packaged = manifest_entry(entry, image_path="pig.png", source_label="fixture", license_label="test-only")
    assert packaged["display_tags"] == entry["display_tags"]
    assert AssetManifestEntry.model_validate(packaged).display_tags == current.display_tags


@pytest.mark.parametrize("tags", [[""], ["a" * 21], ["a\nb"], ["a\x00b"], ["a"] * 6, "效率曲", [123]])
def test_display_tag_rejects_unbounded_or_nontext_values(tags):
    entry = {**_pig_entry("tag-pig", rarity=4), "display_tags": tags}
    with pytest.raises(ValidationError):
        AssetManifestEntry.model_validate(entry)


async def _world(tmp_path):
    entry = _pig_entry("tag-pig", rarity=1)
    entry["display_tags"] = ["效率曲", "EXIST", "谱面梗"]
    db = await _database_with_catalog(tmp_path, [entry])
    service = GameplayService(
        db,
        CatchingSection(cooldown_seconds=0),
        clock=MutableClock(datetime(2026, 8, 28, 1, tzinfo=UTC)),
        random_source=SequenceRandom(*_catch_rolls(), *_catch_rolls()),
    )
    return db, service


async def test_tags_import_receipt_replay_inventory_and_catalog_preserve_instance(tmp_path: Path):
    db, service = await _world(tmp_path)
    try:
        identity = _identity()
        result = await service.catch(identity)
        assert result.pig.display_tags == ("效率曲", "EXIST", "谱面梗")
        assert "标签：效率曲 · EXIST · 谱面梗" in result.receipt.text_summary
        before = dict(await db.fetch_one("SELECT * FROM pig_instances"))
        before_ledger = [tuple(r) for r in await db.fetch_all("SELECT * FROM currency_ledger ORDER BY rowid")]
        manifest = tmp_path / "source/assets.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["entries"][0].update(
            display_tags=["吨级", "地球"],
            length_min_cm=1000,
            length_max_cm=5000,
            weight_min_kg=1000,
            weight_max_kg=100000,
        )
        manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        await AssetCatalogService(
            db,
            AssetCatalogStorage(tmp_path / "data"),
            min_image_side=32,
            max_image_bytes=1024 * 1024,
        ).import_manifest(manifest)
        assert dict(await db.fetch_one("SELECT * FROM pig_instances")) == before
        assert [tuple(r) for r in await db.fetch_all("SELECT * FROM currency_ledger ORDER BY rowid")] == before_ledger
        template = await db.fetch_one("SELECT recipe_tags_json,display_tags_json FROM pig_templates")
        assert json.loads(template["recipe_tags_json"]) == ["测试"]
        assert json.loads(template["display_tags_json"]) == ["吨级", "地球"]
        replay = await service.catch(identity)
        assert not replay.receipt_created and replay.pig.display_tags == result.pig.display_tags
        inventory = await service.inventory(identity, page=1, rarity=None, sort="价值")
        assert inventory.pigs[0].display_tags == ("吨级", "地球")
        assert inventory_view(inventory).items[0].display_tags == ("吨级", "地球")
        detail = pig_card_view(inventory.pigs[0], mode_label="猪猪详情")
        assert detail.display_tags == ("吨级", "地球")
        assert "标签：吨级 · 地球" in format_pig_detail_summary(inventory.pigs[0])
        catalog = await service.catalog(identity, rarity=None, undiscovered_only=False)
        assert catalog.entries[0].display_tags == ("吨级", "地球")
        assert catalog_view(catalog).items[0].display_tags == ("吨级", "地球")
        assert "吨级" in format_catalog_summary(catalog)
        unknown = await service.catalog(_identity(user_id="stranger"), rarity=None, undiscovered_only=False)
        assert unknown.entries[0].display_tags == ()
        assert catalog_view(unknown).items[0].display_tags == ()
        assert "吨级" not in format_catalog_summary(unknown)
        after = await service.catch(_identity(message_id="after-reimport"))
        assert after.pig.size_value >= 1000 and after.pig.weight_value >= 1000
        assert after.pig.official_value == result.pig.official_value  # 相同百分位，原价值公式没有变化。
    finally:
        await db.close()


async def test_schema40_to41_adds_only_empty_display_metadata_and_keeps_rows(tmp_path: Path):
    db, service = await _world(tmp_path)
    try:
        await service.catch(_identity())
        path = tmp_path / "schema40.sqlite3"
        original = await legacy_fixture(db, path, 40)
        with sqlite3.connect(path) as source:
            columns = {
                table: ",".join(f'"{r[1]}"' for r in source.execute(f'PRAGMA table_info("{table}")'))
                for table in original
            }
        migrated = PigCatcherDatabase(path)
        await migrated.open()
        try:
            assert await migrated.schema_version() == SCHEMA_VERSION == 44
            for table, rows in original.items():
                assert [
                    tuple(r) for r in await migrated.fetch_all(f'SELECT {columns[table]} FROM "{table}" ORDER BY rowid')
                ] == rows
            assert [r[0] for r in await migrated.fetch_all("SELECT display_tags_json FROM pig_templates")] == ["[]"]
            assert await migrated.integrity_check() == ("ok",)
            assert await migrated.fetch_all("PRAGMA foreign_key_check") == []
            backup = tmp_path / "backup41.sqlite3"
            await migrated.backup_to(backup)
        finally:
            await migrated.close()
        restored = PigCatcherDatabase(backup)
        await restored.open()
        await restored.close()
        with sqlite3.connect(backup) as damaged:
            damaged.execute("ALTER TABLE pig_templates DROP COLUMN display_tags_json")
        with pytest.raises(MigrationError, match="展示标签"):
            await restored.open()
    finally:
        await db.close()


async def test_tag_html_is_escaped_compact_and_hidden_for_unknown_or_revoked_pigs(tmp_path: Path):
    db, service = await _world(tmp_path)
    try:
        pig = (await service.catch(_identity())).pig
        capability = FakeRender()
        renderer = PigCatcherRenderer(capability, _options())
        tagged = replace(
            pig,
            display_tags=("<b>效率曲</b>", "EXIST", "谱面梗"),
            weight_value=1234.56,
            size_value=1234.5,
        )
        await renderer.render_static_pig_card(pig_card_view(tagged, mode_label="详情"), None)
        html = capability.calls[-1][0]
        assert "&lt;b&gt;效率曲&lt;/b&gt;" in html and "<b>效率曲</b>" not in html
        assert "1.23456 t" in html and "12.345 m" in html
        assert "1234.56 kg" in html and "1234.5 cm" in html
        revoked = replace(tagged, media_visible=False)
        await renderer.render_static_pig_card(pig_card_view(revoked, mode_label="详情"), None)
        assert "效率曲" not in capability.calls[-1][0]
        item = CatalogItemViewModel(
            key="secret",
            display_name="不能泄露的私密猪",
            rarity=6,
            discovered=False,
            acquired_count=0,
            best_size=None,
            best_weight=None,
            collection_name="",
            character_name="",
            media_visible=False,
            is_animated=False,
            image_fit="contain",
            display_tags=("不能泄露的身份标签",),
        )
        catalog = CatalogViewModel("测试", 1, None, False, 0, 1, (item,))
        await renderer.render_catalog(catalog, {})
        assert "不能泄露" not in capability.calls[-1][0]
        visible = replace(
            item,
            display_name="渚打S",
            discovered=True,
            media_visible=True,
            display_tags=("效率曲", "SOS!", "MyGO!!!!!", "谱面梗"),
        )
        await renderer.render_catalog(replace(catalog, items=(visible,)), {})
        html = capability.calls[-1][0]
        assert "效率曲" in html and "SOS!" in html and "+2" in html
        assert "MyGO!!!!!" not in html
    finally:
        await db.close()


async def test_activity_receipt_tags_recheck_revocation_without_affecting_common_card(tmp_path: Path):
    db = await _database_with_catalog(
        tmp_path,
        [
            _pig_entry("public", rarity=1),
            _pig_entry("private", rarity=6, group_id="100"),
        ],
    )
    public = DispatchPigCard("公共猪", "PUBLIC01", 1, "public.png", ("效率曲",), "", template_id="public")
    private = DispatchPigCard("私人猪", "PRIV0001", 6, "private.png", ("私密标签",), "", template_id="private")
    stored_view = DispatchView("历史回执", "测试成员", pigs=(public, private)).payload()
    try:
        async with db.transaction() as session:
            permitted = await DispatchService._restrict_media(
                session, _identity(), DispatchView.from_payload(stored_view)
            )
            assert permitted.pigs == (public, private)
            await session.execute("UPDATE scope_pig_templates SET authorized=0 WHERE template_id='private'")
            revoked = await DispatchService._restrict_media(
                session, _identity(), DispatchView.from_payload(stored_view)
            )
            assert revoked.pigs[0] == public
            assert revoked.pigs[1].image_relpath == "" and revoked.pigs[1].tags == ()
            assert "私密标签" not in revoked.text()
    finally:
        await db.close()
