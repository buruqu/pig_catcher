"""素材目录的暂存、原子发布和安全清理。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

from ..domain.errors import AssetImportError
from .models import StoredCatalog, ValidatedManifest


def _safe_remove_tree(path: Path, allowed_root: Path) -> None:
    resolved_root = allowed_root.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_root or not resolved_path.is_relative_to(resolved_root):
        raise AssetImportError(f"拒绝清理素材暂存目录之外的路径：{resolved_path}")
    if path.exists():
        shutil.rmtree(path)


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
