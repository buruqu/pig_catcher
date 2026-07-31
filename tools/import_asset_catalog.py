"""Validate and import one asset package into the plugin runtime data directory."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pig_catcher.assets import AssetCatalogStorage  # noqa: E402
from pig_catcher.infrastructure import PigCatcherDatabase  # noqa: E402
from pig_catcher.services import AssetCatalogService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--database-filename", default="pig_catcher.sqlite3")
    parser.add_argument("--min-image-side", type=int, default=200)
    parser.add_argument("--max-image-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-animation-frames", type=int, default=300)
    parser.add_argument("--max-animation-duration-ms", type=int, default=30000)
    return parser.parse_args()


async def import_catalog(args: argparse.Namespace) -> dict[str, object]:
    data_dir = args.data_dir.resolve()
    database = PigCatcherDatabase(data_dir / args.database_filename)
    await database.open()
    try:
        service = AssetCatalogService(
            database,
            AssetCatalogStorage(data_dir),
            min_image_side=args.min_image_side,
            max_image_bytes=args.max_image_bytes,
            max_animation_frames=args.max_animation_frames,
            max_animation_duration_ms=args.max_animation_duration_ms,
        )
        result = await service.import_manifest(args.manifest.resolve(strict=True))
        return {
            "catalog_id": result.catalog_id,
            "catalog_hash": result.catalog_hash,
            "entry_count": result.entry_count,
            "storage_relative_path": result.storage_relative_path,
            "schema_version": await database.schema_version(),
            "integrity_check": list(await database.integrity_check()),
        }
    finally:
        await database.close()


def main() -> None:
    result = asyncio.run(import_catalog(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
