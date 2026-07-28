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
    }


def _food_entry(template_id: str, image: str) -> dict[str, object]:
    return {
        "template_id": template_id,
        "kind": "food",
        "display_name": f"测试菜{template_id}",
        "rarity": 2,
        "scope": "common",
        "description": "测试美食素材",
        "image": image,
        "fit": "contain",
        "source": "pytest synthetic asset",
        "license": "test-only",
        "consent_status": "not-required",
        "recipe_tags": ["家常"],
        "effect_id": "test-effect",
    }


def _write_png(path: Path, color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (64, 64), color).save(path, format="PNG")


def _write_manifest(
    root: Path,
    entries: list[dict[str, object]],
    *,
    catalog_id: str = "test-catalog",
) -> Path:
    path = root / "assets.json"
    path.write_text(
        json.dumps(
            {
                "manifest_version": 1,
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


def test_manifest_validator_accepts_png_and_is_deterministic(tmp_path: Path) -> None:
    _write_png(tmp_path / "pig.png", (255, 180, 205, 255))
    manifest = _write_manifest(tmp_path, [_pig_entry("common-pig", "pig.png")])
    validator = AssetManifestValidator(min_image_side=32, max_image_bytes=1024 * 1024)
    first = validator.validate_file(manifest)
    second = validator.validate_file(manifest)
    assert first.catalog_hash == second.catalog_hash
    assert first.assets[0].width == 64
    assert first.assets[0].image_format == "PNG"


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
