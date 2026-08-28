"""通用道具背包、编号修改及自然属性自选猪，均为原子、图片优先结算。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from uuid import uuid4

from ..commands.item_bag import ItemBagRequest, parse_item_bag_request
from ..domain.dispatch import safe_display_name
from ..domain.dispatch_views import DispatchLine as Line
from ..domain.dispatch_views import DispatchPanel as Panel
from ..domain.dispatch_views import DispatchPigCard, DispatchView
from ..domain.display import display_tags_from_json, format_length, format_weight
from ..domain.enums import AssetKind
from ..domain.errors import AssetStateConflictError, DomainValidationError
from ..domain.gameplay import generate_pig_attributes
from ..domain.item_bag import (
    BAG_PAGE_SIZE,
    CHOICE_TTL_MS,
    CODE_CHANGE_COUPON,
    COUPON_HELP,
    LEGACY_CODE_CHANGE_COUPON,
    PIG_CHOICE_COUPON,
    REWARD_NAMES,
)
from ..domain.models import CommandIdentity
from ..domain.ports import Clock, MessageKeyFactory, RandomSource, SystemClock, SystemRandomSource
from ..domain.selectors import parse_asset_selector
from ..domain.short_codes import new_short_code, normalize_short_code
from ..infrastructure.database import DatabaseSession, PigCatcherDatabase
from ..infrastructure.repositories.administration import AdministrationRepository
from ..infrastructure.repositories.asset_codes import AssetCodeRepository
from ..infrastructure.repositories.dispatch import encode, iso_ms, timestamp_ms
from ..infrastructure.repositories.framework import FrameworkRepository
from ..infrastructure.repositories.gameplay import GameplayRepository
from ..infrastructure.repositories.item_bag import ItemBagRepository
from ..infrastructure.repositories.receipts import ReceiptRepository
from ..infrastructure.repositories.restrictions import RestrictionRepository
from ..version import RULESET_VERSION
from .command_state import validate_existing_receipt
from .dispatch import DispatchResult
from .receipts import request_fingerprint


class ItemBagService:
    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        clock: Clock | None = None,
        random_source: RandomSource | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database = database
        self.clock = clock or SystemClock()
        self.random_source = random_source or SystemRandomSource()
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.repository = ItemBagRepository()
        self.receipts = ReceiptRepository()
        self.assets = AdministrationRepository()
        self.codes = AssetCodeRepository()
        self.gameplay = GameplayRepository()

    async def grant_coupon(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        scope_id: str,
        coupon_id: str,
        quantity: int = 1,
        source_id: str,
        now: str,
        source_kind: str = "food",
        source_receipt_id: str = "",
    ) -> dict[str, object]:
        """供吃菜等已开启的事务调用；本方法从不另开事务或依赖成就开关。"""

        return await self.repository.grant_coupon(
            session,
            player_id=player_id,
            scope_id=scope_id,
            coupon_id=coupon_id,
            quantity=quantity,
            source_id=source_id,
            now=now,
            source_kind=source_kind,
            source_receipt_id=source_receipt_id,
        )

    async def bag(self, identity: CommandIdentity, page: int = 1) -> DispatchResult:
        if type(page) is not int or not 1 <= page <= 999_999:
            raise DomainValidationError("页码必须是正整数。")
        return await self.execute(identity, ItemBagRequest("bag", {"page": page}))

    async def reforge_identifier(
        self, identity: CommandIdentity, *, asset_kind: str, old_code: str, new_code: str
    ) -> DispatchResult:
        """保留 /重铸编号 参数，优先使用新券，否则兼容原编号重铸券。"""

        return await self.execute(
            identity,
            ItemBagRequest("rename", {"asset_kind": asset_kind, "selector": old_code, "new_code": new_code}),
        )

    async def execute(
        self, identity: CommandIdentity, request: ItemBagRequest | str, *, section: str = "coupon"
    ) -> DispatchResult:
        if isinstance(request, str):
            request = parse_item_bag_request(request, section=section)
        if request.action not in {"bag", "rename", "choose-pig", "confirm", "cancel"}:
            raise DomainValidationError("未知道具背包操作。")
        now_ms = timestamp_ms(self.clock.now())
        now = iso_ms(now_ms)
        command = "pig-catcher.reward-coupon"
        key = "" if request.action == "bag" else MessageKeyFactory.build(identity, command)
        payload = {"action": request.action, **request.args}
        async with self.database.transaction() as session:
            if key:
                existing = await self.receipts.get_by_key(session, key)
                if existing is not None:
                    validate_existing_receipt(
                        existing, identity=identity, command_name=command, request_payload=payload
                    )
                    view = DispatchView.from_payload(json.loads(existing.result_json)["view"])
                    return DispatchResult(await self._restrict_media(session, identity, view), existing)
            if await RestrictionRepository().active_plugin_access_ban(
                session, scope_id=identity.scope.value, platform_user_id=identity.user_id, now=now
            ):
                raise DomainValidationError("你已被当前群的插件访问黑名单限制，不能使用奖励券。")
            await FrameworkRepository().touch_identity(session, identity=identity, now=now)
            await session.execute(
                "DELETE FROM item_coupon_choices WHERE player_id=? AND expires_ms<=?", (identity.player_id, now_ms)
            )
            if request.action == "bag":
                view = await self._bag_view(session, identity, int(request.args.get("page", 1)))
            elif request.action == "rename":
                view = await self._rename(session, identity, request.args, now, key)
            elif request.action == "choose-pig":
                view = await self._choose_pig(session, identity, str(request.args["selector"]), now_ms)
            elif request.action == "confirm":
                view = await self._confirm_pig(session, identity, now_ms, key)
            else:
                await session.execute("DELETE FROM item_coupon_choices WHERE player_id=?", (identity.player_id,))
                view = self._view(identity, "奖励券确认已取消", banner="没有扣除奖励券，也没有生成或修改资产。")
            view = await self._restrict_media(session, identity, view)
            if not key:
                return DispatchResult(view)
            reservation = await self.receipts.reserve(
                session,
                idempotency_key=key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=command,
                request_fingerprint=request_fingerprint(payload),
                result_type="reward-coupon",
                result_object_id=key,
                result_json=encode({"view": view.payload()}),
                text_summary=view.text(),
                now=now,
                catch_quota_cost=0,
            )
            return DispatchResult(view, reservation.receipt)

    async def _bag_view(self, session: DatabaseSession, identity: CommandIdentity, page: int) -> DispatchView:
        entries = await self.repository.entries(session, player_id=identity.player_id)
        page_count = max(1, (len(entries) + BAG_PAGE_SIZE - 1) // BAG_PAGE_SIZE)
        page = max(1, min(page, page_count))
        panels = tuple(
            Panel(
                f"{entry.category} · {entry.name}",
                (Line("持有总数", str(entry.total), f"{entry.state} · 可用 {entry.available}"),),
                entry.summary,
            )
            for entry in entries[(page - 1) * BAG_PAGE_SIZE : page * BAG_PAGE_SIZE]
        )
        return self._view(
            identity,
            "道具背包",
            banner="总数已包含已装备、排队和待命份数，不重复相加；食物效果不是道具库存。",
            stats=(
                Line("持有种类", str(len(entries))),
                Line("未用与待命合计", str(sum(entry.total for entry in entries))),
            ),
            panels=panels or (Panel("背包空空", (Line("暂无道具", "去 /猪猪商城 查看，或通过吃菜与成就获得奖励。"),)),),
            hints=(
                "/道具背包 [页码]；/使用道具 道具名 [数量]；/取消道具 抓猪|做菜。",
                "/成就奖励 查看活动券/自选材料份；/派遣背包 查看原料；/抓猪成就 查看外观收藏。",
                *COUPON_HELP,
            ),
            page=page,
            page_count=page_count,
        )

    async def _rename(
        self, session: DatabaseSession, identity: CommandIdentity, args: dict, now: str, key: str
    ) -> DispatchView:
        kind = {"猪猪": AssetKind.PIG, "猪": AssetKind.PIG, "美食": AssetKind.FOOD, "菜": AssetKind.FOOD}.get(
            str(args["asset_kind"]).strip()
        )
        if kind is None:
            raise DomainValidationError("资产类型只能填写猪猪或美食。")
        new_code = normalize_short_code(str(args["new_code"]))
        target = await self._owned_target(session, identity, kind, str(args["selector"]))
        coupon_id = CODE_CHANGE_COUPON
        if await self.repository.quantity(session, identity.player_id, coupon_id) < 1:
            coupon_id = LEGACY_CODE_CHANGE_COUPON
        detail = await self.codes.rename_owned_asset(
            session,
            asset_kind=kind,
            asset_instance_id=target["instance_id"],
            owner_player_id=identity.player_id,
            scope_id=identity.scope.value,
            new_short_code=new_code,
            now=now,
        )
        remaining = await self.repository.consume_coupon(
            session, player_id=identity.player_id, coupon_id=coupon_id, now=now
        )
        await self._audit_use(
            session, identity, key, coupon_id, "asset-renamed", {**detail, "remaining": remaining}, now
        )
        return self._view(
            identity,
            "编号修改完成",
            banner="资产本身、收藏状态、体型、价值及养成均保留；新编号不区分大小写。",
            panels=(
                Panel(
                    str(detail["display_name"]),
                    (Line("原编号", f"#{detail['old_short_code']}"), Line("新编号", f"#{new_code}")),
                ),
            ),
            stats=(Line("已使用", REWARD_NAMES[coupon_id]), Line("此券剩余", f"{remaining} 张")),
            hints=("/道具背包 查看剩余奖励券；已释放的旧编号以后可以再次出现。",),
        )

    async def _owned_target(
        self, session: DatabaseSession, identity: CommandIdentity, kind: AssetKind, selector: str
    ) -> dict:
        table = "pig_instances" if kind is AssetKind.PIG else "food_instances"
        id_column = "pig_instance_id" if kind is AssetKind.PIG else "food_instance_id"
        base = (
            f"SELECT {id_column} AS instance_id,display_name_snapshot,short_code FROM {table} "
            "WHERE owner_player_id=? AND scope_id=? AND state IN('active','locked-for-trade') "
        )
        params = (identity.player_id, identity.scope.value)
        normalized = selector.strip()
        if re.fullmatch(r"[A-Za-z0-9]{4,16}", normalized):
            row = await session.fetch_one(base + "AND short_code COLLATE NOCASE=?", (*params, normalized))
            if row:
                return dict(row)
        parsed = parse_asset_selector(normalized)
        rows = await session.fetch_all(
            base + "AND display_name_snapshot=?" + (" AND short_code COLLATE NOCASE=?" if parsed.short_code else ""),
            (*params, parsed.name, *((parsed.short_code,) if parsed.short_code else ())),
        )
        if not rows:
            raise AssetStateConflictError("找不到当前群自己背包中的这件猪猪或美食。")
        if len(rows) > 1:
            raise DomainValidationError("同名资产有多件，请用完整的 名称#编号，或直接填写旧编号。")
        return dict(rows[0])

    async def _template(self, session: DatabaseSession, identity: CommandIdentity, selector: str) -> dict:
        rows = await self.assets.eligible_templates(
            session, scope_id=identity.scope.value, asset_kind=AssetKind.PIG, selector=selector
        )
        if not rows:
            raise DomainValidationError("当前群没有已启用且已授权的这只猪；不能选择其他群的六星。")
        if len(rows) != 1:
            raise DomainValidationError(
                "同名猪模板不唯一，请选择一个模板ID：" + "、".join(str(row["template_id"]) for row in rows)
            )
        return rows[0]

    @staticmethod
    def _template_fingerprint(template: dict) -> str:
        fields = (
            "template_id",
            "template_version",
            "display_name",
            "rarity",
            "length_min",
            "length_max",
            "weight_min",
            "weight_max",
            "fat_profile",
            "image_relpath",
            "stature_profile",
        )
        return request_fingerprint({field: template[field] for field in fields})

    async def _choose_pig(
        self, session: DatabaseSession, identity: CommandIdentity, selector: str, now_ms: int
    ) -> DispatchView:
        if await self.repository.quantity(session, identity.player_id, PIG_CHOICE_COUPON) < 1:
            raise DomainValidationError("你没有可用的猪猪自选券。")
        template = await self._template(session, identity, selector)
        payload = {"template_id": template["template_id"], "fingerprint": self._template_fingerprint(template)}
        await session.execute(
            "INSERT INTO item_coupon_choices VALUES(?,?,'pig-choice',?,?,?) ON CONFLICT(player_id) DO UPDATE SET "
            "scope_id=excluded.scope_id,payload_json=excluded.payload_json,expires_ms=excluded.expires_ms,created_at=excluded.created_at",
            (identity.player_id, identity.scope.value, encode(payload), now_ms + CHOICE_TTL_MS, iso_ms(now_ms)),
        )
        return self._view(
            identity,
            "猪猪自选 · 等待确认",
            banner="将消耗1张猪猪自选券。体型、重量和价值在确认时按自然规则随机生成，不受道具、美食或等级加成。",
            pigs=(self._pig_card(template, "待生成", "这是选择预览，尚未发放猪猪或扣除券。"),),
            hints=("30秒内输入 /使用奖励券 确认；/使用奖励券 取消。选择新的猪会替换旧预览。",),
        )

    async def _confirm_pig(
        self, session: DatabaseSession, identity: CommandIdentity, now_ms: int, key: str
    ) -> DispatchView:
        pending = await session.fetch_one(
            "SELECT * FROM item_coupon_choices WHERE player_id=? AND scope_id=? AND expires_ms>?",
            (identity.player_id, identity.scope.value, now_ms),
        )
        if pending is None:
            raise DomainValidationError("没有有效的自选猪确认；30秒后自动失效，请重新选择。")
        payload = json.loads(pending["payload_json"])
        template = await self._template(session, identity, payload["template_id"])
        if self._template_fingerprint(template) != payload["fingerprint"]:
            raise DomainValidationError("所选猪的模板已更新，请重新预览确认；奖励券未消耗。")
        now = iso_ms(now_ms)
        remaining = await self.repository.consume_coupon(
            session, player_id=identity.player_id, coupon_id=PIG_CHOICE_COUPON, now=now
        )
        rolls = tuple(self.random_source.random() for _ in range(5))
        attributes = generate_pig_attributes(
            rarity=int(template["rarity"]),
            length_min=float(template["length_min"]),
            length_max=float(template["length_max"]),
            weight_min=float(template["weight_min"]),
            weight_max=float(template["weight_max"]),
            fat_profile=str(template["fat_profile"]),
            random_values=rolls,
        )
        short_code = await self._new_short_code(session)
        instance_id = self.id_factory()
        snapshot = {
            "source": "reward-pig-choice",
            "coupon_id": PIG_CHOICE_COUPON,
            "source_receipt_key": key,
            "attribute_rolls": rolls,
            "gameplay_rewards_applied": False,
            "statistics_incremented": False,
            "ruleset_version": RULESET_VERSION,
        }
        await self.gameplay.insert_pig_instance(
            session,
            values={
                "pig_instance_id": instance_id,
                "short_code": short_code,
                "scope_id": identity.scope.value,
                "owner_player_id": identity.player_id,
                "template_id": template["template_id"],
                "template_version": int(template["template_version"]),
                "rarity": int(template["rarity"]),
                "display_name_snapshot": template["display_name"],
                "size_value": attributes.size_value,
                "size_percentile": attributes.size_percentile,
                "weight_value": attributes.weight_value,
                "weight_percentile": attributes.weight_percentile,
                "fat_ratio": attributes.fat_ratio,
                "official_value": attributes.official_value,
                "ruleset_version": RULESET_VERSION,
                "random_snapshot_json": encode(snapshot),
                "acquired_at": now,
                "updated_at": now,
            },
        )
        await self.gameplay.upsert_pig_catalog(
            session,
            player_id=identity.player_id,
            template_id=str(template["template_id"]),
            size_value=attributes.size_value,
            weight_value=attributes.weight_value,
            now=now,
        )
        await session.execute("DELETE FROM item_coupon_choices WHERE player_id=?", (identity.player_id,))
        await self._audit_use(
            session,
            identity,
            key,
            PIG_CHOICE_COUPON,
            "pig-selected",
            {
                "instance_id": instance_id,
                "template_id": template["template_id"],
                "short_code": short_code,
                "remaining": remaining,
                "attribute_rolls": list(rolls),
            },
            now,
        )
        summary = (
            f"{format_length(attributes.size_value)} · {format_weight(attributes.weight_value)}"
            f" · 价值 {attributes.official_value} 猪币"
        )
        return self._view(
            identity,
            "自选猪猪已到背包",
            banner="已消耗1张猪猪自选券，图鉴已记录；不扣抓猪额度、不增加抓猪奖励、经验或抓猪排行。",
            pigs=(self._pig_card(template, short_code, summary),),
            stats=(Line("猪猪自选券剩余", f"{remaining} 张"),),
            hints=(f"/猪猪详情 {template['display_name']}#{short_code}；/道具背包 查看道具。",),
        )

    async def _new_short_code(self, session: DatabaseSession) -> str:
        for _ in range(64):
            candidate = new_short_code()
            if not await self.codes.code_is_occupied(session, candidate):
                return candidate
        raise RuntimeError("无法生成唯一资产编号，本次自选已回滚。")

    async def _audit_use(
        self,
        session: DatabaseSession,
        identity: CommandIdentity,
        key: str,
        coupon_id: str,
        operation: str,
        detail: dict,
        now: str,
    ) -> None:
        await self.repository.record_use(
            session,
            key=key,
            player_id=identity.player_id,
            scope_id=identity.scope.value,
            coupon_id=coupon_id,
            operation=operation,
            detail=detail,
            now=now,
        )
        await self.assets.insert_audit_event(
            session,
            audit_event_id=self.id_factory(),
            scope_id=identity.scope.value,
            actor_user_id=identity.user_id,
            action="reward-coupon-" + operation,
            object_type="reward-coupon",
            object_id=key,
            detail_json=encode({"coupon_id": coupon_id, **detail}),
            now=now,
        )

    @staticmethod
    def _pig_card(template: dict, code: str, summary: str) -> DispatchPigCard:
        return DispatchPigCard(
            name=str(template["display_name"]),
            short_code=code,
            rarity=int(template["rarity"]),
            image_relpath=str(template["image_relpath"]),
            tags=display_tags_from_json(template.get("display_tags_json")),
            summary=summary,
            template_id=str(template["template_id"]),
        )

    @staticmethod
    def _view(identity: CommandIdentity, title: str, **kwargs) -> DispatchView:
        return DispatchView(
            title,
            safe_display_name(identity.display_name, identity.user_id),
            subtitle="道具与奖励",
            **kwargs,
        )

    @staticmethod
    async def _restrict_media(session: DatabaseSession, identity: CommandIdentity, view: DispatchView) -> DispatchView:
        if not view.pigs:
            return view
        allowed = set()
        for pig in view.pigs:
            row = await session.fetch_one(
                "SELECT 1 FROM pig_templates t LEFT JOIN scope_pig_templates a "
                "ON a.template_id=t.template_id AND a.scope_id=? WHERE t.template_id=? "
                "AND (t.scope_type='common' OR (a.authorized=1 AND a.consent_status='granted'))",
                (identity.scope.value, pig.template_id),
            )
            if row:
                allowed.add(pig.template_id)
        return replace(
            view,
            pigs=tuple(
                pig if pig.template_id in allowed else replace(pig, image_relpath="", tags=()) for pig in view.pigs
            ),
        )
