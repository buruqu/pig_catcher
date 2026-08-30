"""对战公开图片投影；姓名不含OpenID，随机种子仅留后台审计。"""

from dataclasses import dataclass, fields

from .dispatch_views import DispatchLine, DispatchView


@dataclass(frozen=True, slots=True)
class BattleWheelSegment:
    label: str
    weight: int | float


@dataclass(frozen=True, slots=True)
class BattleWheelCard:
    """静态权重图及已提交的落点；None表示只有规则、尚未抽取。"""

    kind: str
    title: str
    segments: tuple[BattleWheelSegment, ...]
    selected_index: int | None = None
    note: str = ""


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
    ready: str = ""
    weight_breakdown: str = ""
    next_weight: str = ""
    count_wheel: BattleWheelCard | None = None
    move_wheel: BattleWheelCard | None = None
    action_lines: tuple[DispatchLine, ...] = ()
    action_note: str = ""


@dataclass(frozen=True, slots=True)
class BattleView(DispatchView):
    subtitle: str = "猪猪对战 · 轮盘交锋"
    fighters: tuple[FighterCard, ...] = ()
    battle_id: str = ""
    round_label: str = ""
    win_percent: str = "50"
    celebration: bool = False
    wheels: tuple[BattleWheelCard, ...] = ()
    retention_mode: str = "legacy-full"

    @classmethod
    def from_payload(cls, data: dict) -> "BattleView":
        def parse_wheel(card: dict | None) -> BattleWheelCard | None:
            if not card:
                return None
            return BattleWheelCard(
                kind=card["kind"],
                title=card["title"],
                segments=tuple(BattleWheelSegment(**segment) for segment in card["segments"]),
                selected_index=card.get("selected_index"),
                note=card.get("note", ""),
            )

        def parse_fighter(item: dict) -> FighterCard:
            return FighterCard(
                **{
                    key: item[key]
                    for key in (
                        "player_name",
                        "pig_name",
                        "short_code",
                        "level",
                        "weight",
                        "chance",
                        "count",
                        "injury",
                        "risk",
                        "core",
                        "debt",
                        "pending",
                        "tool",
                    )
                },
                ready=item.get("ready", ""),
                weight_breakdown=item.get("weight_breakdown", ""),
                next_weight=item.get("next_weight", ""),
                count_wheel=parse_wheel(item.get("count_wheel")),
                move_wheel=parse_wheel(item.get("move_wheel")),
                action_lines=tuple(DispatchLine(**line) for line in item.get("action_lines", ())),
                action_note=item.get("action_note", ""),
            )

        base = DispatchView.from_payload(data)
        return cls(
            **{field.name: getattr(base, field.name) for field in fields(DispatchView)},
            fighters=tuple(parse_fighter(item) for item in data.get("fighters", [])),
            battle_id=data.get("battle_id", ""),
            round_label=data.get("round_label", ""),
            win_percent=data.get("win_percent", "50"),
            celebration=data.get("celebration", False),
            wheels=tuple(parse_wheel(card) for card in data.get("wheels", ()) if card),
            retention_mode=data.get("retention_mode", "legacy-full"),
        )

    def text(self) -> str:
        lines = [DispatchView.text(self)]
        if self.battle_id:
            lines.append(f"{self.battle_id} · {self.round_label}")
        for fighter in self.fighters:
            lines.append(
                f"{fighter.player_name}｜{fighter.pig_name}#{fighter.short_code} +{fighter.level}"
                f"｜{'本回合结算权重' if self.retention_mode == 'half-round' else '累计权重'}{fighter.weight}"
                f"｜回合胜率{fighter.chance}｜{fighter.count}｜{fighter.injury}｜{fighter.risk}"
                f"｜核心{fighter.core}｜{fighter.debt}｜{fighter.pending}｜{fighter.ready}｜{fighter.tool}"
            )
            if fighter.weight_breakdown:
                lines.append(f"  权重构成：{fighter.weight_breakdown}")
            if fighter.next_weight:
                lines.append(f"  {fighter.next_weight}")
            lines.extend(
                f"  {item.label}：{item.value} {item.note}".strip() for item in fighter.action_lines
            )
            if fighter.action_note:
                lines.append(f"  {fighter.action_note}")
        return "\n".join(lines)
