"""Build a secret-free, group-isolated Pig Catcher 2.0 preview package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tomllib
from pathlib import Path
from typing import Any

import tomlkit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTERNAL_PLUGIN_ID = "local.pig-catcher-v2-internal"
INTERNAL_PLUGIN_NAME = "抓猪插件 2.0 内部测试"
REQUIRED_SCOPE_IDS = (
    "qq:1092931381",
    "qq-official:5E5854406D0297D6FEAE696A13E3A339",
)
ROOT_FILES = (
    "_manifest.json",
    "config.toml",
    "plugin.py",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "LICENSE",
)
OPTIONAL_ROOT_FILES = ("ASSET_NOTICE.md",)
RUNTIME_DIRECTORIES = (
    "pig_catcher",
    "catalogs",
    "asset_library/current",
)
RUNTIME_TOOL_FILES = ("tools/import_asset_catalog.py",)
SKIPPED_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_PACKAGE_SUFFIXES = {
    ".db",
    ".env",
    ".sqlite",
    ".sqlite3",
    ".xls",
    ".xlsx",
}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _copy_file(source: Path, destination: Path) -> None:
    if _is_reparse_point(source):
        raise ValueError(f"构建输入不能包含链接或 Junction：{source}")
    if source.suffix.lower() in SKIPPED_SUFFIXES:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_directory(source: Path, destination: Path) -> None:
    if _is_reparse_point(source):
        raise ValueError(f"构建输入不能包含链接或 Junction：{source}")
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda path: path.name.casefold()):
        if child.name in SKIPPED_NAMES or child.suffix.lower() in SKIPPED_SUFFIXES:
            continue
        target = destination / child.name
        if child.is_dir():
            _copy_directory(child, target)
        elif child.is_file():
            _copy_file(child, target)
        else:
            raise ValueError(f"构建输入含非常规文件：{child}")


def _copy_runtime_source(source_root: Path, output_root: Path) -> None:
    for relative in ROOT_FILES:
        source = source_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"缺少运行文件：{source}")
        _copy_file(source, output_root / relative)
    for relative in OPTIONAL_ROOT_FILES:
        source = source_root / relative
        if source.is_file():
            _copy_file(source, output_root / relative)
    for relative in RUNTIME_DIRECTORIES:
        source = source_root / relative
        if not source.is_dir():
            raise FileNotFoundError(f"缺少运行目录：{source}")
        _copy_directory(source, output_root / relative)
    for relative in RUNTIME_TOOL_FILES:
        source = source_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"缺少部署工具：{source}")
        _copy_file(source, output_root / relative)


def _rewrite_manifest(output_root: Path) -> dict[str, Any]:
    manifest_path = output_root / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["id"] = INTERNAL_PLUGIN_ID
    manifest["name"] = INTERNAL_PLUGIN_NAME
    manifest["description"] = (
        "抓猪 2.0 内部灰度版：仅供指定内部测试群验证新机制，使用独立插件与数据目录。"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _rewrite_config(output_root: Path) -> None:
    config_path = output_root / "config.toml"
    document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    # 内部群在 2026-09-01 正式开榜前暂停周榜；构建产物必须保持关闭，
    # 避免后续灰度覆盖把运行态意外重新打开。
    document["features"]["weekly_competitions_enabled"] = False
    access = document["access"]
    access["group_whitelist"] = [scope.split(":", 1)[1] for scope in REQUIRED_SCOPE_IDS]
    access["group_blacklist"] = []
    access["user_whitelist"] = []
    access["user_blacklist"] = []
    access["command_session_allowlist"] = list(REQUIRED_SCOPE_IDS)
    access["notify_denied"] = False

    regulation = document["regulation"]
    regulation["enabled_scope_ids"] = []

    quota = document["quota_administration"]
    quota["group_id"] = ""
    quota["platform"] = ""
    quota["execute_current_window_reset"] = False
    quota["boost_window_limit"] = 0

    blacklist = document["blacklist_administration"]
    blacklist["group_id"] = ""
    blacklist["platform"] = ""
    blacklist["user_ids"] = []
    blacklist["gift_action"] = "不操作"
    blacklist["trade_action"] = "不操作"
    blacklist["execute_blacklist_update"] = False

    announcement = document["announcement_administration"]
    announcement["group_id"] = ""
    announcement["platform"] = ""
    announcement["content"] = ""
    announcement["execute_send"] = False

    config_path.write_text(tomlkit.dumps(document), encoding="utf-8")


def _file_inventory(root: Path) -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    ):
        if path.name == "INTERNAL_PREVIEW_BUILD.json":
            continue
        payload = path.read_bytes()
        inventory.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return inventory


def verify_internal_preview_package(output_root: Path) -> dict[str, object]:
    """Verify identity, isolation, inventory, and secret-file boundaries."""

    root = output_root.resolve(strict=True)
    manifest = json.loads((root / "_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("id") != INTERNAL_PLUGIN_ID:
        raise ValueError("内部灰度包 Manifest ID 不正确。")

    with (root / "config.toml").open("rb") as config_file:
        config = tomllib.load(config_file)
    expected_group_ids = [scope.split(":", 1)[1] for scope in REQUIRED_SCOPE_IDS]
    access = config.get("access", {})
    if access.get("group_whitelist") != expected_group_ids:
        raise ValueError("内部灰度包群白名单不正确。")
    routing_allowlist = access.get("command_session_allowlist")
    if routing_allowlist != list(REQUIRED_SCOPE_IDS):
        raise ValueError("内部灰度包命令路由白名单不正确。")
    if config.get("regulation", {}).get("enabled_scope_ids") != []:
        raise ValueError("内部灰度包不应自动启用监管作用域。")
    if config.get("features", {}).get("weekly_competitions_enabled") is not False:
        raise ValueError("内部灰度包在 2026-09-01 前必须暂停周榜。")

    for path in root.rglob("*"):
        if _is_reparse_point(path):
            raise ValueError(f"内部灰度包不能包含链接或 Junction：{path}")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_PACKAGE_SUFFIXES:
            raise ValueError(f"内部灰度包包含禁止的数据或密钥文件：{path}")

    plugin_source = (root / "plugin.py").read_text(encoding="utf-8")
    if "/plugin-config?plugin=local.pig-catcher" in plugin_source:
        raise ValueError("内部灰度包仍含正式插件 ID 的硬编码 WebUI 链接。")
    if "PLUGIN_CONFIG_URL" not in plugin_source:
        raise ValueError("内部灰度包没有使用 Manifest 驱动的 WebUI 链接。")

    inventory = _file_inventory(root)
    return {
        "plugin_id": INTERNAL_PLUGIN_ID,
        "plugin_version": str(manifest.get("version") or ""),
        "group_scope_ids": list(REQUIRED_SCOPE_IDS),
        "group_ids": expected_group_ids,
        "command_routing_allowlist": list(REQUIRED_SCOPE_IDS),
        "weekly_competitions_enabled": False,
        "file_count": len(inventory),
        "payload_bytes": sum(int(item["bytes"]) for item in inventory),
        "files": inventory,
        "secret_inputs_copied": False,
    }


def build_internal_preview(
    *,
    source_root: Path,
    output_root: Path,
) -> dict[str, object]:
    """Build one immutable directory package and return its verification report."""

    source = source_root.resolve(strict=True)
    output = output_root.resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    if _is_relative_to(output, source) or _is_relative_to(source, output):
        raise ValueError("构建输出不能与源码目录互相包含。")
    output.mkdir(parents=True, exist_ok=False)
    try:
        _copy_runtime_source(source, output)
        _rewrite_manifest(output)
        _rewrite_config(output)
        report = verify_internal_preview_package(output)
        (output / "INTERNAL_PREVIEW_BUILD.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="构建仅供两个指定群使用的抓猪 2.0 独立内部灰度包。"
    )
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_internal_preview(
        source_root=args.source,
        output_root=args.output,
    )
    summary = {key: value for key, value in report.items() if key != "files"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
