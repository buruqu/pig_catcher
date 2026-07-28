"""素材校验、存储和数据库激活服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ..assets import AssetCatalogStorage, AssetManifestValidator
from ..domain.enums import AssetKind
from ..domain.ports import Clock, SystemClock
from ..infrastructure.database import PigCatcherDatabase
from ..infrastructure.repositories import AssetRepository


def _iso_timestamp(clock: Clock) -> str:
    return clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CatalogImportResult:
    """一次成功素材导入的可审计摘要。"""

    catalog_id: str
    catalog_hash: str
    entry_count: int
    storage_relative_path: str


class AssetCatalogService:
    """让文件发布先完成、数据库激活后完成。"""

    def __init__(
        self,
        database: PigCatcherDatabase,
        storage: AssetCatalogStorage,
        *,
        min_image_side: int,
        max_image_bytes: int,
        repository: AssetRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.storage = storage
        self.validator = AssetManifestValidator(
            min_image_side=min_image_side,
            max_image_bytes=max_image_bytes,
        )
        self.repository = repository or AssetRepository()
        self.clock = clock or SystemClock()

    async def import_manifest(self, manifest_path: Path) -> CatalogImportResult:
        validated = await asyncio.to_thread(self.validator.validate_file, manifest_path)
        stored = await self.storage.store(validated)
        async with self.database.transaction() as session:
            await self.repository.activate_catalog(
                session,
                validated=validated,
                stored=stored,
                now=_iso_timestamp(self.clock),
            )
        return CatalogImportResult(
            catalog_id=validated.manifest.catalog_id,
            catalog_hash=validated.catalog_hash,
            entry_count=len(validated.assets),
            storage_relative_path=stored.storage_relative_path,
        )

    async def list_drawable_template_ids(self, *, kind: AssetKind, scope_id: str) -> list[str]:
        async with self.database.transaction(immediate=False) as session:
            return await self.repository.list_drawable_template_ids(
                session,
                kind=kind,
                scope_id=scope_id,
            )
