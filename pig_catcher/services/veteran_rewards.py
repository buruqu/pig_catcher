"""Lv.21+ 一次性猪币里程碑结算。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..domain.gameplay import (
    veteran_benefits,
    veteran_milestone_coin_reward,
    veteran_milestone_level,
)
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories import EconomyRepository


@dataclass(frozen=True, slots=True)
class VeteranRewardSettlement:
    """One transaction's newly granted veteran milestone rewards."""

    coin_reward: int
    rewarded_levels: tuple[int, ...]
    balance_after: int

    @property
    def summary(self) -> str:
        if not self.coin_reward:
            return ""
        levels = "、".join(f"Lv.{level}" for level in self.rewarded_levels)
        return f"资深里程碑 {levels}：一次性获得 {self.coin_reward:,} 猪币"


async def settle_veteran_rewards(
    repository: EconomyRepository,
    session: DatabaseSession,
    *,
    player_id: str,
    scope_id: str,
    player_level: int,
    current_balance: int,
    id_factory: Callable[[], str],
    now: str,
) -> VeteranRewardSettlement:
    """Grant every earned but unclaimed milestone exactly once."""

    earned_tier = veteran_benefits(player_level).tier
    if earned_tier <= 0:
        return VeteranRewardSettlement(0, (), current_balance)
    claimed = await repository.claimed_veteran_reward_tiers(
        session,
        player_id=player_id,
    )
    balance = current_balance
    total = 0
    levels: list[int] = []
    for tier in range(1, earned_tier + 1):
        if tier in claimed:
            continue
        amount = veteran_milestone_coin_reward(tier)
        level = veteran_milestone_level(tier)
        updated_balance = await repository.apply_currency_change(
            session,
            player_id=player_id,
            scope_id=scope_id,
            amount=amount,
            reason_code="veteran-level-reward",
            reason_text=f"Lv.{level} 资深里程碑奖励",
            source_object_type="veteran-tier",
            source_object_id=str(tier),
            ledger_entry_id=id_factory(),
            idempotency_key=f"veteran-level-reward:{player_id}:{tier}",
            now=now,
        )
        if updated_balance is None:
            raise RuntimeError("资深里程碑猪币奖励无法写入玩家余额。")
        balance = updated_balance
        total += amount
        levels.append(level)
    return VeteranRewardSettlement(total, tuple(levels), balance)
