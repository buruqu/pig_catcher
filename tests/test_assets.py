"""素材协议、图片安全、原子导入和六星群隔离。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from pig_catcher.assets import AssetManifestValidator
from pig_catcher.assets.storage import AssetCatalogStorage
from pig_catcher.domain.enums import AssetKind
from pig_catcher.domain.errors import AssetImportError, AssetValidationError
from pig_catcher.domain.models import CommandIdentity, ScopeKey
from pig_catcher.infrastructure import PigCatcherDatabase
from pig_catcher.services import AssetCatalogService, FrameworkService


def _pig_entry(
    template_id: str,
    image: str,
    *,
    rarity: int = 1,
    scope: str = "common",
    group_scope_id: str | None = None,
    consent_status: str = "not-required",
    description: str = "测试猪素材",
    paired_food_template_id: str = "",
) -> dict[str, object]:
    return {
        "template_id": template_id,
        "kind": "pig",
        "display_name": f"测试猪{template_id}",
        "rarity": rarity,
        "scope": scope,
        "group_scope_id": group_scope_id,
        "description": description,
        "image": image,
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": consent_status,
        "length_min_cm": 30,
        "length_max_cm": 60,
        "weight_min_kg": 20,
        "weight_max_kg": 100,
        "fat_profile": "balanced",
        "recipe_tags": ["家常"],
        "paired_food_template_id": paired_food_template_id,
    }


def _food_entry(
    template_id: str,
    image: str,
    *,
    rarity: int = 2,
    group_scope_id: str | None = None,
) -> dict[str, object]:
    group_only = group_scope_id is not None
    return {
        "template_id": template_id,
        "kind": "food",
        "display_name": f"测试菜{template_id}",
        "rarity": rarity,
        "scope": "group" if group_only else "common",
        "group_scope_id": group_scope_id,
        "description": "测试美食素材",
        "image": image,
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": "granted" if group_only else "not-required",
        "recipe_tags": ["家常"],
        "effect_id": "test-effect",
    }


def test_manifest_v4_requires_one_to_one_same_group_six_star_pairs(
    tmp_path: Path,
) -> None:
    for filename, color in (
        ("pig-a.png", (255, 180, 205, 255)),
        ("pig-b.png", (240, 170, 210, 255)),
        ("food-a.png", (255, 220, 150, 255)),
        ("food-b.png", (245, 210, 160, 255)),
    ):
        _write_png(tmp_path / filename, color)
    entries = [
        _pig_entry(
            "pig-group-a",
            "pig-a.png",
            rarity=6,
            scope="group",
            group_scope_id="qq:100",
            consent_status="granted",
            paired_food_template_id="food-group-a",
        ),
        _pig_entry(
            "pig-group-b",
            "pig-b.png",
            rarity=6,
            scope="group",
            group_scope_id="qq:200",
            consent_status="granted",
            paired_food_template_id="food-group-b",
        ),
        _food_entry(
            "food-group-a",
            "food-a.png",
            rarity=6,
            group_scope_id="qq:100",
        ),
        _food_entry(
            "food-group-b",
            "food-b.png",
            rarity=6,
            group_scope_id="qq:200",
        ),
    ]
    manifest = _write_manifest(tmp_path, entries, manifest_version=4)
    validated = AssetManifestValidator(
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    ).validate_file(manifest)
    assert len(validated.assets) == 4

    entries[0]["paired_food_template_id"] = "food-group-b"
    _write_manifest(tmp_path, entries, manifest_version=4)
    with pytest.raises(AssetValidationError, match="其他群"):
        AssetManifestValidator(
            min_image_side=32,
            max_image_bytes=1024 * 1024,
        ).validate_file(manifest)


def _write_png(path: Path, color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (64, 64), color).save(path, format="PNG")


def _write_manifest(
    root: Path,
    entries: list[dict[str, object]],
    *,
    catalog_id: str = "test-catalog",
    manifest_version: int = 1,
) -> Path:
    path = root / "assets.json"
    path.write_text(
        json.dumps(
            {
                "manifest_version": manifest_version,
                "catalog_id": catalog_id,
                "source_label": "pytest synthetic catalog",
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _write_gif(
    path: Path,
    *,
    durations: list[int] | None = None,
    loop: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [
        Image.new("RGBA", (64, 64), color)
        for color in ("#F58CAD", "#66BFA3", "#5B8FD1")
    ]
    options: dict[str, object] = {
        "format": "GIF",
        "save_all": True,
        "append_images": frames[1:],
        "loop": loop,
        "disposal": 2,
    }
    if durations is not None:
        options["duration"] = durations
    frames[0].save(path, **options)


def test_manifest_validator_accepts_png_and_is_deterministic(tmp_path: Path) -> None:
    _write_png(tmp_path / "pig.png", (255, 180, 205, 255))
    manifest = _write_manifest(tmp_path, [_pig_entry("common-pig", "pig.png")])
    validator = AssetManifestValidator(min_image_side=32, max_image_bytes=1024 * 1024)
    first = validator.validate_file(manifest)
    second = validator.validate_file(manifest)
    assert first.catalog_hash == second.catalog_hash
    assert first.assets[0].width == 64
    assert first.assets[0].image_format == "PNG"


def test_alternate_image_is_fully_validated_and_hashed(tmp_path: Path) -> None:
    _write_png(tmp_path / "pig.png", (255, 180, 205, 255))
    _write_png(tmp_path / "sticker.png", (100, 210, 190, 255))
    entry = _pig_entry("alternate-pig", "pig.png")
    entry["alternate_image"] = "sticker.png"
    manifest = _write_manifest(tmp_path, [entry])
    validator = AssetManifestValidator(min_image_side=32, max_image_bytes=1024 * 1024)
    first = validator.validate_file(manifest)
    asset = first.assets[0]
    assert asset.alternate_source_path == (tmp_path / "sticker.png").resolve()
    assert len(asset.alternate_sha256) == 64

    Image.new("RGBA", (64, 64), "#AA3366").save(tmp_path / "sticker.png", format="PNG")
    second = validator.validate_file(manifest)
    assert second.assets[0].alternate_sha256 != asset.alternate_sha256
    assert second.catalog_hash != first.catalog_hash


def test_missing_or_corrupt_alternate_image_is_rejected_before_storage(
    tmp_path: Path,
) -> None:
    _write_png(tmp_path / "pig.png", (255, 180, 205, 255))
    entry = _pig_entry("alternate-pig", "pig.png")
    entry["alternate_image"] = "missing.png"
    manifest = _write_manifest(tmp_path, [entry])
    validator = AssetManifestValidator(min_image_side=32, max_image_bytes=1024 * 1024)
    with pytest.raises(AssetValidationError):
        validator.validate_file(manifest)

    (tmp_path / "missing.png").write_bytes(b"not an image")
    with pytest.raises(AssetValidationError, match="无法解码"):
        validator.validate_file(manifest)


def test_manifest_detects_animated_content_even_when_extension_is_jpg(
    tmp_path: Path,
) -> None:
    _write_gif(tmp_path / "animated.jpg", durations=[40, 70, 90], loop=3)
    manifest = _write_manifest(
        tmp_path,
        [_pig_entry("animated-pig", "animated.jpg")],
        manifest_version=2,
    )
    asset = AssetManifestValidator(
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    ).validate_file(manifest).assets[0]
    assert asset.image_format == "GIF"
    assert asset.is_animated is True
    assert asset.frame_count == 3
    assert asset.frame_durations_ms == (40, 70, 90)
    assert asset.total_duration_ms == 200
    assert asset.loop_count == 3


@pytest.mark.parametrize(
    ("entries", "error_fragment"),
    [
        (
            [
                _pig_entry("same-id", "pig.png"),
                _pig_entry("same-id", "pig.png"),
            ],
            "重复",
        ),
        (
            [_pig_entry("public-six", "pig.png", rarity=6)],
            "六星素材不能声明为公共素材",
        ),
        (
            [
                _pig_entry(
                    "group-five",
                    "pig.png",
                    rarity=5,
                    scope="group",
                    group_scope_id="qq:100",
                    consent_status="granted",
                )
            ],
            "群专属素材只允许六星",
        ),
    ],
)
def test_manifest_rejects_invalid_domain_entries(
    tmp_path: Path,
    entries: list[dict[str, object]],
    error_fragment: str,
) -> None:
    _write_png(tmp_path / "pig.png", (255, 180, 205, 255))
    manifest = _write_manifest(tmp_path, entries)
    validator = AssetManifestValidator(min_image_side=32, max_image_bytes=1024 * 1024)
    with pytest.raises(AssetValidationError, match=error_fragment):
        validator.validate_file(manifest)


def test_manifest_rejects_corrupt_image(tmp_path: Path) -> None:
    (tmp_path / "pig.png").write_bytes(b"not-a-png")
    manifest = _write_manifest(tmp_path, [_pig_entry("broken-pig", "pig.png")])
    validator = AssetManifestValidator(min_image_side=32, max_image_bytes=1024 * 1024)
    with pytest.raises(AssetValidationError, match="无法解码"):
        validator.validate_file(manifest)


def test_manifest_model_rejects_path_traversal(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, [_pig_entry("escape-pig", "../pig.png")])
    validator = AssetManifestValidator(min_image_side=32, max_image_bytes=1024 * 1024)
    with pytest.raises(AssetValidationError, match="相对路径"):
        validator.validate_file(manifest)


def test_manifest_rejects_duplicate_collection_slots(tmp_path: Path) -> None:
    _write_png(tmp_path / "one.png", (255, 180, 205, 255))
    _write_png(tmp_path / "two.png", (220, 150, 240, 255))
    collection = {
        "collaboration_name": "BanG Dream!",
        "collection_id": "bandori-test",
        "collection_name": "测试乐队",
        "slot": 1,
        "total": 5,
        "character_id": "member-one",
        "character_name": "成员一",
        "official_profile_url": "https://bang-dream.com/member-one/",
    }
    entries = [
        {**_pig_entry("member-one", "one.png"), "collection": collection},
        {
            **_pig_entry("member-two", "two.png"),
            "collection": {
                **collection,
                "character_id": "member-two",
                "character_name": "成员二",
            },
        },
    ]
    manifest = _write_manifest(tmp_path, entries, manifest_version=2)
    with pytest.raises(AssetValidationError, match="重复槽位"):
        AssetManifestValidator(
            min_image_side=32,
            max_image_bytes=1024 * 1024,
        ).validate_file(manifest)


@pytest.mark.asyncio
async def test_import_is_idempotent_and_six_star_is_group_isolated(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_png(source / "common-pig.png", (255, 180, 205, 255))
    _write_png(source / "group-pig.png", (220, 150, 240, 255))
    _write_png(source / "food.png", (255, 220, 150, 255))
    manifest = _write_manifest(
        source,
        [
            _pig_entry("common-pig", "common-pig.png"),
            _pig_entry(
                "group-pig",
                "group-pig.png",
                rarity=6,
                scope="group",
                group_scope_id="qq:100",
                consent_status="granted",
            ),
            _food_entry("common-food", "food.png"),
        ],
    )
    data_dir = tmp_path / "data"
    database = PigCatcherDatabase(data_dir / "pig.sqlite3")
    await database.open()
    service = AssetCatalogService(
        database,
        AssetCatalogStorage(data_dir),
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    )
    await FrameworkService(database).touch_identity(
        CommandIdentity(
            scope=ScopeKey("qq", "100"),
            stream_id="stream-100",
            user_id="user-1",
            display_name="成员",
            group_name="真实群名",
        )
    )
    first = await service.import_manifest(manifest)
    second = await service.import_manifest(manifest)
    assert first.catalog_hash == second.catalog_hash
    assert await service.list_drawable_template_ids(
        kind=AssetKind.PIG,
        scope_id="qq:100",
    ) == ["common-pig", "group-pig"]
    assert await service.list_drawable_template_ids(
        kind=AssetKind.PIG,
        scope_id="qq:999",
    ) == ["common-pig"]
    assert await service.list_drawable_template_ids(
        kind=AssetKind.FOOD,
        scope_id="qq:100",
    ) == ["common-food"]
    row = await database.fetch_one("SELECT template_version FROM pig_templates WHERE template_id = 'common-pig'")
    assert row is not None
    assert row["template_version"] == 1
    scope_row = await database.fetch_one("SELECT group_name, stream_id FROM scopes WHERE scope_id = 'qq:100'")
    assert scope_row is not None
    assert tuple(scope_row) == ("真实群名", "stream-100")
    await database.close()


@pytest.mark.asyncio
async def test_reimport_can_revoke_group_asset(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_png(source / "group.png", (220, 150, 240, 255))
    granted = _pig_entry(
        "group-pig",
        "group.png",
        rarity=6,
        scope="group",
        group_scope_id="qq:100",
        consent_status="granted",
    )
    manifest = _write_manifest(source, [granted])
    data_dir = tmp_path / "data"
    database = PigCatcherDatabase(data_dir / "pig.sqlite3")
    await database.open()
    service = AssetCatalogService(
        database,
        AssetCatalogStorage(data_dir),
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    )
    await service.import_manifest(manifest)
    assert await service.list_drawable_template_ids(
        kind=AssetKind.PIG,
        scope_id="qq:100",
    ) == ["group-pig"]

    revoked = {**granted, "consent_status": "revoked", "description": "素材授权已撤回"}
    _write_manifest(source, [revoked])
    await service.import_manifest(manifest)
    assert (
        await service.list_drawable_template_ids(
            kind=AssetKind.PIG,
            scope_id="qq:100",
        )
        == []
    )
    row = await database.fetch_one(
        "SELECT template_version, enabled FROM pig_templates WHERE template_id = 'group-pig'"
    )
    assert row is not None
    assert tuple(row) == (2, 0)
    await database.close()


@pytest.mark.asyncio
async def test_group_template_cannot_move_to_another_group(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_png(source / "group.png", (220, 150, 240, 255))
    entry = _pig_entry(
        "stable-group-pig",
        "group.png",
        rarity=6,
        scope="group",
        group_scope_id="qq:100",
        consent_status="granted",
    )
    manifest = _write_manifest(source, [entry])
    data_dir = tmp_path / "data"
    database = PigCatcherDatabase(data_dir / "pig.sqlite3")
    await database.open()
    service = AssetCatalogService(
        database,
        AssetCatalogStorage(data_dir),
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    )
    await service.import_manifest(manifest)

    moved = {
        **entry,
        "group_scope_id": "qq:200",
        "description": "尝试迁移群范围",
    }
    _write_manifest(source, [moved])
    with pytest.raises(AssetImportError, match="不能迁移所属群"):
        await service.import_manifest(manifest)
    assert await service.list_drawable_template_ids(
        kind=AssetKind.PIG,
        scope_id="qq:100",
    ) == ["stable-group-pig"]
    assert (
        await service.list_drawable_template_ids(
            kind=AssetKind.PIG,
            scope_id="qq:200",
        )
        == []
    )
    await database.close()


@pytest.mark.asyncio
async def test_group_template_can_keep_additional_authorized_scope_on_reimport(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_png(source / "group.png", (220, 150, 240, 255))
    entry = _pig_entry(
        "shared-group-pig",
        "group.png",
        rarity=6,
        scope="group",
        group_scope_id="qq:100",
        consent_status="granted",
    )
    manifest = _write_manifest(source, [entry])
    data_dir = tmp_path / "data"
    database = PigCatcherDatabase(data_dir / "pig.sqlite3")
    await database.open()
    service = AssetCatalogService(
        database,
        AssetCatalogStorage(data_dir),
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    )
    await service.import_manifest(manifest)

    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO scopes(
                scope_id, platform, group_id, group_name, stream_id,
                enabled, created_at, updated_at
            )
            VALUES (
                'qq:200', 'qq', '200', '', '', 1,
                '2026-07-28T00:00:00.000Z',
                '2026-07-28T00:00:00.000Z'
            )
            """
        )
        await session.execute(
            """
            INSERT INTO scope_pig_templates(
                scope_id, template_id, authorized, consent_status,
                created_at, updated_at
            )
            VALUES (
                'qq:200', 'shared-group-pig', 1, 'granted',
                '2026-07-28T00:00:00.000Z',
                '2026-07-28T00:00:00.000Z'
            )
            """
        )

    updated = {**entry, "description": "Updated without revoking the shared scope."}
    _write_manifest(source, [updated])
    await service.import_manifest(manifest)
    assert await service.list_drawable_template_ids(
        kind=AssetKind.PIG,
        scope_id="qq:100",
    ) == ["shared-group-pig"]
    assert await service.list_drawable_template_ids(
        kind=AssetKind.PIG,
        scope_id="qq:200",
    ) == ["shared-group-pig"]
    await database.close()


