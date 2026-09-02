"""Production packages are complete, four-scope, and secret-free."""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from tools.build_production_release import (
    BUILD_REPORT,
    FORMAL_PLUGIN_ID,
    REQUIRED_SCOPE_IDS,
    build_production_release,
    verify_production_package,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    for name in (
        "_manifest.json", "config.toml", "pyproject.toml", "requirements.txt",
        "uv.lock", "LICENSE", "plugin.py",
    ):
        shutil.copy2(PROJECT_ROOT / name, root / name)
    (root / "pig_catcher").mkdir()
    (root / "pig_catcher" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "catalogs").mkdir()
    (root / "catalogs" / "sample.json").write_text("{}\n", encoding="utf-8")
    (root / "asset_library" / "current" / "media").mkdir(parents=True)
    (root / "asset_library" / "current" / "assets.json").write_text("{}\n", encoding="utf-8")
    (root / "asset_library" / "current" / "media" / "sample.png").write_bytes(b"media")
    (root / "tools").mkdir()
    (root / "tools" / "import_asset_catalog.py").write_text("# importer\n", encoding="utf-8")
    return root


def test_build_production_release_enables_all_scopes_and_campaign(tmp_path: Path) -> None:
    output = tmp_path / "package"
    report = build_production_release(source_root=_source(tmp_path), output_root=output)
    manifest = json.loads((output / "_manifest.json").read_text(encoding="utf-8"))
    with (output / "config.toml").open("rb") as handle:
        config = tomllib.load(handle)
    assert manifest["id"] == FORMAL_PLUGIN_ID
    assert config["access"]["group_whitelist"] == [s.split(":", 1)[1] for s in REQUIRED_SCOPE_IDS]
    assert config["access"]["command_session_allowlist"] == []
    assert config["features"]["weekly_competitions_enabled"] is True
    assert config["launch_campaign"]["enabled"] is True
    assert config["launch_campaign"]["first_day_window_limit"] == 20
    assert config["launch_campaign"]["first_day_high_star_multiplier"] == 2.0
    assert config["launch_campaign"]["starter_code_change_tickets"] == 3
    assert report == json.loads((output / BUILD_REPORT).read_text(encoding="utf-8"))
    assert verify_production_package(output)["files"] == report["files"]


def test_production_verifier_rejects_data_file(tmp_path: Path) -> None:
    output = tmp_path / "package"
    build_production_release(source_root=_source(tmp_path), output_root=output)
    (output / "player.sqlite3").write_bytes(b"forbidden")
    with pytest.raises(ValueError, match="禁止的数据或密钥文件"):
        verify_production_package(output)
