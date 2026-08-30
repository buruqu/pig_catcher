"""Internal v2 preview packages are isolated, reproducible, and secret-free."""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from tools.build_internal_preview import (
    INTERNAL_PLUGIN_ID,
    REQUIRED_SCOPE_IDS,
    build_internal_preview,
    verify_internal_preview_package,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
def _minimal_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    for name in (
        "_manifest.json",
        "config.toml",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
        "LICENSE",
    ):
        shutil.copy2(PROJECT_ROOT / name, source / name)
    (source / "plugin.py").write_text(
        "from .pig_catcher.plugin_metadata import PLUGIN_CONFIG_URL\n",
        encoding="utf-8",
    )
    (source / "pig_catcher").mkdir()
    (source / "pig_catcher" / "plugin_metadata.py").write_text(
        "PLUGIN_CONFIG_URL = 'manifest-driven'\n",
        encoding="utf-8",
    )
    (source / "catalogs").mkdir()
    (source / "catalogs" / "catalog.json").write_text("{}\n", encoding="utf-8")
    (source / "asset_library" / "current" / "media").mkdir(parents=True)
    (source / "asset_library" / "current" / "assets.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (source / "asset_library" / "current" / "media" / "sample.png").write_bytes(
        b"preview-media"
    )
    (source / "tools").mkdir()
    (source / "tools" / "import_asset_catalog.py").write_text(
        "# isolated catalog importer\n",
        encoding="utf-8",
    )
    return source


def test_build_internal_preview_rewrites_identity_and_isolates_both_groups(
    tmp_path: Path,
) -> None:
    source = _minimal_source(tmp_path)
    output = tmp_path / "package"
    report = build_internal_preview(
        source_root=source,
        output_root=output,
    )

    manifest = json.loads((output / "_manifest.json").read_text(encoding="utf-8"))
    with (output / "config.toml").open("rb") as config_file:
        config = tomllib.load(config_file)
    assert manifest["id"] == INTERNAL_PLUGIN_ID
    assert manifest["name"] == "抓猪插件 2.0 内部测试"
    assert config["access"]["group_whitelist"] == [
        "1092931381",
        "5E5854406D0297D6FEAE696A13E3A339",
    ]
    assert config["access"]["command_session_allowlist"] == list(REQUIRED_SCOPE_IDS)
    assert config["access"]["notify_denied"] is False
    assert config["regulation"]["enabled_scope_ids"] == []
    assert config["features"]["weekly_competitions_enabled"] is False
    assert config["quota_administration"]["group_id"] == ""
    assert config["announcement_administration"]["execute_send"] is False
    assert not (output / "tests").exists()
    assert not (output / "docs").exists()
    assert report["plugin_id"] == INTERNAL_PLUGIN_ID
    assert report["weekly_competitions_enabled"] is False
    assert report["secret_inputs_copied"] is False
    assert report == json.loads(
        (output / "INTERNAL_PREVIEW_BUILD.json").read_text(encoding="utf-8")
    )
    assert verify_internal_preview_package(output)["files"] == report["files"]


def test_build_internal_preview_refuses_overwrite_and_nested_output(tmp_path: Path) -> None:
    source = _minimal_source(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(FileExistsError):
        build_internal_preview(
            source_root=source,
            output_root=existing,
        )
    with pytest.raises(ValueError, match="互相包含"):
        build_internal_preview(
            source_root=source,
            output_root=source / "nested",
        )


def test_preview_verifier_rejects_data_and_secret_file_types(tmp_path: Path) -> None:
    source = _minimal_source(tmp_path)
    output = tmp_path / "package"
    build_internal_preview(
        source_root=source,
        output_root=output,
    )
    (output / "leaked.sqlite3").write_bytes(b"not-a-real-database")

    with pytest.raises(ValueError, match="禁止的数据或密钥文件"):
        verify_internal_preview_package(output)
