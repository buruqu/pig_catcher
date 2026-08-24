"""素材目录的暂存、原子发布和安全清理。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..domain.errors import AssetImportError
from .models import StoredCatalog, ValidatedManifest

_CATALOG_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _safe_remove_tree(path: Path, allowed_root: Path) -> None:
    resolved_root = allowed_root.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_root or not resolved_path.is_relative_to(resolved_root):
        raise AssetImportError(f"拒绝清理素材根目录之外的路径：{resolved_path}")
    if path.exists():
        shutil.rmtree(path)


def _directory_size(path: Path) -> int:
    """计算目录内普通文件大小，不跟随符号链接。"""

    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        directories[:] = [
            name for name in directories if not (root_path / name).is_symlink()
        ]
        for name in files:
            candidate = root_path / name
            try:
                if candidate.is_file() and not candidate.is_symlink():
                    total += candidate.stat().st_size
            except FileNotFoundError:
                continue
    return total


def _catalog_hash_from_path(relative_path: str) -> str | None:
    normalized = str(relative_path or "").strip().replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        return None
    parts = PurePosixPath(normalized).parts
    if (
        len(parts) < 3
        or parts[0] != "assets"
        or parts[1] != "catalogs"
        or not _CATALOG_HASH_PATTERN.fullmatch(parts[2])
    ):
        return None
    return parts[2]


class AssetCatalogStorage:
    """管理数据目录中的不可变素材目录。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.assets_root = self.data_dir / "assets"
        self.catalogs_root = self.assets_root / "catalogs"
        self.staging_root = self.assets_root / ".staging"
        self._lock = asyncio.Lock()

    def ensure_layout(self) -> None:
        self.catalogs_root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)

    async def store(self, validated: ValidatedManifest) -> StoredCatalog:
        """复制清单和图片，并通过目录重命名原子发布。"""

        async with self._lock:
            self.ensure_layout()
            target = self.catalogs_root / validated.catalog_hash
            if target.is_dir():
                return self._stored_catalog(validated.catalog_hash, target)
            staging = self.staging_root / f"{validated.catalog_hash[:12]}-{uuid4().hex}"
            try:
                await asyncio.to_thread(self._copy_catalog, validated, staging)
                os.replace(staging, target)
            except Exception as exc:
                if staging.exists():
                    await asyncio.to_thread(_safe_remove_tree, staging, self.staging_root)
                if isinstance(exc, AssetImportError):
                    raise
                raise AssetImportError(f"素材目录发布失败：{exc}") from exc
            return self._stored_catalog(validated.catalog_hash, target)

    def _copy_catalog(self, validated: ValidatedManifest, staging: Path) -> None:
        staging.mkdir(parents=True, exist_ok=False)
        files_root = staging / "files"
        files_root.mkdir()
        for asset in validated.assets:
            relative_path = Path(asset.entry.image)
            destination = files_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset.source_path, destination)
            if asset.entry.alternate_image:
                alt_relative_path = Path(asset.entry.alternate_image)
                alt_source = asset.alternate_source_path
                if alt_source is None:
                    raise AssetImportError(
                        f"备用图片未通过素材校验：{asset.entry.alternate_image}"
                    )
                alt_destination = files_root / alt_relative_path
                alt_destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(alt_source, alt_destination)
        manifest_data = validated.manifest.model_dump(mode="json")
        (staging / "manifest.json").write_text(
            json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        validation_data = {
            "catalog_hash": validated.catalog_hash,
            "assets": [
                {
                    "template_id": asset.entry.template_id,
                    "sha256": asset.sha256,
                    "width": asset.width,
                    "height": asset.height,
                    "format": asset.image_format,
                    "is_animated": asset.is_animated,
                    "frame_count": asset.frame_count,
                    "frame_durations_ms": list(asset.frame_durations_ms),
                    "total_duration_ms": asset.total_duration_ms,
                    "loop_count": asset.loop_count,
                    "has_transparency": asset.has_transparency,
                    "alternate_sha256": asset.alternate_sha256,
                }
                for asset in validated.assets
            ],
        }
        (staging / "validated.json").write_text(
            json.dumps(validation_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _stored_catalog(self, catalog_hash: str, target: Path) -> StoredCatalog:
        relative_path = target.relative_to(self.data_dir).as_posix()
        return StoredCatalog(
            catalog_hash=catalog_hash,
            root=target,
            storage_relative_path=relative_path,
        )

    async def cleanup_staging(self, *, older_than_hours: int) -> int:
        """只清理本插件暂存根目录下的过期目录。"""

        async with self._lock:
            self.ensure_layout()
            cutoff = time.time() - int(older_than_hours) * 3600
            candidates = [
                child for child in self.staging_root.iterdir() if child.is_dir() and child.stat().st_mtime < cutoff
            ]
            for candidate in candidates:
                await asyncio.to_thread(_safe_remove_tree, candidate, self.staging_root)
            return len(candidates)

    async def cleanup_catalogs(
        self,
        referenced_paths: tuple[str, ...] | list[str],
        *,
        retain_unreferenced: int = 1,
        minimum_age_hours: int = 24,
    ) -> tuple[int, int]:
        """清理未被引用且已度过新发布保护期的旧不可变素材目录。"""

        async with self._lock:
            self.ensure_layout()
            cleanup_cutoff = time.time() - max(1, int(minimum_age_hours)) * 3600
            protected_hashes = {
                catalog_hash
                for relative_path in referenced_paths
                if (catalog_hash := _catalog_hash_from_path(relative_path)) is not None
            }
            candidates = [
                child
                for child in self.catalogs_root.iterdir()
                if child.is_dir()
                and not child.is_symlink()
                and _CATALOG_HASH_PATTERN.fullmatch(child.name)
                and child.name not in protected_hashes
                and child.stat().st_mtime <= cleanup_cutoff
            ]
            candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            stale = candidates[max(0, int(retain_unreferenced)) :]
            removed_bytes = 0
            for candidate in stale:
                removed_bytes += await asyncio.to_thread(_directory_size, candidate)
                await asyncio.to_thread(
                    _safe_remove_tree,
                    candidate,
                    self.catalogs_root,
                )
            return len(stale), removed_bytes
