"""素材清单、路径、图片和授权校验。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from ..domain.errors import AssetValidationError
from .models import AssetManifest, ValidatedAsset, ValidatedManifest

_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _ensure_no_symlink(root: Path, relative_path: Path) -> None:
    current = root
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise AssetValidationError(f"素材路径不能经过符号链接：{relative_path.as_posix()}")


def _safe_asset_path(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise AssetValidationError(f"素材路径越界：{relative_path}")
    _ensure_no_symlink(root, relative)
    candidate = (root / relative).resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    if not candidate.is_relative_to(resolved_root):
        raise AssetValidationError(f"素材路径逃逸目录：{relative_path}")
    if not candidate.is_file():
        raise AssetValidationError(f"素材文件不存在：{relative_path}")
    return candidate


class AssetManifestValidator:
    """从 JSON 清单生成可安全导入的不可变结果。"""

    def __init__(self, *, min_image_side: int = 256, max_image_bytes: int = 12 * 1024 * 1024) -> None:
        self.min_image_side = int(min_image_side)
        self.max_image_bytes = int(max_image_bytes)
        if self.min_image_side < 32:
            raise ValueError("图片最短边不能低于 32 像素。")
        if self.max_image_bytes < 1024:
            raise ValueError("图片大小上限不能低于 1024 字节。")

    def validate_file(self, manifest_path: Path) -> ValidatedManifest:
        path = Path(manifest_path)
        if not path.is_file():
            raise AssetValidationError(f"素材清单不存在：{path.name}")
        if path.is_symlink():
            raise AssetValidationError("素材清单不能是符号链接。")
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise AssetValidationError("素材清单超过 2 MiB。")
        try:
            raw_manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AssetValidationError(f"素材清单不是有效 UTF-8 JSON：{exc}") from exc
        try:
            manifest = AssetManifest.model_validate(raw_manifest)
        except ValidationError as exc:
            raise AssetValidationError(f"素材清单字段不合法：{exc}") from exc

        source_root = path.parent.resolve(strict=True)
        validated_assets: list[ValidatedAsset] = []
        for entry in manifest.entries:
            source_path = _safe_asset_path(source_root, entry.image)
            file_size = source_path.stat().st_size
            if file_size > self.max_image_bytes:
                raise AssetValidationError(f"素材图片超过大小上限：{entry.image}")
            sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            try:
                with Image.open(source_path) as image:
                    image.verify()
                with Image.open(source_path) as image:
                    width, height = image.size
                    image_format = str(image.format or "").upper()
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise AssetValidationError(f"素材图片无法解码：{entry.image}") from exc
            if image_format not in {"PNG", "WEBP"}:
                raise AssetValidationError(f"素材图片格式必须是 PNG 或 WebP：{entry.image}")
            if min(width, height) < self.min_image_side:
                raise AssetValidationError(
                    f"素材图片最短边不足 {self.min_image_side}px：{entry.image}（{width}x{height}）"
                )
            validated_assets.append(
                ValidatedAsset(
                    entry=entry,
                    source_path=source_path,
                    sha256=sha256,
                    width=width,
                    height=height,
                    image_format=image_format,
                )
            )

        canonical = manifest.model_dump(mode="json")
        canonical["image_hashes"] = {
            asset.entry.template_id: asset.sha256
            for asset in sorted(
                validated_assets,
                key=lambda item: item.entry.template_id,
            )
        }
        catalog_hash = hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return ValidatedManifest(
            manifest=manifest,
            source_root=source_root,
            source_manifest_path=path.resolve(strict=True),
            catalog_hash=catalog_hash,
            assets=tuple(validated_assets),
        )
