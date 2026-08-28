"""对战公开图片投影；姓名不含OpenID，随机种子仅留后台审计。"""

from dataclasses import dataclass, fields

from .dispatch_views import DispatchView


@dataclass(frozen=True, slots=True)
class FighterCard:
    player_name: str
    pig_name: str
    short_code: str
    level: int
    weight: str
    chance: str
    count: str
    injury: str
    risk: str
    core: str
    debt: str
    pending: str
    tool: str


@dataclass(frozen=True, slots=True)
class BattleWheelSegment:
    label: str
    weight: int


@dataclass(frozen=True, slots=True)
class BattleWheelCard:
    """静态权重图及已提交的落点；None表示只有规则、尚未抽取。"""

    kind: str
    title: str
    segments: tuple[BattleWheelSegment, ...]
    selected_index: int | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class BattleView(DispatchView):
    subtitle: str = "猪猪对战 · 轮盘交锋"
    fighters: tuple[FighterCard, ...] = ()
    battle_id: str = ""
    round_label: str = ""
    win_percent: str = "50"
    celebration: bool = False
    wheels: tuple[BattleWheelCard, ...] = ()

    @classmethod
    def from_payload(cls, data: dict) -> "BattleView":
        base = DispatchView.from_payload(data)
        return cls(
            **{field.name: getattr(base, field.name) for field in fields(DispatchView)},
            fighters=tuple(FighterCard(**item) for item in data.get("fighters", [])),
            battle_id=data.get("battle_id", ""),
            round_label=data.get("round_label", ""),
            win_percent=data.get("win_percent", "50"),
            celebration=data.get("celebration", False),
            wheels=tuple(
                BattleWheelCard(
                    kind=card["kind"],
                    title=card["title"],
                    segments=tuple(BattleWheelSegment(**segment) for segment in card["segments"]),
                    selected_index=card.get("selected_index"),
                    note=card.get("note", ""),
                )
                for card in data.get("wheels", ())
            ),
        )

    def text(self) -> str:
        lines = [DispatchView.text(self)]
        if self.battle_id:
            lines.append(f"{self.battle_id} · {self.round_label}")
        for fighter in self.fighters:
            lines.append(
                f"{fighter.player_name}｜{fighter.pig_name}#{fighter.short_code} +{fighter.level}"
                f"｜累计权重{fighter.weight}"
                f"｜回合胜率{fighter.chance}｜{fighter.count}｜{fighter.injury}｜{fighter.risk}"
                f"｜核心{fighter.core}｜{fighter.debt}｜{fighter.pending}｜{fighter.tool}"
            )
        return "\n".join(lines)
