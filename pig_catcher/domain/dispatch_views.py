"""派遣图片与幂等收据共用的公开视图，不含随机种子或玩家 OpenID。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DispatchLine:
    label: str
    value: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class DispatchPanel:
    title: str
    rows: tuple[DispatchLine, ...] = ()
    caption: str = ""


@dataclass(frozen=True, slots=True)
class DispatchPigCard:
    name: str
    short_code: str
    rarity: int
    image_relpath: str
    tags: tuple[str, ...]
    summary: str
    favorite: bool = False
    template_id: str = ""


@dataclass(frozen=True, slots=True)
class DispatchView:
    title: str
    player_name: str
    subtitle: str = "猪猪远行社 · 派遣不消耗猪猪"
    banner: str = ""
    stats: tuple[DispatchLine, ...] = ()
    pigs: tuple[DispatchPigCard, ...] = ()
    panels: tuple[DispatchPanel, ...] = ()
    hints: tuple[str, ...] = ()
    page: int = 1
    page_count: int = 1
    achievement_title: str = ""
    achievement_frame: str = ""
    achievement_badge: str = ""
    achievement_badges: tuple[str, ...] = ()
    achievement_badge_capacity: int = 1
    # 仅影响模板路由和插画；不参与旅行、库存或经济结算。旧回执无字段仍可读。
    presentation: str = "dispatch"
    scene_key: str = ""

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> DispatchView:
        return cls(
            title=data["title"],
            player_name=data["player_name"],
            subtitle=data["subtitle"],
            banner=data["banner"],
            stats=tuple(DispatchLine(**item) for item in data["stats"]),
            pigs=tuple(DispatchPigCard(**{**item, "tags": tuple(item["tags"])}) for item in data["pigs"]),
            panels=tuple(
                DispatchPanel(
                    title=panel["title"],
                    caption=panel["caption"],
                    rows=tuple(DispatchLine(**item) for item in panel["rows"]),
                )
                for panel in data["panels"]
            ),
            hints=tuple(data["hints"]),
            page=data["page"],
            page_count=data["page_count"],
            achievement_title=data.get("achievement_title", ""),
            achievement_frame=data.get("achievement_frame", ""),
            achievement_badge=data.get("achievement_badge", ""),
            achievement_badges=tuple(data.get("achievement_badges", ())),
            achievement_badge_capacity=data.get("achievement_badge_capacity", 1),
            presentation=data.get("presentation", "dispatch"),
            scene_key=data.get("scene_key", ""),
        )

    def text(self) -> str:
        lines = [f"【{self.title}】{self.player_name}", self.subtitle]
        if self.banner:
            lines.append(self.banner)
        for item in self.stats:
            lines.append(f"{item.label}：{item.value} {item.note}".strip())
        for pig in self.pigs:
            lines.append(
                f"{'★' * pig.rarity} {pig.name}#{pig.short_code}｜{' / '.join(pig.tags)}｜{pig.summary}"
                + ("｜已收藏" if pig.favorite else "")
            )
        for panel in self.panels:
            lines.append(f"{panel.title} {panel.caption}".strip())
            lines.extend(f"{item.label}：{item.value} {item.note}".strip() for item in panel.rows)
        lines.extend(self.hints)
        if self.page_count > 1:
            lines.append(f"第{self.page}/{self.page_count}页")
        return "\n".join(lines)
