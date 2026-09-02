"""Atomic, once-per-player PiG Dream! 2.0 launch starter pack."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from ..config.model import LaunchCampaignSection
from ..domain.dispatch import safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchView
from ..domain.economy import generate_food_attributes, recipe_affinity
from ..domain.enums import AssetKind
from ..domain.errors import DomainValidationError
from ..domain.item_bag import (
    BATTLE_PIG_CHOICE_COUPON,
    CODE_CHANGE_COUPON,
    FIVE_STAR_COLLAB_RANDOM_COUPON,
    FOOD_CHOICE_COUPON,
    PIG_CHOICE_COUPON,
)
from ..domain.launch_campaign import campaign_started
from ..domain.models import CommandIdentity
from ..domain.ports import Clock, RandomSource, SystemClock, SystemRandomSource
from ..domain.short_codes import new_short_code
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories.administration import AdministrationRepository
from ..infrastructure.repositories.asset_codes import AssetCodeRepository
from ..infrastructure.repositories.economy import EconomyRepository
from ..infrastructure.repositories.framework import FrameworkRepository
from ..infrastructure.repositories.item_bag import ItemBagRepository
from ..version import RULESET_VERSION
from .command_state import iso_timestamp
from .dispatch import DispatchResult

SIX_WAYS_FOOD_NAME = "一猪六吃"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class LaunchCouponBackfillSummary:
    """Result of the idempotent launch-event coupon backfill."""

    registered_players: int
    already_granted: int
    newly_granted: int
    quantity_per_player: int
    scope_counts: dict[str, int]


class LaunchCampaignService:
    """Grant the complete starter pack in one SQLite immediate transaction."""

    def __init__(
        self,
        database: PigCatcherDatabase,
        config: LaunchCampaignSection,
        *,
        clock: Clock | None = None,
        random_source: RandomSource | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self.config = config
        self.clock = clock or SystemClock()
        self.random_source = random_source or SystemRandomSource()
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.framework = FrameworkRepository()
        self.economy = EconomyRepository()
        self.items = ItemBagRepository()
        self.admin = AdministrationRepository()
        self.codes = AssetCodeRepository()

    async def claim_if_eligible(self, identity: CommandIdentity) -> DispatchResult | None:
        now_datetime = self.clock.now()
        if not campaign_started(self.config, now_datetime):
            return None
        now = iso_timestamp(now_datetime)
        async with self.database.transaction() as session:
            existing = await session.fetch_one(
                "SELECT 1 FROM launch_campaign_grants WHERE campaign_id=? AND player_id=?",
                (self.config.campaign_id, identity.player_id),
            )
            if existing is not None:
                return None
            await self.framework.touch_identity(session, identity=identity, now=now)
            templates = await self.admin.eligible_templates(
                session,
                scope_id=identity.scope.value,
                asset_kind=AssetKind.FOOD,
                selector=SIX_WAYS_FOOD_NAME,
            )
            if len(templates) != 1:
                raise DomainValidationError("开服礼包的一猪六吃模板未正确导入，本次礼包没有发放。")
            food_template = templates[0]
            balance = await self.economy.apply_currency_change(
                session,
                player_id=identity.player_id,
                scope_id=identity.scope.value,
                amount=int(self.config.starter_coin_amount),
                reason_code="v2-launch-starter-pack",
                reason_text="PiG Dream! 2.0 开服礼包",
                source_object_type="launch-campaign",
                source_object_id=self.config.campaign_id,
                ledger_entry_id=self.id_factory(),
                idempotency_key=f"launch:{self.config.campaign_id}:{identity.player_id}:coins",
                now=now,
            )
            if balance is None:
                raise RuntimeError("开服礼包猪币入账失败。")
            ticket_quantities = {
                PIG_CHOICE_COUPON: int(self.config.starter_pig_choice_tickets),
                FOOD_CHOICE_COUPON: int(self.config.starter_food_choice_tickets),
                BATTLE_PIG_CHOICE_COUPON: int(self.config.starter_battle_pig_choice_tickets),
                FIVE_STAR_COLLAB_RANDOM_COUPON: int(self.config.starter_five_star_collab_random_tickets),
                CODE_CHANGE_COUPON: int(self.config.starter_code_change_tickets),
            }
            for coupon_id, quantity in ticket_quantities.items():
                if quantity <= 0:
                    continue
                await self.items.grant_coupon(
                    session,
                    player_id=identity.player_id,
                    scope_id=identity.scope.value,
                    coupon_id=coupon_id,
                    quantity=quantity,
                    source_id=f"{self.config.campaign_id}:{coupon_id}",
                    now=now,
                    source_kind="launch-campaign",
                )
            food_codes: list[str] = []
            for index in range(int(self.config.starter_six_ways_foods)):
                food_codes.append(
                    await self._grant_six_ways_food(
                        session,
                        identity=identity,
                        template=food_template,
                        index=index,
                        now=now,
                    )
                )
            result = {
                "campaign_id": self.config.campaign_id,
                "coin_amount": int(self.config.starter_coin_amount),
                "balance": balance,
                "tickets": ticket_quantities,
                "six_ways_food_codes": food_codes,
            }
            await session.execute(
                "INSERT INTO launch_campaign_grants(campaign_id,player_id,scope_id,result_json,created_at) "
                "VALUES(?,?,?,?,?)",
                (self.config.campaign_id, identity.player_id, identity.scope.value, _json(result), now),
            )
            view = DispatchView(
                "2.0 开服礼包已领取",
                safe_display_name(identity.display_name, identity.user_id),
                subtitle="PiG Dream! 抓猪派对! · 盛大开服",
                banner="礼包已经一次性全部进入你的当前群资产；同一账号重复发送指令不会再次领取。",
                stats=(
                    Line("开服猪币", f"+{int(self.config.starter_coin_amount):,}"),
                    Line("当前余额", f"{balance:,} 猪币"),
                    Line("开服首日", "每时段20次 · 4/5/6星权重×2"),
                ),
                panels=(
                    Panel(
                        "自选与随机券",
                        (
                            Line("猪猪自选券", f"×{ticket_quantities[PIG_CHOICE_COUPON]}"),
                            Line("美食自选券", f"×{ticket_quantities[FOOD_CHOICE_COUPON]}"),
                            Line("战斗猪自选券", f"×{ticket_quantities[BATTLE_PIG_CHOICE_COUPON]}"),
                            Line("五星联动猪随机券", f"×{ticket_quantities[FIVE_STAR_COLLAB_RANDOM_COUPON]}"),
                            Line("编号修改券", f"×{ticket_quantities[CODE_CHANGE_COUPON]}"),
                        ),
                    ),
                    Panel(
                        "开服美食",
                        (Line(SIX_WAYS_FOOD_NAME, f"×{len(food_codes)}"),),
                        "奖励美食不增加做菜经验、收益或周榜成绩。",
                    ),
                ),
                hints=("/道具背包 查看券；/美食背包 查看一猪六吃；/抓猪 开始首日冒险。",),
                presentation="item-bag",
                scene_key="v2-launch",
            )
            return DispatchResult(view)

    async def grant_code_change_bonus_to_registered_players(
        self,
        *,
        actor_user_id: str = "system:week1-launch",
    ) -> LaunchCouponBackfillSummary:
        """Give every registered player the launch-event code coupons exactly once.

        New registrants receive the same grant through ``claim_if_eligible``. Existing
        registrants are selected and granted inside one immediate transaction so the
        preview/execute tool can be safely rerun after an uncertain operator result.
        """

        quantity = int(self.config.starter_code_change_tickets)
        if quantity <= 0:
            raise DomainValidationError("开服追加编号修改券数量必须大于0。")
        now = iso_timestamp(self.clock.now())
        source_id = f"{self.config.campaign_id}:{CODE_CHANGE_COUPON}"
        async with self.database.transaction() as session:
            players = await session.fetch_all(
                "SELECT player_id,scope_id FROM players ORDER BY scope_id,player_id"
            )
            existing_rows = await session.fetch_all(
                "SELECT player_id FROM reward_coupon_grants "
                "WHERE source_kind='launch-campaign' AND source_id=? AND coupon_id=?",
                (source_id, CODE_CHANGE_COUPON),
            )
            existing = {str(row["player_id"]) for row in existing_rows}
            scope_counts: dict[str, int] = {}
            newly_granted = 0
            for player in players:
                player_id = str(player["player_id"])
                scope_id = str(player["scope_id"])
                if player_id in existing:
                    continue
                await self.items.grant_coupon(
                    session,
                    player_id=player_id,
                    scope_id=scope_id,
                    coupon_id=CODE_CHANGE_COUPON,
                    quantity=quantity,
                    source_id=source_id,
                    now=now,
                    source_kind="launch-campaign",
                )
                newly_granted += 1
                scope_counts[scope_id] = scope_counts.get(scope_id, 0) + 1
            if newly_granted:
                await session.execute(
                    "INSERT INTO audit_events(audit_event_id,scope_id,actor_user_id,action,"
                    "object_type,object_id,detail_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        self.id_factory(),
                        None,
                        actor_user_id,
                        "launch-event-code-change-bonus-granted",
                        "launch-campaign",
                        self.config.campaign_id,
                        _json(
                            {
                                "coupon_id": CODE_CHANGE_COUPON,
                                "quantity_per_player": quantity,
                                "registered_players": len(players),
                                "newly_granted": newly_granted,
                                "scope_counts": scope_counts,
                                "source_kind": "launch-campaign",
                                "source_id": source_id,
                            }
                        ),
                        now,
                    ),
                )
            return LaunchCouponBackfillSummary(
                registered_players=len(players),
                already_granted=len(existing.intersection({str(row["player_id"]) for row in players})),
                newly_granted=newly_granted,
                quantity_per_player=quantity,
                scope_counts=scope_counts,
            )

    async def _grant_six_ways_food(
        self,
        session: DatabaseSession,
        *,
        identity: CommandIdentity,
        template: dict[str, object],
        index: int,
        now: str,
    ) -> str:
        portion_roll = self.random_source.random()
        attributes = generate_food_attributes(
            rarity=int(template["rarity"]),
            template_id=str(template["template_id"]),
            source_weight=60.0,
            source_weight_percentile=0.5,
            portion_roll=portion_roll,
        )
        try:
            tags_payload = json.loads(str(template.get("recipe_tags_json") or "[]"))
        except json.JSONDecodeError:
            tags_payload = []
        fat_category = recipe_affinity(
            tuple(str(value) for value in tags_payload) if isinstance(tags_payload, list) else ()
        )
        short_code = await self._new_short_code(session)
        instance_id = self.id_factory()
        snapshot = {
            "source": "v2-launch-starter-pack",
            "campaign_id": self.config.campaign_id,
            "campaign_food_index": index,
            "portion_roll": portion_roll,
            "gameplay_rewards_applied": False,
            "statistics_incremented": False,
            "ruleset_version": RULESET_VERSION,
        }
        await self.economy.insert_food_instance(
            session,
            values={
                "food_instance_id": instance_id,
                "short_code": short_code,
                "scope_id": identity.scope.value,
                "owner_player_id": identity.player_id,
                "template_id": template["template_id"],
                "template_version": int(template["template_version"]),
                "source_pig_instance_id": None,
                "rarity": int(template["rarity"]),
                "display_name_snapshot": template["display_name"],
                "portion_weight": attributes.portion_weight,
                "fat_category": fat_category,
                "official_value": attributes.official_value,
                "effect_id": str(template.get("effect_id") or ""),
                "effect_params_json": str(template.get("effect_params_json") or "{}"),
                "ruleset_version": RULESET_VERSION,
                "random_snapshot_json": _json(snapshot),
                "acquired_at": now,
                "updated_at": now,
            },
        )
        await self.economy.upsert_food_catalog(
            session,
            player_id=identity.player_id,
            template_id=str(template["template_id"]),
            portion_weight=attributes.portion_weight,
            now=now,
        )
        return short_code

    async def _new_short_code(self, session: DatabaseSession) -> str:
        for _ in range(64):
            candidate = new_short_code()
            if not await self.codes.code_is_occupied(session, candidate):
                return candidate
        raise RuntimeError("开服礼包无法生成唯一资产编号，整包发放已回滚。")


__all__ = ["LaunchCampaignService", "LaunchCouponBackfillSummary"]