@pytest.mark.asyncio
async def test_band_collection_progress_uses_fixed_five_member_denominator(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_png(source / "kasumi.png", (255, 180, 205, 255))
    _write_png(source / "rimi.png", (220, 150, 240, 255))
    base_collection = {
        "collaboration_name": "BanG Dream!",
        "collection_id": "bandori-poppin-party",
        "collection_name": "Poppin'Party",
        "total": 5,
        "official_profile_url": "https://bang-dream.com/artist/poppinparty/",
    }
    manifest = _write_manifest(
        source,
        [
            {
                **_pig_entry("kasumi-star", "kasumi.png", rarity=5),
                "collection": {
                    **base_collection,
                    "slot": 1,
                    "character_id": "toyama-kasumi",
                    "character_name": "户山香澄",
                },
            },
            {
                **_pig_entry("rimi-chocolate", "rimi.png", rarity=5),
                "collection": {
                    **base_collection,
                    "slot": 3,
                    "character_id": "ushigome-rimi",
                    "character_name": "牛込里美",
                },
            },
        ],
        manifest_version=2,
    )
    data_dir = tmp_path / "data"
    database = PigCatcherDatabase(data_dir / "pig.sqlite3")
    await database.open()
    service = AssetCatalogService(
        database,
        AssetCatalogStorage(data_dir),
        min_image_side=32,
        max_image_bytes=1024 * 1024,
    )
    await service.import_manifest(manifest)
    identity = CommandIdentity(
        scope=ScopeKey("qq", "100"),
        stream_id="stream-100",
        user_id="user-1",
        display_name="成员",
    )
    await FrameworkService(database).touch_identity(identity)
    async with database.transaction() as session:
        await session.execute(
            """
            INSERT INTO pig_catalog_entries(
                player_id, template_id, first_acquired_at, last_acquired_at,
                acquired_count, best_size, best_weight
            )
            VALUES (?, 'kasumi-star', 'now', 'now', 1, 50, 80)
            """,
            (identity.player_id,),
        )
    progress = await service.list_collection_progress(player_id=identity.player_id)
    assert len(progress) == 1
    assert progress[0].collection_name == "Poppin'Party"
    assert progress[0].display_progress == "1/5"
    assert progress[0].available_count == 2
    row = await database.fetch_one(
        """
        SELECT character_name, media_format, frame_count
        FROM pig_templates
        WHERE template_id = 'kasumi-star'
        """
    )
    assert row is not None
    assert tuple(row) == ("户山香澄", "PNG", 1)
    await database.close()
