"""Build and verify the secret-free Pig Catcher 2.0 production package."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path

import tomlkit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.build_internal_preview import (  # noqa: E402
    FORBIDDEN_PACKAGE_SUFFIXES,
    _copy_runtime_source,
    _file_inventory,
    _is_relative_to,
    _is_reparse_point,
)

FORMAL_PLUGIN_ID = "local.pig-catcher"
REQUIRED_SCOPE_IDS = (
    "qq:1092931381",
    "qq-official:5E5854406D0297D6FEAE696A13E3A339",
    "qq:237716658",
    "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
)
BUILD_REPORT = "PRODUCTION_RELEASE_BUILD.json"


def _rewrite_config(output_root: Path) -> None:
    path = output_root / "config.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["features"]["weekly_competitions_enabled"] = True
    access = document["access"]
    access["group_whitelist"] = [scope.split(":", 1)[1] for scope in REQUIRED_SCOPE_IDS]
    access["group_blacklist"] = []
    access["user_whitelist"] = []
    access["user_blacklist"] = []
    # An empty routing allowlist means all four explicitly whitelisted scopes.
    access["command_session_allowlist"] = []
    access["notify_denied"] = True

    campaign = document["launch_campaign"]
    campaign["enabled"] = True
    campaign["campaign_id"] = "pig-dream-2.0-launch"
    campaign["starts_at"] = "2026-09-01T00:00:00+08:00"
    campaign["first_day_ends_at"] = "2026-09-02T00:00:00+08:00"
    campaign["first_day_window_limit"] = 20
    campaign["first_day_high_star_multiplier"] = 2.0

    # Release artifacts never carry one-shot administration switches.
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
    announcement["image_path"] = ""
    announcement["execute_send"] = False
    path.write_text(tomlkit.dumps(document), encoding="utf-8")


def verify_production_package(output_root: Path) -> dict[str, object]:
    root = output_root.resolve(strict=True)
    manifest = json.loads((root / "_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("id") != FORMAL_PLUGIN_ID:
        raise ValueError("正式发布包 Manifest ID 不正确。")
    with (root / "config.toml").open("rb") as handle:
        config = tomllib.load(handle)
    access = config.get("access", {})
    expected_groups = [scope.split(":", 1)[1] for scope in REQUIRED_SCOPE_IDS]
    if access.get("group_whitelist") != expected_groups:
        raise ValueError("正式发布包没有精确包含四个既有群范围。")
    if access.get("command_session_allowlist") != []:
        raise ValueError("正式发布包命令路由仍被内部灰度范围限制。")
    if config.get("features", {}).get("weekly_competitions_enabled") is not True:
        raise ValueError("正式发布包没有开启首期周榜。")
    campaign = config.get("launch_campaign", {})
    if campaign.get("enabled") is not True or campaign.get("first_day_window_limit") != 20:
        raise ValueError("正式发布包首日活动配置不完整。")
    if float(campaign.get("first_day_high_star_multiplier", 0)) != 2.0:
        raise ValueError("正式发布包首日高星权重配置不正确。")
    if int(campaign.get("starter_code_change_tickets", 0)) != 3:
        raise ValueError("正式发布包缺少编号修改券 ×3 开服福利。")
    for section, key in (
        ("quota_administration", "execute_current_window_reset"),
        ("blacklist_administration", "execute_blacklist_update"),
        ("announcement_administration", "execute_send"),
    ):
        if config.get(section, {}).get(key) is not False:
            raise ValueError(f"正式发布包残留一次性管理开关：{section}.{key}")
    for path in root.rglob("*"):
        if _is_reparse_point(path):
            raise ValueError(f"正式发布包不能包含链接或 Junction：{path}")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_PACKAGE_SUFFIXES:
            raise ValueError(f"正式发布包包含禁止的数据或密钥文件：{path}")
        if path.is_file() and path.name.casefold() in {".env", "secrets.toml", "credentials.json"}:
            raise ValueError(f"正式发布包包含疑似密钥文件：{path}")
    inventory = [
        item for item in _file_inventory(root) if item["path"] != BUILD_REPORT
    ]
    return {
        "plugin_id": FORMAL_PLUGIN_ID,
        "plugin_version": str(manifest.get("version") or ""),
        "group_scope_ids": list(REQUIRED_SCOPE_IDS),
        "weekly_competitions_enabled": True,
        "launch_campaign_id": str(campaign.get("campaign_id") or ""),
        "file_count": len(inventory),
        "payload_bytes": sum(int(item["bytes"]) for item in inventory),
        "inventory_sha256": hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "files": inventory,
        "secret_inputs_copied": False,
    }


def build_production_release(*, source_root: Path, output_root: Path) -> dict[str, object]:
    source = source_root.resolve(strict=True)
    output = output_root.resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output}")
    if _is_relative_to(output, source) or _is_relative_to(source, output):
        raise ValueError("构建输出不能与源码目录互相包含。")
    output.mkdir(parents=True, exist_ok=False)
    try:
        _copy_runtime_source(source, output)
        _rewrite_config(output)
        report = verify_production_package(output)
        (output / BUILD_REPORT).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report
    except BaseException:
        import shutil

        shutil.rmtree(output, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建抓猪 2.0 正式无密钥发布包。")
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_production_release(source_root=args.source, output_root=args.output)
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
