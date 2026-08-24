"""Preview or remove old, regenerable files under the ignored artifacts root."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_ROOT = REPO_ROOT / "artifacts"
_REPARSE_POINT_ATTRIBUTE = 0x400


@dataclass(frozen=True, slots=True)
class Candidate:
    path: str
    bytes: int
    newest_mtime: float


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(
            path.stat(follow_symlinks=False),
            "st_file_attributes",
            0,
        )
        return bool(attributes & _REPARSE_POINT_ATTRIBUTE)
    except FileNotFoundError:
        return False


def _tree_stats(path: Path) -> tuple[int, float]:
    if path.is_symlink() or _is_reparse_point(path):
        raise RuntimeError(f"拒绝扫描重解析点：{path}")
    if path.is_file():
        stat = path.stat()
        return int(stat.st_size), float(stat.st_mtime)
    total = 0
    newest = float(path.stat().st_mtime)
    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in directories:
            child = current_path / name
            if child.is_symlink() or _is_reparse_point(child):
                continue
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in files:
            child = current_path / name
            if child.is_symlink() or _is_reparse_point(child):
                continue
            stat = child.stat()
            total += int(stat.st_size)
            newest = max(newest, float(stat.st_mtime))
    return total, newest


def _validated_root(*, allow_root_reparse_point: bool) -> tuple[Path, bool]:
    requested = ARTIFACTS_ROOT.absolute()
    if not requested.is_dir():
        raise RuntimeError(f"本地验收目录不存在：{requested}")
    root_is_reparse_point = requested.is_symlink() or _is_reparse_point(requested)
    if root_is_reparse_point and not allow_root_reparse_point:
        raise RuntimeError(
            "artifacts 根目录是符号链接或 Junction；若确认其目标就是专用验收目录，"
            "请显式追加 --allow-root-reparse-point"
        )
    resolved = requested.resolve()
    if resolved == REPO_ROOT.resolve() or resolved == Path(resolved.anchor):
        raise RuntimeError(f"拒绝把仓库或磁盘根目录作为清理目标：{resolved}")
    return resolved, root_is_reparse_point


def _candidates(root: Path, *, older_than_days: int) -> list[Candidate]:
    cutoff = time.time() - int(older_than_days) * 86400
    result: list[Candidate] = []
    for child in root.iterdir():
        resolved = child.resolve()
        if resolved.parent != root or child.is_symlink() or _is_reparse_point(child):
            raise RuntimeError(f"拒绝处理 artifacts 根目录之外或重解析点目标：{child}")
        size, newest = _tree_stats(child)
        if newest < cutoff:
            result.append(Candidate(str(resolved), size, newest))
    return sorted(result, key=lambda item: item.newest_mtime)


def _remove(candidate: Candidate, root: Path) -> None:
    path = Path(candidate.path)
    resolved = path.resolve()
    if resolved.parent != root or path.is_symlink() or _is_reparse_point(path):
        raise RuntimeError(f"拒绝删除 artifacts 根目录之外或重解析点目标：{resolved}")
    if path.is_dir():
        _detach_reparse_points(path)
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()


def _detach_reparse_points(path: Path) -> None:
    """先只移除目录链接本身，防止递归删除跟随到外部素材或数据库。"""

    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        retained: list[str] = []
        for name in directories:
            child = current_path / name
            if child.is_symlink():
                child.unlink()
            elif _is_reparse_point(child):
                os.rmdir(child)
            else:
                retained.append(name)
        directories[:] = retained
        for name in files:
            child = current_path / name
            if child.is_symlink() or _is_reparse_point(child):
                child.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--older-than-days", type=int, default=7)
    parser.add_argument("--apply", action="store_true", help="实际删除；省略时只预览")
    parser.add_argument(
        "--allow-root-reparse-point",
        action="store_true",
        help="明确允许 artifacts 根为专用目录 Junction/符号链接",
    )
    parser.add_argument("--manifest", type=Path, help="把本次候选清单写入 JSON")
    args = parser.parse_args()
    if not 0 <= args.older_than_days <= 3650:
        parser.error("--older-than-days 必须在 0 至 3650 之间；0 表示清理全部现有产物")

    root, root_is_reparse_point = _validated_root(
        allow_root_reparse_point=args.allow_root_reparse_point
    )
    candidates = _candidates(root, older_than_days=args.older_than_days)
    total = sum(item.bytes for item in candidates)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "root_reparse_point": root_is_reparse_point,
        "older_than_days": args.older_than_days,
        "apply": bool(args.apply),
        "candidate_count": len(candidates),
        "candidate_bytes": total,
        "candidates": [asdict(item) for item in candidates],
    }
    if args.manifest is not None:
        destination = args.manifest.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {key: payload[key] for key in payload if key != "candidates"},
            ensure_ascii=False,
        )
    )
    if args.apply:
        for candidate in candidates:
            _remove(candidate, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
