"""已拥有徽章的三槽展示管理，不执行成就补发、概率或经济操作。"""

from __future__ import annotations

import json
from collections.abc import Mapping

from ..domain.achievements import ACHIEVEMENT_DEFINITIONS
from ..domain.dispatch import safe_display_name
from ..domain.dispatch_views import DispatchLine, DispatchPanel, DispatchView
from ..domain.errors import DomainValidationError
from ..domain.models import CommandIdentity
from ..domain.ports import MessageKeyFactory
from ..infrastructure.database import DatabaseSession
from ..infrastructure.repositories.achievements import AchievementRepository
from ..infrastructure.repositories.receipts import ReceiptRepository
from .achievements import AchievementService, _now_text
from .command_state import validate_existing_receipt
from .dispatch import DispatchResult
from .receipts import request_fingerprint


class AchievementBadgeService:
    """纯名称目录由调用方传入；只列出当前玩家库存中的徽章。"""

    def __init__(self, achievements: AchievementService, *, labels: Mapping[str, str]) -> None:
        self.achievements = achievements
        self.repository = AchievementRepository()
        self.labels = labels

    async def execute(self, identity: CommandIdentity, arguments: str = "") -> DispatchResult:
        text = arguments.strip()
        words = text.split(maxsplit=1)
        query = not words or words[0] == "查看"
        page = 1
        slot = 0
        selector = ""
        if query:
            if len(words) == 2:
                if not words[1].isascii() or not words[1].isdecimal() or len(words[1]) > 4:
                    raise DomainValidationError("请使用 /成就徽章 查看 页码。")
                page = max(1, int(words[1]))
        elif words[0] == "卸下":
            if len(words) != 2 or words[1] not in {"1", "2", "3"}:
                raise DomainValidationError("请使用 /成就徽章 卸下 1（位置可填1、2、3）。")
            slot = int(words[1])
        else:
            if len(words) != 2 or words[0] not in {"1", "2", "3"} or not words[1].strip():
                raise DomainValidationError("请使用 /成就徽章 1 徽章名；/成就徽章 查看已获得的徽章。")
            slot, selector = int(words[0]), words[1].strip()
        command = "pig-catcher.achievement-badge"
        key = "" if query else MessageKeyFactory.build(identity, command)
        payload = {"arguments": text}
        receipts = ReceiptRepository()
        now = _now_text(self.achievements.clock)
        async with self.achievements.database.transaction() as session:
            if key:
                previous = await receipts.get_by_key(session, key)
                if previous:
                    validate_existing_receipt(
                        previous, identity=identity, command_name=command, request_payload=payload
                    )
                    return DispatchResult(DispatchView.from_payload(json.loads(previous.result_json)["view"]), previous)
            await self.achievements.framework_repository.touch_identity(session, identity=identity, now=now)
            await self.repository.ensure_profile(session, player_id=identity.player_id, now=now)
            banner = "徽章与展示位是永久外观，不消耗库存、不改变任何概率。"
            if not query:
                badge_id = await self._owned_badge(session, identity.player_id, selector) if selector else ""
                await self.repository.update_badge_slot(
                    session, player_id=identity.player_id, slot=slot, badge_id=badge_id, now=now
                )
                banner = (
                    f"第{slot}位已佩戴：{self.labels.get(badge_id, badge_id)}。"
                    if badge_id
                    else f"第{slot}位已卸下；其他外观和所有奖励完整保留。"
                )
            view = await self._view(session, identity, banner=banner, page=page)
            if not key:
                return DispatchResult(view)
            reserved = await receipts.reserve(
                session,
                idempotency_key=key,
                scope_id=identity.scope.value,
                player_id=identity.player_id,
                command_name=command,
                request_fingerprint=request_fingerprint(payload),
                result_type="achievement-badge",
                result_object_id=identity.player_id,
                result_json=json.dumps({"view": view.payload()}, ensure_ascii=False),
                text_summary=view.text(),
                now=now,
                catch_quota_cost=0,
            )
            return DispatchResult(view, reserved.receipt)

    async def _owned_badge(self, session: DatabaseSession, player_id: str, selector: str) -> str:
        normalized = selector.casefold()
        rows = await self.repository.reward_rows(session, player_id=player_id)
        owned = {str(row["reward_id"]) for row in rows if row["reward_type"] == "badge"}
        matches = {
            reward_id
            for reward_id in owned
            if normalized in {reward_id.casefold(), self.labels.get(reward_id, reward_id).casefold()}
        }
        for definition in ACHIEVEMENT_DEFINITIONS:
            if normalized in {definition.name.casefold(), definition.achievement_id.casefold()}:
                matches.update(
                    reward.reward_id
                    for reward in definition.rewards
                    if reward.reward_type == "badge" and reward.reward_id in owned
                )
        if not matches:
            # Never reveal whether a guessed hidden reward exists elsewhere.
            raise DomainValidationError("你在本群尚未获得这个徽章，请 /成就徽章 查看已拥有的徽章。")
        if len(matches) > 1:
            raise DomainValidationError("这个名称对应多个已拥有的徽章，请使用 /成就徽章 中显示的徽章ID。")
        return next(iter(matches))

    async def _view(
        self, session: DatabaseSession, identity: CommandIdentity, *, banner: str, page: int
    ) -> DispatchView:
        profile = await self.repository.profile_row(session, player_id=identity.player_id)
        cosmetics = self.achievements._cosmetics_from_profile(profile)
        rows = [
            row
            for row in await self.repository.reward_rows(session, player_id=identity.player_id)
            if row["reward_type"] == "badge"
        ]
        page_count = max(1, (len(rows) + 7) // 8)
        page = min(page, page_count)
        return DispatchView(
            title="我的徽章展示架",
            player_name=safe_display_name(identity.display_name, identity.user_id),
            subtitle="PiG Dream! · 只展示已获得的纪念",
            presentation="cosmetics",
            banner=banner,
            stats=(
                DispatchLine("展示位", f"{cosmetics.badge_capacity} 格"),
                DispatchLine("徽章收藏", f"{len(rows)} 枚"),
            ),
            panels=(
                DispatchPanel(
                    "已佩戴徽章",
                    tuple(
                        DispatchLine(f"第{index}位", self.labels.get(badge, badge) if badge else "空位")
                        for index, badge in enumerate(cosmetics.badge_ids, 1)
                    ),
                    "获得500成就点里程碑奖励后永久开放三格。"
                    if cosmetics.badge_capacity == 1
                    else "三格均已解锁，同一徽章不能重复占位；佩戴不消耗库存。",
                ),
                DispatchPanel(
                    "已拥有的徽章",
                    tuple(
                        DispatchLine(
                            self.labels.get(str(row["reward_id"]), str(row["reward_id"])),
                            "已拥有",
                            str(row["reward_id"]),
                        )
                        for row in rows[(page - 1) * 8 : page * 8]
                    ),
                    "没有徽章时，可从成就或周冲榜获得；此处不会显示未解锁隐藏奖励。",
                ),
            ),
            hints=(
                "/成就徽章 1 徽章名（或徽章ID）；第二、三位同理。",
                "/成就徽章 卸下 1；/成就徽章 查看 2 翻页。",
                "旧 /佩戴成就 更新徽章第1位；/取消佩戴成就 卸下全部外观。",
            ),
            page=page,
            page_count=page_count,
            achievement_title=cosmetics.title_id,
            achievement_frame=cosmetics.frame_id,
            achievement_badge=cosmetics.badge_name,
            achievement_badges=cosmetics.badge_ids,
            achievement_badge_capacity=cosmetics.badge_capacity,
        )
