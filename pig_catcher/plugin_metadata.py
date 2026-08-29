"""Read deployment-sensitive metadata from the package manifest."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote


def manifest_plugin_id(manifest_path: Path | None = None) -> str:
    """Return the validated plugin ID declared by the installed package."""

    path = manifest_path or Path(__file__).resolve().parents[1] / "_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    plugin_id = str(payload.get("id") or "").strip()
    if not plugin_id or len(plugin_id) > 160:
        raise RuntimeError("插件 Manifest 未声明合法的插件 ID。")
    if any(character.isspace() or ord(character) < 32 for character in plugin_id):
        raise RuntimeError("插件 Manifest 的插件 ID 含非法字符。")
    return plugin_id


def plugin_config_url(plugin_id: str) -> str:
    """Build the WebUI configuration route without a deployment-specific literal."""

    normalized = str(plugin_id or "").strip()
    if not normalized:
        raise ValueError("插件 ID 不能为空。")
    return f"/plugin-config?plugin={quote(normalized, safe='._-')}"


PLUGIN_CONFIG_URL = plugin_config_url(manifest_plugin_id())
