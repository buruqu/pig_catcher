"""Preserve animated source media while composing it into a rendered card."""

from __future__ import annotations

import asyncio
import base64
import binascii
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from ..domain.errors import RenderError
from .models import MediaSlot, RenderedImage


class AnimatedCardComposer:
    """Composites every source frame over one HTML-rendered PNG background."""

    def __init__(
        self,
        *,
        max_output_bytes: int,
        missing_frame_duration_ms: int = 100,
    ) -> None:
        self.max_output_bytes = int(max_output_bytes)
        self.missing_frame_duration_ms = int(missing_frame_duration_ms)
        if self.max_output_bytes < 1024:
            raise ValueError("动画输出大小上限不能低于 1024 字节")
        if not 10 <= self.missing_frame_duration_ms <= 10000:
            raise ValueError("缺失帧时长回退值必须在 10 至 10000 毫秒之间")

    async def compose(
        self,
        *,
        base: RenderedImage,
        source_path: Path,
        slot: MediaSlot,
    ) -> RenderedImage:
        return await asyncio.to_thread(
            self._compose_sync,
            base,
            Path(source_path),
            slot,
        )

    def _compose_sync(
        self,
        base: RenderedImage,
        source_path: Path,
        slot: MediaSlot,
    ) -> RenderedImage:
        base_image = self._decode_base(base)
        self._validate_slot(slot, base_image.size)
        if not source_path.is_file():
            raise RenderError(f"动画素材不存在：{source_path.name}")

        try:
            with Image.open(source_path) as source:
                frame_count = int(getattr(source, "n_frames", 1))
                if frame_count <= 1:
                    raise RenderError(f"素材不是动画：{source_path.name}")
                loop_count = source.info.get("loop")
                composed_frames: list[Image.Image] = []
                durations: list[int] = []
                for frame_index, frame in enumerate(ImageSequence.Iterator(source)):
                    frame.load()
                    duration = int(frame.info.get("duration", source.info.get("duration", 0)) or 0)
                    durations.append(duration if duration > 0 else self.missing_frame_duration_ms)
                    canvas = base_image.copy()
                    fitted = self._fit_frame(frame.convert("RGBA"), slot)
                    canvas.alpha_composite(fitted, (slot.x, slot.y))
                    output_frame = canvas.convert("RGB")
                    # Pillow merges visually identical adjacent GIF frames. A
                    # one-pixel alternating marker keeps deliberate hold frames
                    # and their original timing as separate frames.
                    marker = (255, 255, 255) if frame_index % 2 == 0 else (0, 0, 0)
                    output_frame.putpixel(
                        (output_frame.width - 1, output_frame.height - 1),
                        marker,
                    )
                    composed_frames.append(output_frame)
        except RenderError:
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise RenderError(f"动画素材无法解码：{source_path.name}") from exc

        output = BytesIO()
        save_options: dict[str, object] = {
            "format": "GIF",
            "save_all": True,
            "append_images": composed_frames[1:],
            "duration": durations,
            "disposal": 2,
            "optimize": True,
        }
        if loop_count is not None:
            save_options["loop"] = int(loop_count)
        composed_frames[0].save(output, **save_options)
        raw = output.getvalue()
        if len(raw) > self.max_output_bytes:
            raise RenderError(
                f"动画卡片大小 {len(raw)} 字节超过上限 {self.max_output_bytes} 字节"
            )
        self._verify_output(
            raw,
            expected_size=base_image.size,
            expected_frames=len(composed_frames),
        )
        return RenderedImage(
            image_base64=base64.b64encode(raw).decode("ascii"),
            mime_type="image/gif",
            width=base_image.width,
            height=base_image.height,
            byte_length=len(raw),
            frame_count=len(composed_frames),
            total_duration_ms=sum(durations),
            loop_count=int(loop_count) if loop_count is not None else None,
        )

    @staticmethod
    def _decode_base(base: RenderedImage) -> Image.Image:
        if base.mime_type != "image/png" or base.is_animated:
            raise RenderError("动画合成底图必须是单帧 PNG")
        encoded = base.image_base64.strip()
        if encoded.startswith("data:"):
            _, separator, encoded = encoded.partition(",")
            if not separator:
                raise RenderError("动画合成底图包含无效 data URL")
        try:
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG":
                    raise RenderError("动画合成底图不是 PNG")
                image.load()
                return image.convert("RGBA")
        except RenderError:
            raise
        except (ValueError, binascii.Error, OSError, UnidentifiedImageError) as exc:
            raise RenderError("动画合成底图无法解码") from exc

    @staticmethod
    def _validate_slot(slot: MediaSlot, canvas_size: tuple[int, int]) -> None:
        if slot.width <= 0 or slot.height <= 0 or slot.x < 0 or slot.y < 0:
            raise RenderError("动画素材区域必须位于卡片内且尺寸为正")
        if slot.x + slot.width > canvas_size[0] or slot.y + slot.height > canvas_size[1]:
            raise RenderError("动画素材区域超出卡片边界")
        if slot.fit not in {"contain", "cover"}:
            raise RenderError("动画素材 fit 仅支持 contain 或 cover")

    @staticmethod
    def _fit_frame(frame: Image.Image, slot: MediaSlot) -> Image.Image:
        size = (slot.width, slot.height)
        if slot.fit == "cover":
            return ImageOps.fit(frame, size, method=Image.Resampling.LANCZOS)
        contained = ImageOps.contain(frame, size, method=Image.Resampling.LANCZOS)
        result = Image.new("RGBA", size, (255, 255, 255, 0))
        result.alpha_composite(
            contained,
            ((slot.width - contained.width) // 2, (slot.height - contained.height) // 2),
        )
        return result

    @staticmethod
    def _verify_output(
        raw: bytes,
        *,
        expected_size: tuple[int, int],
        expected_frames: int,
    ) -> None:
        try:
            with Image.open(BytesIO(raw)) as image:
                if image.format != "GIF":
                    raise RenderError("动画合成结果不是 GIF")
                if image.size != expected_size:
                    raise RenderError("动画合成结果尺寸发生变化")
                if int(getattr(image, "n_frames", 1)) != expected_frames:
                    raise RenderError("动画合成结果丢失帧")
                for frame in ImageSequence.Iterator(image):
                    frame.load()
        except RenderError:
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise RenderError("动画合成结果无法安全解码") from exc
