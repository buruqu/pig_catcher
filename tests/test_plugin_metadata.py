"""Deployment-specific plugin metadata stays manifest-driven."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pig_catcher.plugin_metadata import manifest_plugin_id, plugin_config_url


def test_manifest_plugin_id_and_webui_url_follow_packaged_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "_manifest.json"
    manifest_path.write_text(
        json.dumps({"id": "local.pig-catcher-v2-internal"}),
        encoding="utf-8",
    )

    plugin_id = manifest_plugin_id(manifest_path)

    assert plugin_id == "local.pig-catcher-v2-internal"
    assert plugin_config_url(plugin_id) == (
        "/plugin-config?plugin=local.pig-catcher-v2-internal"
    )


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"id": ""},
        {"id": "local.pig catcher"},
        {"id": "local.pig-catcher\ninternal"},
    ),
)
def test_manifest_plugin_id_rejects_missing_or_unsafe_values(
    tmp_path: Path,
    payload: dict[str, str],
) -> None:
    manifest_path = tmp_path / "_manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError):
        manifest_plugin_id(manifest_path)
