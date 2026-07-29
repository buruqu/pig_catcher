"""Build an immutable v2 asset package from the user-provided source library."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEFINITIONS = PROJECT_ROOT / "catalogs" / "2b" / "catalog-definitions.json"
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PIG_RANGES = {
    1: (18.0, 55.0, 8.0, 85.0),
    2: (22.0, 72.0, 14.0, 140.0),
    3: (28.0, 92.0, 20.0, 220.0),
    4: (34.0, 118.0, 28.0, 330.0),
    5: (40.0, 148.0, 38.0, 480.0),
    6: (36.0, 138.0, 34.0, 450.0),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--definitions", type=Path, default=DEFAULT_DEFINITIONS)
    return parser.parse_args()


def load_definitions(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("definition_version", 0)) != 1:
        raise ValueError("Unsupported catalog definition version")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Catalog definitions contain no entries")
    return raw


def source_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    }


def validate_coverage(root: Path, entries: list[dict[str, object]]) -> None:
    defined = [str(entry["source_path"]) for entry in entries]
    if len(defined) != len(set(defined)):
        raise ValueError("Catalog definitions contain duplicate source_path values")
    actual = source_files(root)
    expected = set(defined)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise ValueError(
            json.dumps(
                {"missing": missing, "unexpected": unexpected},
                ensure_ascii=False,
                indent=2,
            )
        )


def manifest_entry(
    definition: dict[str, object],
    *,
    image_path: str,
    source_label: str,
    license_label: str,
) -> dict[str, object]:
    rarity = int(definition["rarity"])
    kind = str(definition["kind"])
    group_scope_id = definition.get("group_scope_id")
    result: dict[str, object] = {
        "template_id": definition["template_id"],
        "kind": kind,
        "display_name": definition["display_name"],
        "rarity": rarity,
        "scope": "group" if group_scope_id else "common",
        "group_scope_id": group_scope_id,
        "description": definition["description"],
        "image": image_path,
        "fit": definition.get("fit", "contain"),
        "source": source_label,
        "license": license_label,
        "consent_status": "granted" if group_scope_id else "not-required",
        "recipe_tags": definition.get("recipe_tags", []),
        "effect_id": definition.get("effect_id", ""),
        "effect_params": definition.get("effect_params", {}),
        "collection": definition.get("collection"),
    }
    if kind == "pig":
        length_min, length_max, weight_min, weight_max = PIG_RANGES[rarity]
        result.update(
            {
                "length_min_cm": definition.get("length_min_cm", length_min),
                "length_max_cm": definition.get("length_max_cm", length_max),
                "weight_min_kg": definition.get("weight_min_kg", weight_min),
                "weight_max_kg": definition.get("weight_max_kg", weight_max),
                "fat_profile": definition.get("fat_profile", "balanced"),
                "stature_profile": definition.get("stature_profile", "standard"),
            }
        )
    return result


def build_package(
    *,
    source_root: Path,
    output_root: Path,
    definitions_path: Path,
) -> dict[str, object]:
    source_root = source_root.resolve(strict=True)
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"Output already exists: {output_root}")
    definitions = load_definitions(definitions_path.resolve(strict=True))
    entries = list(definitions["entries"])
    validate_coverage(source_root, entries)
    output_root.mkdir(parents=True)

    hash_to_media_path: dict[str, str] = {}
    aliases: list[dict[str, str]] = []
    manifest_entries: list[dict[str, object]] = []
    inventory: list[dict[str, object]] = []
    for raw_definition in entries:
        definition = dict(raw_definition)
        source_relative = str(definition["source_path"])
        source_path = source_root / Path(source_relative)
        payload = source_path.read_bytes()
        sha256 = hashlib.sha256(payload).hexdigest()
        media_path = hash_to_media_path.get(sha256)
        if media_path is None:
            media_path = f"media/{source_relative}"
            destination = output_root / Path(media_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            hash_to_media_path[sha256] = media_path
        else:
            aliases.append(
                {
                    "source_path": source_relative,
                    "canonical_media_path": media_path,
                    "sha256": sha256,
                }
            )
        manifest_entries.append(
            manifest_entry(
                definition,
                image_path=media_path,
                source_label=str(definitions["source_label"]),
                license_label=str(definitions["license"]),
            )
        )
        inventory.append(
            {
                "template_id": definition["template_id"],
                "source_path": source_relative,
                "media_path": media_path,
                "sha256": sha256,
                "bytes": len(payload),
            }
        )

    manifest = {
        "manifest_version": 3,
        "catalog_id": definitions["catalog_id"],
        "source_label": definitions["source_label"],
        "entries": manifest_entries,
    }
    (output_root / "assets.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = {
        "source_root": str(source_root),
        "definitions": str(definitions_path.resolve()),
        "entry_count": len(entries),
        "unique_binary_count": len(hash_to_media_path),
        "duplicate_aliases": aliases,
        "inventory": inventory,
    }
    (output_root / "build-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    args = parse_args()
    report = build_package(
        source_root=args.source,
        output_root=args.output,
        definitions_path=args.definitions,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
