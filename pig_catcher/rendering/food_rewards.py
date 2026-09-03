"""Public, receipt-backed views for food supplies, coupons and the green-core draw."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from ..services.economy import EatResult
from .models import RenderedImage

_REWARD_HINT = "输入 /道具背包 查看道具与奖励券，发放结果已保存。"
_REWARD_KINDS = {
    "material": ("材", "养成材料"),
    "ticket": ("券", "活动奖励券"),
    "coupon": ("券", "奖励券"),
    "chest": ("箱", "自选补给"),
    "pig": ("猪", "猪猪"),
    "food": ("菜", "美食"),
}


@dataclass(frozen=True, slots=True)
class FoodRewardItem:
    key: str
    name: str
    short_code: str = ""
    rarity: int = 0
    quantity: int = 1
    detail: str = ""
    image_relpath: str = ""
    template_id: str = ""
    kind: str = ""
    balance_after: int | None = None
    media_visible: bool = True

    @property
    def token_label(self) -> str:
        if not self.media_visible:
            return "藏"
        if self.kind in {"pig", "food"} and self.rarity:
            return f"{self.rarity}★"
        return _REWARD_KINDS.get(self.kind, ("礼", "奖励"))[0]

    @property
    def kind_label(self) -> str:
        return _REWARD_KINDS.get(self.kind, ("礼", "奖励"))[1]

    @property
    def tone(self) -> str:
        # Only fixed styles, never arbitrary user text in a class name.
        return self.kind if self.kind in _REWARD_KINDS else "reward"


@dataclass(frozen=True, slots=True)
class FoodRewardView:
    title: str
    player_name: str
    food_name: str
    summary: str
    coin_balance: int
    experience: int
    items: tuple[FoodRewardItem, ...]
    prize_label: str = ""
    animation: str = ""
    coin_bonus: int = 0
    hint: str = _REWARD_HINT


def food_reward_view(result: EatResult) -> FoodRewardView:
    payload = result.reward_payload
    items = []
    for index, item in enumerate(payload.get("items", [])):
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        media_visible = item.get("media_visible") is not False
        value = int(item.get("value") or 0)
        if not media_visible:
            detail = "素材授权已撤回 · 结算记录保留"
        elif kind in {"pig", "food"}:
            detail = f"价值 {value:,} 猪币"
        else:
            detail = str(item.get("use_hint") or "已加入道具背包，未自动使用")
        balance_after = item.get("balance_after")
        items.append(
            FoodRewardItem(
                key=str(index),
                name=str(item.get("name") or "奖励") if media_visible else "已隐藏的专属奖励",
                short_code=str(item.get("short_code") or ""),
                rarity=int(item.get("rarity") or 0),
                quantity=int(item.get("quantity") or 1),
                detail=detail,
                image_relpath=str(item.get("asset_path") or "") if media_visible else "",
                template_id=str(item.get("template_id") or ""),
                kind=kind,
                balance_after=int(balance_after) if balance_after is not None else None,
                media_visible=media_visible,
            )
        )
    is_lottery = bool(payload.get("prize_id"))
    reward_kind = str(payload.get("kind") or "")
    title_by_kind = {
        "catch-window-transfer": "月光迁时已生效",
        "window-six-star-resonance": "粉蓝共鸣已点亮",
    }
    hint_by_kind = {
        "catch-window-transfer": "下一个时段将封存额度；再下一个时段按月栖分布集中返还。",
        "window-six-star-resonance": "共鸣仅持续当前抓猪时段；抓猪与做菜会实时累积彼此的六星概率。",
    }
    prize_by_kind = {
        "catch-window-transfer": (
            f"{payload.get('blocked_window', '')} → {payload.get('target_window', '')}"
        ).strip(" →"),
        "window-six-star-resonance": str(payload.get("window") or ""),
    }
    return FoodRewardView(
        title=(
            "绿芯幸运揭晓"
            if is_lottery
            else title_by_kind[reward_kind]
            if reward_kind in title_by_kind
            else "满层加餐已到账"
            if reward_kind == "permanent-overflow"
            else "美食补给已到账"
        ),
        player_name=result.food.owner_display_name,
        food_name=result.food.display_name,
        summary=result.effect.summary,
        coin_balance=result.coin_balance,
        coin_bonus=result.effect.coin_bonus,
        experience=result.base_experience + result.effect.experience_bonus,
        items=tuple(items),
        prize_label=str(
            payload.get("prize_label")
            or payload.get("title")
            or prize_by_kind.get(reward_kind, "")
        ),
        animation=str(payload.get("animation") or ""),
        hint=(
            "奖励已加入你的猪猪／美食背包；重复查看不会再次发奖。"
            if is_lottery
            else hint_by_kind.get(reward_kind, _REWARD_HINT)
        ),
    )


def reveal_receipt(base: RenderedImage, *, max_bytes: int, theme: str) -> RenderedImage:
    """Animate a code-rendered receipt, never edit the supplied illustration.

    One HTML render, six bounded fade frames, play once and hold the complete
    result. If GIF encoding exceeds the budget, retain the readable PNG.
    """
    if theme not in {"pure-947", "original-947"}:
        return base
    raw = base64.b64decode(base.image_base64, validate=True)
    with Image.open(BytesIO(raw)) as decoded:
        if decoded.width * decoded.height > 2_500_000:
            return base
        final = decoded.convert("RGB")
    frames = []
    veil = Image.new("RGB", final.size, (240, 255, 249) if theme == "pure-947" else (255, 245, 224))
    try:
        for alpha in (0.12, 0.32, 0.56, 0.76, 0.92, 1.0):
            frame = Image.blend(veil, final, alpha)
            try:
                frames.append(frame.quantize(colors=256, method=Image.Quantize.FASTOCTREE))
            finally:
                frame.close()
        with BytesIO() as output:
            frames[0].save(
                output,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=[120, 120, 140, 140, 160, 4500],
                disposal=2,
                optimize=False,
            )
            encoded = output.getvalue()
        if len(encoded) > max_bytes:
            return base
        with Image.open(BytesIO(encoded)) as checked:
            count = checked.n_frames
            total = 0
            for index in range(count):
                checked.seek(index)
                total += int(checked.info.get("duration") or 0)
            if count != 6:
                return base
        return RenderedImage(
            image_base64=base64.b64encode(encoded).decode("ascii"),
            mime_type="image/gif",
            width=base.width,
            height=base.height,
            byte_length=len(encoded),
            frame_count=count,
            total_duration_ms=total,
            loop_count=None,
        )
    finally:
        for frame in frames:
            frame.close()
        veil.close()
        final.close()
