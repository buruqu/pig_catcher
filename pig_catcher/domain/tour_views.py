"""巡演图片及同源文字收据；内部随机种子和玩家ID不进入公开投影。"""

from __future__ import annotations

from dataclasses import dataclass

from .dispatch_views import DispatchLine, DispatchView


@dataclass(frozen=True, slots=True)
class TourScoreCard:
    title: str
    score: str
    grade: str
    components: tuple[DispatchLine, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class TourView(DispatchView):
    subtitle: str = "自由乐队 · 猪猪巡演"
    band_name: str = "PiG Dream!"
    color: str = "#cf678f"
    emblem: str = "★"
    costume: str = ""
    scorecards: tuple[TourScoreCard, ...] = ()
    celebration: bool = False

    @classmethod
    def from_payload(cls, data: dict) -> TourView:
        base = DispatchView.from_payload(data)
        return cls(
            title=base.title,
            player_name=base.player_name,
            subtitle=base.subtitle,
            banner=base.banner,
            stats=base.stats,
            pigs=base.pigs,
            panels=base.panels,
            hints=base.hints,
            page=base.page,
            page_count=base.page_count,
            achievement_title=base.achievement_title,
            achievement_frame=base.achievement_frame,
            achievement_badge=base.achievement_badge,
            band_name=data["band_name"],
            color=data["color"],
            emblem=data["emblem"],
            costume=data["costume"],
            celebration=data["celebration"],
            scorecards=tuple(
                TourScoreCard(
                    title=item["title"],
                    score=item["score"],
                    grade=item["grade"],
                    components=tuple(DispatchLine(**row) for row in item["components"]),
                    note=item["note"],
                )
                for item in data["scorecards"]
            ),
        )

    def text(self) -> str:
        body = DispatchView.text(self)
        scores = []
        for card in self.scorecards:
            scores.append(f"{card.title}：{card.score}分 · {card.grade} · {card.note}")
            scores.extend(f"{line.label} {line.value} {line.note}".strip() for line in card.components)
        return "\n".join([f"乐队：{self.band_name}", body, *scores])
