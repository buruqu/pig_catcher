"""仓库中的 Manifest、TOML 与代码版本保持一致。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from pig_catcher.config import PigCatcherConfig
from pig_catcher.version import FRAMEWORK_PHASE, PLUGIN_VERSION

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_config_parses_with_current_model() -> None:
    with (_PROJECT_ROOT / "config.toml").open("rb") as config_file:
        config = PigCatcherConfig.model_validate(tomllib.load(config_file))
    assert config.plugin.config_version == PLUGIN_VERSION
    assert config.plugin.framework_phase == FRAMEWORK_PHASE
    assert config.features.help_enabled is True
    assert config.trading.gift_enabled is True
    assert config.trading.trade_enabled is True


def test_manifest_versions_dependencies_and_capabilities_are_narrow() -> None:
    manifest = json.loads((_PROJECT_ROOT / "_manifest.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 2
    assert manifest["id"] == "local.pig-catcher"
    assert manifest["version"] == PLUGIN_VERSION
    assert pyproject["project"]["version"] == PLUGIN_VERSION
    assert manifest["host_application"]["min_version"] == "1.0.12"
    assert manifest["sdk"]["min_version"] == "2.7.0"
    assert manifest["capabilities"] == [
        "message.get_by_time_in_chat",
        "render.html2png",
        "send.hybrid",
        "send.image",
        "send.text",
    ]
    assert {dependency["name"] for dependency in manifest["dependencies"]} == {
        "aiosqlite",
        "Jinja2",
        "Pillow",
        "tomlkit",
    }
