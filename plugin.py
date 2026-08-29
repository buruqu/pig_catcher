"""MaiBot 抓猪插件第五轮显式命令入口。"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from re import escape
from typing import Any, cast
from uuid import uuid4

import tomlkit
from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, Command, HomeCard, MaiBotPlugin

from .pig_catcher.assets import AssetCatalogStorage
from .pig_catcher.commands import (
    MentionTarget,
    extract_command_identity,
    extract_mention_target,
    format_help,
    matched_group,
    parse_action_type,
    parse_admin_asset_grant,
    parse_admin_asset_selector,
    parse_admin_blacklist_query,
    parse_admin_coin_amount,
    parse_admin_target_arguments,
    parse_batch_cook_query,
    parse_batch_sale_query,
    parse_catalog_query,
    parse_food_inventory_query,
    parse_gift_query,
    parse_inventory_query,
    parse_item_use_query,
    parse_ledger_page,
    parse_purchase_query,
    parse_ranking_query,
    parse_records_page,
    parse_showcase_query,
    parse_store_query,
    parse_trade_id,
    parse_trade_list_query,
    parse_trade_offer_query,
    parse_upgrade_name,
)
from .pig_catcher.commands.battle import BATTLE_HELP, parse_battle_request
from .pig_catcher.commands.dispatch import DISPATCH_HELP, parse_dispatch_request
from .pig_catcher.commands.item_bag import ITEM_BAG_PATTERN, REWARD_COUPON_PATTERN, parse_item_bag_request
from .pig_catcher.commands.tour import TOUR_HELP, parse_tour_request
from .pig_catcher.config import AccessPolicy, PigCatcherConfig
from .pig_catcher.domain.achievements import AchievementReward
from .pig_catcher.domain.battle_views import BattleView
from .pig_catcher.domain.dispatch_views import DispatchLine, DispatchView
from .pig_catcher.domain.enums import AssetKind
from .pig_catcher.domain.errors import (
    CommandContextError,
    DomainValidationError,
    MentionTargetError,
    PigCatcherError,
)
from .pig_catcher.domain.feature_shop import (
    FEATURE_SHOP_PRODUCTS,
    FEATURE_SHOP_PRODUCTS_BY_NAME,
    FeatureShopSystem,
)
from .pig_catcher.domain.gameplay import ITEM_DEFINITIONS
from .pig_catcher.domain.models import (
    CommandIdentity,
    CommandReceipt,
    ScopeKey,
)
from .pig_catcher.domain.special_content import (
    TECHNIQUE_LAPSE_BLUE,
    TECHNIQUE_MALEVOLENT_KITCHEN,
    TECHNIQUE_REVERSAL_RED,
)
from .pig_catcher.domain.tour_views import TourView
from .pig_catcher.infrastructure import PigCatcherDatabase, safe_database_path
from .pig_catcher.plugin_metadata import PLUGIN_CONFIG_URL
from .pig_catcher.rendering import (
    AnimatedCardComposer,
    EconomyReceiptRowViewModel,
    EconomyReceiptViewModel,
    PigCatcherRenderer,
    RenderDelivery,
    RenderedImage,
    RenderOptions,
    achievement_backfill_summary_view,
    achievement_overview_view,
    achievement_page_view,
    achievement_ranking_view,
    achievement_unlock_view,
    batch_cook_view,
    batch_sale_receipt_view,
    catalog_media_paths,
    catalog_view,
    daily_giants_media_paths,
    daily_giants_view,
    eat_receipt_view,
    food_card_view,
    food_catalog_media_paths,
    food_catalog_view,
    food_inventory_media_paths,
    food_inventory_view,
    food_media_path,
    gift_receipt_view,
    group_event_eat_view,
    group_event_quota_reset_view,
    inventory_media_paths,
    inventory_view,
    is_special_event_food,
    item_receipt_view,
    ledger_view,
    media_path,
    pig_card_view,
    pig_media_path,
    profile_view,
    purchase_receipt_view,
    ranking_media_paths,
    ranking_view,
    records_view,
    roulette_event_view,
    sale_receipt_view,
    showcase_receipt_view,
    special_event_eat_view,
    store_view,
    technique_activation_view,
    technique_catch_event_view,
    trade_list_view,
    trade_receipt_view,
    weekly_competition_award_view,
    weekly_competition_view,
)
from .pig_catcher.rendering.cosmetics import COSMETIC_DEFINITIONS
from .pig_catcher.services import (
    AchievementService,
    AdministrationService,
    AnnouncementAdminService,
    AssetCatalogService,
    CatchQuotaResetService,
    CatchResult,
    CookingResult,
    EatConfirmationRequest,
    EatResult,
    EconomyService,
    FoodView,
    FrameworkService,
    GameplayService,
    MaintenanceOptions,
    MaintenanceRunner,
    PigView,
    ReceiptService,
    RegulationService,
    RestrictionAdminService,
    SocialService,
    WeeklyCompetitionService,
    format_achievement_unlocks,
    format_batch_cooking_summary,
    format_batch_sale_summary,
    format_catalog_summary,
    format_catch_summary,
    format_cooking_summary,
    format_daily_giants_summary,
    format_eat_summary,
    format_food_catalog_summary,
    format_food_detail_summary,
    format_food_inventory_summary,
    format_gift_summary,
    format_group_event_eat_summary,
    format_inventory_summary,
    format_item_action_summary,
    format_ledger_summary,
    format_pig_detail_summary,
    format_profile_summary,
    format_purchase_summary,
    format_ranking_summary,
    format_records_summary,
    format_sale_summary,
    format_showcase_summary,
    format_store_summary,
    format_trade_page_summary,
    format_trade_summary,
    format_weekly_award_summary,
    format_weekly_competition_summary,
    is_group_event_food,
    reward_label,
)
from .pig_catcher.services.battle import BattleResult, BattleService
from .pig_catcher.services.dispatch import DispatchResult, DispatchService
from .pig_catcher.services.item_bag import ItemBagService
from .pig_catcher.services.tour import TourService
from .pig_catcher.version import PLUGIN_VERSION

_PURCHASE_PRODUCT_NAMES = tuple(item.display_name for item in ITEM_DEFINITIONS) + tuple(
    product.display_name for product in FEATURE_SHOP_PRODUCTS
)
_PURCHASE_PRODUCT_PATTERN = "(?:" + "|".join(escape(name) for name in _PURCHASE_PRODUCT_NAMES) + ")"
_COMMAND_LEADING_MENTION_PATTERN = r"(?:\[CQ:at,qq=[^\],]+\]\s*|<@!?[^>\s]+>\s*|@\S+\s*)?"
_PURCHASE_COMMAND_PATTERN = (
    rf"^{_COMMAND_LEADING_MENTION_PATTERN}/购买"
    rf"(?:\s+(?P<arguments>{_PURCHASE_PRODUCT_PATTERN}(?:\s+.*?)?))?\s*$"
)
_UPGRADE_TARGET_PATTERN = r"(?:猪饲料|饲料|猪饲料升级|厨具|厨具升级)"
_UPGRADE_COMMAND_PATTERN = (
    rf"^{_COMMAND_LEADING_MENTION_PATTERN}/升级"
    rf"(?:\s+(?P<arguments>{_UPGRADE_TARGET_PATTERN}))?\s*$"
)


class PigCatcherPlugin(MaiBotPlugin):
    """Expose catching, cooking, collection, and economy commands."""

    config_model = PigCatcherConfig

    def __init__(self) -> None:
        super().__init__()
        self._database: PigCatcherDatabase | None = None
        self._storage: AssetCatalogStorage | None = None
        self._asset_service: AssetCatalogService | None = None
        self._framework_service: FrameworkService | None = None
        self._gameplay_service: GameplayService | None = None
        self._economy_service: EconomyService | None = None
        self._social_service: SocialService | None = None
        self._receipt_service: ReceiptService | None = None
        self._regulation_service: RegulationService | None = None
        self._quota_reset_service: CatchQuotaResetService | None = None
        self._administration_service: AdministrationService | None = None
        self._restriction_admin_service: RestrictionAdminService | None = None
        self._announcement_admin_service: AnnouncementAdminService | None = None
        self._achievement_service: AchievementService | None = None
        self._weekly_competition_service: WeeklyCompetitionService | None = None
        self._dispatch_service: DispatchService | None = None
        self._item_bag_service: ItemBagService | None = None
        self._tour_service: TourService | None = None
        self._battle_service: BattleService | None = None
        self._renderer: PigCatcherRenderer | None = None
        self._animation_composer: AnimatedCardComposer | None = None
        self._delivery: RenderDelivery | None = None
        self._maintenance: MaintenanceRunner | None = None

    def get_components(self) -> list[dict[str, Any]]:
        """Expose commands only to configured sessions during a dual-version rollout."""

        components = super().get_components()
        configured = self._plugin_config_instance
        allowed_sessions = (
            list(cast(PigCatcherConfig, configured).access.command_session_allowlist)
            if configured is not None
            else []
        )
        if not allowed_sessions:
            return components
        for component in components:
            if component.get("type") == "COMMAND":
                component["allowed_session"] = allowed_sessions.copy()
        return components

    @property
    def settings(self) -> PigCatcherConfig:
        """返回强类型配置，集中隔离 SDK 基类的宽类型。"""

        return cast(PigCatcherConfig, self.config)

    @property
    def database(self) -> PigCatcherDatabase | None:
        """供加载验收读取当前数据库状态。"""

        return self._database

    @property
    def renderer(self) -> PigCatcherRenderer | None:
        """供本地视觉验收调用，不注册群聊预览命令。"""

        return self._renderer

    @property
    def animation_composer(self) -> AnimatedCardComposer | None:
        """供后续抓取与图鉴卡片复用动画保真服务。"""

        return self._animation_composer

    async def _regulation_chat_messages(
        self,
        chat_id: str,
        start_time: float,
        end_time: float,
        limit: int,
    ) -> Sequence[Mapping[str, object]]:
        """Read bounded message metadata through the public MaiBot SDK."""

        result = await self.ctx.message.get_by_time_in_chat(
            chat_id,
            str(start_time),
            str(end_time),
            limit=int(limit),
            limit_mode="latest",
            filter_mai=True,
            filter_command=True,
            include_binary_data=False,
        )
        if not isinstance(result, list):
            raise RuntimeError("MaiBot 群消息查询没有返回列表。")
        return tuple(item for item in result if isinstance(item, Mapping))

    @property
    def gameplay_service(self) -> GameplayService | None:
        """Expose the active gameplay service for command-level acceptance."""

        return self._gameplay_service

    @property
    def economy_service(self) -> EconomyService | None:
        """Expose the active economy service for command-level acceptance."""

        return self._economy_service

    @property
    def social_service(self) -> SocialService | None:
        """Expose the active social service for command-level acceptance."""

        return self._social_service

    async def on_load(self) -> None:
        if self.settings.plugin.enabled:
            await self._open_runtime()
            await self._execute_pending_admin_operations(source="admin-panel-load")
        self.ctx.logger.info(
            "抓猪插件已加载，版本=%s，阶段=%s",
            PLUGIN_VERSION,
            self.settings.plugin.framework_phase,
        )

    async def on_unload(self) -> None:
        await self._close_runtime()
        self.ctx.logger.info("抓猪插件已卸载")

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, Any],
        version: str,
    ) -> None:
        del config_data
        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        if self.settings.plugin.enabled and self._has_pending_admin_operation():
            if self._database is None:
                await self._open_runtime()
            await self._execute_pending_admin_operations(source="admin-panel-save")
        await self._close_runtime()
        if self.settings.plugin.enabled:
            await self._open_runtime()
        self.ctx.logger.info("抓猪插件配置已更新，version=%s", version)

    async def _open_runtime(self) -> None:
        if self._database is not None:
            return
        settings = self.settings
        data_dir = Path(self.ctx.paths.data_dir).resolve()
        database_path = safe_database_path(
            data_dir,
            settings.storage.database_filename,
        )
        database = PigCatcherDatabase(
            database_path,
            busy_timeout_ms=settings.storage.sqlite_busy_timeout_ms,
            max_concurrent_reads=settings.storage.sqlite_read_concurrency,
        )
        storage = AssetCatalogStorage(data_dir)
        try:
            await database.open()
            storage.ensure_layout()
            asset_service = AssetCatalogService(
                database,
                storage,
                min_image_side=settings.assets.min_image_side,
                max_image_bytes=settings.assets.max_image_bytes,
                max_animation_frames=settings.assets.max_animation_frames,
                max_animation_duration_ms=settings.assets.max_animation_duration_ms,
            )
            renderer = PigCatcherRenderer(
                self.ctx.render,
                RenderOptions(
                    card_width=settings.rendering.card_width,
                    viewport_height=settings.rendering.viewport_height,
                    device_scale_factor=settings.rendering.device_scale_factor,
                    render_timeout_ms=settings.rendering.render_timeout_ms,
                    max_png_bytes=settings.rendering.max_png_bytes,
                    max_animation_bytes=settings.rendering.max_animation_bytes,
                    missing_frame_duration_ms=settings.rendering.missing_frame_duration_ms,
                    font_family=settings.rendering.font_family,
                    single_media_preview_max_side=settings.rendering.single_media_preview_max_side,
                    media_preview_cache_bytes=settings.rendering.media_preview_cache_bytes,
                    media_preview_disk_cache_bytes=settings.rendering.media_preview_disk_cache_bytes,
                    media_preprocess_concurrency=settings.rendering.media_preprocess_concurrency,
                ),
                preview_cache_root=data_dir / "cache" / "render-previews-v1",
            )
            animation_composer = AnimatedCardComposer(
                max_output_bytes=settings.rendering.max_animation_bytes,
                missing_frame_duration_ms=settings.rendering.missing_frame_duration_ms,
                max_working_memory_bytes=settings.rendering.max_animation_working_memory_bytes,
                max_concurrency=settings.rendering.animation_composition_concurrency,
            )
            maintenance = MaintenanceRunner(
                database,
                storage,
                data_dir,
                MaintenanceOptions(
                    interval_minutes=settings.maintenance.interval_minutes,
                    initial_delay_seconds=settings.maintenance.initial_delay_seconds,
                    full_check_interval_hours=settings.maintenance.full_check_interval_hours,
                    run_integrity_check=settings.maintenance.run_integrity_check,
                    auto_backup_enabled=settings.storage.auto_backup_enabled,
                    backup_interval_hours=settings.storage.backup_interval_hours,
                    backup_retention_count=settings.storage.backup_retention_count,
                    catalog_rollback_retention_count=(settings.storage.catalog_rollback_retention_count),
                    catalog_cleanup_grace_hours=(settings.storage.catalog_cleanup_grace_hours),
                    staging_max_age_hours=settings.assets.staging_max_age_hours,
                ),
                logger=self.ctx.logger,
            )
            self._database = database
            self._storage = storage
            self._asset_service = asset_service
            self._framework_service = FrameworkService(database)
            self._dispatch_service = DispatchService(database)
            self._item_bag_service = ItemBagService(database)
            self._tour_service = TourService(database)
            self._gameplay_service = GameplayService(
                database,
                settings.catching,
                ranking=settings.ranking,
            )
            self._economy_service = EconomyService(
                database,
                settings.cooking,
                settings.economy,
                catch_base_weights=settings.catching.weights(),
                quota_refresh_hours=settings.catching.quota_refresh_hours,
                quota_timezone_name=settings.catching.daily_reset_timezone,
            )
            regulation_service = RegulationService(
                database,
                settings.regulation,
                admin_user_ids=settings.access.admin_user_ids,
                chat_message_provider=self._regulation_chat_messages,
            )
            self._regulation_service = regulation_service
            self._battle_service = BattleService(
                database,
                catching=settings.catching,
                regulation_service=regulation_service,
                access_policy_factory=self._access_policy,
            )
            self._social_service = SocialService(
                database,
                settings.trading,
                settings.ranking,
                regulation_service=regulation_service,
            )
            self._receipt_service = ReceiptService(database)
            self._quota_reset_service = CatchQuotaResetService(
                database,
                refresh_hours=settings.catching.quota_refresh_hours,
                timezone_name=settings.catching.daily_reset_timezone,
                window_limit=settings.catching.daily_limit,
            )
            self._administration_service = AdministrationService(
                database,
                refresh_hours=settings.catching.quota_refresh_hours,
                timezone_name=settings.catching.daily_reset_timezone,
            )
            self._restriction_admin_service = RestrictionAdminService(database)
            self._announcement_admin_service = AnnouncementAdminService(database)
            achievement_service = AchievementService(database)
            await achievement_service.initialize()
            self._achievement_service = achievement_service
            if settings.features.weekly_competitions_enabled:
                weekly_competition_service = WeeklyCompetitionService(database)
                await weekly_competition_service.initialize()
                self._weekly_competition_service = weekly_competition_service
            self._renderer = renderer
            self._animation_composer = animation_composer
            self._delivery = RenderDelivery(
                self.ctx.send,
                logger=self.ctx.logger,
                fallback_to_text=settings.rendering.fallback_to_text,
                max_concurrent_deliveries=settings.rendering.max_concurrent_image_deliveries,
                max_concurrent_image_sends=settings.rendering.max_concurrent_image_sends,
                queue_timeout_ms=settings.rendering.image_delivery_queue_timeout_ms,
                image_send_queue_timeout_ms=settings.rendering.image_send_queue_timeout_ms,
                render_timeout_ms=settings.rendering.render_timeout_ms,
                image_send_timeout_ms=settings.rendering.image_send_timeout_ms,
                text_send_timeout_ms=settings.rendering.text_send_timeout_ms,
            )
            self._maintenance = maintenance
            if settings.maintenance.enabled:
                maintenance.start()
        except BaseException:
            await database.close()
            self._clear_runtime_references()
            raise

    async def _close_runtime(self) -> None:
        maintenance = self._maintenance
        database = self._database
        if maintenance is not None:
            await maintenance.stop()
        if database is not None:
            await database.close()
        self._clear_runtime_references()

    def _clear_runtime_references(self) -> None:
        if isinstance(getattr(self, "_renderer", None), PigCatcherRenderer):
            self._renderer.clear_art_cache()
        self._maintenance = None
        self._delivery = None
        self._animation_composer = None
        self._renderer = None
        self._quota_reset_service = None
        self._administration_service = None
        self._restriction_admin_service = None
        self._announcement_admin_service = None
        self._achievement_service = None
        self._weekly_competition_service = None
        self._dispatch_service = None
        self._item_bag_service = None
        self._tour_service = None
        self._battle_service = None
        self._receipt_service = None
        self._regulation_service = None
        self._economy_service = None
        self._social_service = None
        self._gameplay_service = None
        self._framework_service = None
        self._asset_service = None
        self._storage = None
        self._database = None

    def _has_pending_admin_operation(self) -> bool:
        return bool(
            self.settings.quota_administration.execute_current_window_reset
            or self.settings.blacklist_administration.execute_blacklist_update
            or self.settings.announcement_administration.execute_send
        )

    async def _execute_pending_admin_operations(self, *, source: str) -> None:
        """Consume panel triggers at most once, then execute and audit each request."""

        quota = self.settings.quota_administration
        blacklist = self.settings.blacklist_administration
        announcement = self.settings.announcement_administration
        run_quota = bool(quota.execute_current_window_reset)
        run_blacklist = bool(blacklist.execute_blacklist_update)
        run_announcement = bool(announcement.execute_send)
        if not (run_quota or run_blacklist or run_announcement):
            return

        # Reset every trigger before any external effect. A crash can lose a requested
        # operation, but it cannot silently repeat a punishment or announcement.
        self._clear_administration_triggers()
        data_dir = Path(self.ctx.paths.data_dir).resolve()

        if run_quota:
            try:
                service = self._quota_reset_service
                if service is None:
                    raise RuntimeError("抓猪额度重置服务尚未就绪。")
                platform = str(quota.platform or "").strip() or "qq"
                if quota.boost_window_limit > 0:
                    result = await service.apply_window_boost(
                        data_dir=data_dir,
                        scope_ids=[
                            ScopeKey(
                                platform=platform,
                                group_id=quota.group_id,
                            ).value
                        ],
                        limit_value=quota.boost_window_limit,
                        created_by="maibot-admin-panel",
                        reason=(f"控制面板提额至 {quota.boost_window_limit} 次/时段"),
                        source=source,
                    )
                    self.ctx.logger.info(
                        "抓猪额度已提升：scope=%s，window=%s，limit=%s，cleared=%s，players=%s，audit=%s，backup=%s",
                        ",".join(result.scope_ids),
                        result.window.label,
                        result.limit_value,
                        result.cleared_catches,
                        result.affected_players,
                        ",".join(result.audit_event_ids),
                        result.backup_path,
                    )
                else:
                    result = await service.backup_and_reset_current_window(
                        data_dir=data_dir,
                        group_id=quota.group_id,
                        platform=platform,
                        actor_user_id="maibot-admin-panel",
                        source=source,
                    )
                    self.ctx.logger.info(
                        "抓猪额度已精准重置：scope=%s，window=%s，cleared=%s，players=%s，audit=%s，backup=%s",
                        result.scope_id,
                        result.window.label,
                        result.cleared_catches,
                        result.affected_players,
                        result.audit_event_id,
                        result.backup_path,
                    )
            except Exception:
                self.ctx.logger.exception("控制面板抓猪额度重置失败；触发开关已关闭，不会自动重试")

        if run_blacklist:
            try:
                service = self._restriction_admin_service
                if service is None:
                    raise RuntimeError("社交黑名单管理服务尚未就绪。")
                action_map = {
                    "不操作": "none",
                    "加入黑名单": "add",
                    "解除黑名单": "remove",
                }
                result = await service.backup_and_update_social_blacklists(
                    data_dir=data_dir,
                    group_id=blacklist.group_id,
                    platform=blacklist.platform,
                    user_ids=blacklist.user_ids,
                    gift_action=action_map[blacklist.gift_action],
                    trade_action=action_map[blacklist.trade_action],
                    reason=blacklist.reason,
                    source=source,
                    created_by="maibot-admin-panel",
                )
                self.ctx.logger.info(
                    "社交黑名单已更新：scope=%s，players=%s，gift=%s/%s，trade=%s/%s，cancelled=%s，audit=%s，backup=%s",
                    result.scope_id,
                    len(result.player_ids),
                    result.gift_action,
                    result.gift_rows_changed,
                    result.trade_action,
                    result.trade_rows_changed,
                    result.cancelled_pending_trades,
                    result.audit_event_id,
                    result.backup_path,
                )
            except Exception:
                self.ctx.logger.exception("控制面板社交黑名单变更失败；触发开关已关闭，不会自动重试")

        if run_announcement:
            service = self._announcement_admin_service
            if service is None:
                self.ctx.logger.error("群公告发送服务尚未就绪；触发开关已关闭，不会自动重试")
                return
            try:
                claim = await service.claim(
                    group_id=announcement.group_id,
                    platform=announcement.platform,
                    content=announcement.content,
                    source=source,
                    created_by="maibot-admin-panel",
                )
            except Exception:
                self.ctx.logger.exception("控制面板群公告认领失败；触发开关已关闭，不会自动重试")
                return
            try:
                sent = await self._send_text_capability(claim.content, claim.stream_id)
            except Exception as exc:
                try:
                    await service.record_result(claim, success=False, error=str(exc))
                except Exception:
                    self.ctx.logger.exception("群公告发送失败后写入审计结果失败")
                self.ctx.logger.exception(
                    "控制面板群公告发送失败：scope=%s；QQ 官方机器人可能缺少 5 分钟内的被动回复上下文",
                    claim.scope_id,
                )
                return
            try:
                result_audit_id = await service.record_result(
                    claim,
                    success=sent,
                    error="" if sent else "MaiBot 发送接口返回 false",
                )
            except Exception:
                self.ctx.logger.exception(
                    "群公告发送后写入结果审计失败：scope=%s，announcement=%s",
                    claim.scope_id,
                    claim.announcement_id,
                )
                return
            if sent:
                self.ctx.logger.info(
                    "控制面板群公告已发送：scope=%s，stream=%s，announcement=%s，audit=%s",
                    claim.scope_id,
                    claim.stream_id,
                    claim.announcement_id,
                    result_audit_id,
                )
            else:
                self.ctx.logger.error(
                    "控制面板群公告未发送成功：scope=%s，announcement=%s，audit=%s；不会自动重试",
                    claim.scope_id,
                    claim.announcement_id,
                    result_audit_id,
                )

    @staticmethod
    def _clear_administration_triggers() -> None:
        """Atomically turn all one-shot admin switches off in config.toml."""

        config_path = Path(__file__).resolve().with_name("config.toml")
        temporary_path = config_path.with_name(f".{config_path.name}.{uuid4().hex}.tmp")
        document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
        trigger_fields = {
            "quota_administration": "execute_current_window_reset",
            "blacklist_administration": "execute_blacklist_update",
            "announcement_administration": "execute_send",
        }
        for section_name, field_name in trigger_fields.items():
            section = document.get(section_name)
            if section is not None:
                section[field_name] = False
        try:
            temporary_path.write_text(tomlkit.dumps(document), encoding="utf-8")
            os.replace(temporary_path, config_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @HomeCard(
        "pig_catcher_quota_control",
        title="抓猪运营管理",
        description="集中管理额度、社交黑名单、群公告与自动监管。",
        content=[
            {
                "type": "key_value",
                "entries": {
                    "每日刷新": "00:00 / 09:00 / 12:00 / 19:00",
                    "黑名单": "赠送/收赠与交易分别管理",
                    "自动监管": "以当前部署配置的作用域为准",
                    "群公告": "使用目标群最近活跃线路",
                },
            },
            {
                "type": "markdown",
                "content": (
                    "点击下方按钮进入插件配置。所有写操作均为一次性开关；"
                    "黑名单变更会先在线备份，公告失败不会自动重发；"
                    "监管案件可用 /猪管监管 查看并人工解除。"
                ),
            },
            {
                "type": "actions",
                "actions": [
                    {
                        "label": "打开运营控制",
                        "url": PLUGIN_CONFIG_URL,
                    }
                ],
            },
        ],
        link_url=PLUGIN_CONFIG_URL,
        link_label="打开运营控制",
        icon="shield-check",
        width="medium",
        order=140,
    )
    async def home_quota_control(self) -> None:
        """Declare the safe catch-quota reset entry on the MaiBot home page."""

        return None

    def _access_policy(self) -> AccessPolicy:
        settings = self.settings.access
        return AccessPolicy(
            group_whitelist=settings.group_whitelist,
            group_blacklist=settings.group_blacklist,
            user_whitelist=settings.user_whitelist,
            user_blacklist=settings.user_blacklist,
            denied_message=settings.denied_message,
        )

    def _recipient_identity(
        self,
        actor: CommandIdentity,
        target: MentionTarget,
    ) -> CommandIdentity:
        decision = self._access_policy().evaluate(
            group_id=actor.scope.group_id,
            user_id=target.user_id,
        )
        if not decision.allowed:
            raise MentionTargetError("被提及成员未启用抓猪插件，不能接收资产或报价。")
        return CommandIdentity(
            scope=actor.scope,
            stream_id=actor.stream_id,
            user_id=target.user_id,
            display_name=target.display_name,
            group_name=actor.group_name,
        )

    def _is_configured_admin(self, identity: CommandIdentity) -> bool:
        return self._access_policy().is_admin(
            platform=identity.scope.platform,
            user_id=identity.user_id,
            admin_user_ids=self.settings.access.admin_user_ids,
        )

    async def _is_operationally_blacklisted(self, identity: CommandIdentity) -> bool:
        """Apply the current-group DB blacklist while keeping configured admins recoverable."""

        if self._is_configured_admin(identity):
            return False
        service = self._administration_service
        if service is None:
            return False
        return await service.is_plugin_access_banned(
            scope_id=identity.scope.value,
            platform_user_id=identity.user_id,
        )

    async def _prepare_admin_command(
        self,
        stream_id: str,
        kwargs: dict[str, Any],
    ) -> tuple[CommandIdentity | None, tuple[bool, str, int] | None]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=True,
            feature_label="插件管理",
        )
        if rejected is not None or identity is None:
            return identity, rejected
        if not self._is_configured_admin(identity):
            self.ctx.logger.warning(
                "拒绝非管理员执行猪管命令：platform=%s，scope=%s，user_id=%s",
                identity.scope.platform,
                identity.scope.value,
                identity.user_id,
            )
            return None, await self._reply_text(
                identity.stream_id,
                "只有插件配置中的管理员可以使用猪管命令。QQ 官方机器人请配置成员 OpenID。",
                success=False,
            )
        if self._administration_service is None:
            return None, await self._reply_text(
                identity.stream_id,
                "抓猪管理服务尚未就绪，请稍后再试。",
                success=False,
            )
        return identity, None

    @staticmethod
    def _optional_mention_target(kwargs: dict[str, Any]) -> MentionTarget | None:
        try:
            return extract_mention_target(kwargs)
        except MentionTargetError as exc:
            if "一次只能" in str(exc):
                raise
            return None

    @staticmethod
    def _admin_kind(value: str) -> AssetKind:
        normalized = str(value or "").strip()
        if normalized in {"猪猪", "猪"}:
            return AssetKind.PIG
        if normalized in {"美食", "菜"}:
            return AssetKind.FOOD
        raise ValueError(f"未知管理员资产类别：{value}")

    def _target_is_configured_admin(
        self,
        identity: CommandIdentity,
        target_user_id: str,
    ) -> bool:
        normalized = str(target_user_id or "").strip()
        scope_prefix = f"{identity.scope.value}:"
        platform_prefix = f"{identity.scope.platform}:"
        if normalized.startswith(scope_prefix):
            normalized = normalized[len(scope_prefix) :]
        elif normalized.startswith(platform_prefix):
            normalized = normalized[len(platform_prefix) :]
        return self._access_policy().is_admin(
            platform=identity.scope.platform,
            user_id=normalized,
            admin_user_ids=self.settings.access.admin_user_ids,
        )

    async def _reply_text(
        self,
        stream_id: str,
        text: str,
        *,
        success: bool,
    ) -> tuple[bool, str, int]:
        if not stream_id:
            return False, text, 1
        try:
            sent = await self._send_text_capability(text, stream_id)
        except TimeoutError:
            self.ctx.logger.warning(
                "抓猪纯文字回复超时（%s ms）：stream=%s",
                self.settings.rendering.text_send_timeout_ms,
                stream_id,
            )
            sent = False
        except Exception:
            self.ctx.logger.exception("抓猪纯文字回复失败：stream=%s", stream_id)
            sent = False
        delivered = sent and success
        return delivered, text, 2 if delivered else 1

    async def _send_text_capability(self, text: str, stream_id: str) -> bool:
        """Bound one host text capability call so a command stays below Runner RPC timeout."""

        return bool(
            await asyncio.wait_for(
                self.ctx.send.text(text, stream_id),
                timeout=self.settings.rendering.text_send_timeout_ms / 1000,
            )
        )

    async def _prepare_command(
        self,
        stream_id: str,
        kwargs: dict[str, Any],
        *,
        feature_enabled: bool,
        feature_label: str,
    ) -> tuple[CommandIdentity | None, tuple[bool, str, int] | None]:
        if not self.settings.plugin.enabled:
            rejected = await self._reply_text(
                stream_id,
                "抓猪插件当前已在管理面板中停用。",
                success=False,
            )
            return None, rejected
        try:
            identity = extract_command_identity(stream_id, kwargs)
        except CommandContextError as exc:
            rejected = await self._reply_text(stream_id, str(exc), success=False)
            return None, rejected
        decision = self._access_policy().evaluate(
            group_id=identity.scope.group_id,
            user_id=identity.user_id,
        )
        if not decision.allowed:
            if self.settings.access.notify_denied:
                rejected = await self._reply_text(
                    identity.stream_id,
                    decision.reason,
                    success=False,
                )
                return None, rejected
            return None, (False, "", 0)
        if await self._is_operationally_blacklisted(identity):
            if self.settings.access.notify_denied:
                rejected = await self._reply_text(
                    identity.stream_id,
                    self.settings.access.denied_message,
                    success=False,
                )
                return None, rejected
            return None, (False, "", 0)
        regulation = self._regulation_service
        if regulation is not None:
            hold = await regulation.active_plugin_hold(identity)
            if hold is not None:
                if self.settings.access.notify_denied:
                    rejected = await self._reply_text(
                        identity.stream_id,
                        hold.public_message,
                        success=False,
                    )
                    return None, rejected
                return None, (False, "", 0)
        if not feature_enabled:
            rejected = await self._reply_text(
                identity.stream_id,
                f"管理面板已关闭“{feature_label}”功能。",
                success=False,
            )
            return None, rejected
        if self._gameplay_service is None:
            rejected = await self._reply_text(
                identity.stream_id,
                "抓猪玩法服务尚未就绪，请稍后再试。",
                success=False,
            )
            return None, rejected
        return identity, None

    async def _deliver_player_status(
        self,
        identity: CommandIdentity,
        view: EconomyReceiptViewModel,
        *,
        fallback_text: str,
        receipt: CommandReceipt | None = None,
    ) -> tuple[bool, str, int]:
        """Render a completed setting/reward, never rerun its business action."""

        async def render() -> RenderedImage:
            return await cast(PigCatcherRenderer, self._renderer).render_economy_receipt(view)

        if receipt is not None:
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=receipt,
                render=render,
                fallback_text=fallback_text,
            )
        return await self._deliver_query(
            stream_id=identity.stream_id,
            render=render,
            fallback_text=fallback_text,
        )

    async def _deliver_query(
        self,
        *,
        stream_id: str,
        render: Callable[[], Awaitable[RenderedImage]],
        fallback_text: str,
        activity_identity: CommandIdentity | None = None,
    ) -> tuple[bool, str, int]:
        if (
            activity_identity is not None
            and self._achievement_service is not None
            and self.settings.features.achievements_enabled
        ):
            try:
                await self._achievement_service.process_activity_facts(
                    scope_id=activity_identity.scope.value, receipt_id=f"activity-query:{activity_identity.message_id}"
                )
            except Exception:
                self.ctx.logger.exception("活动成就补录失败，事实仍保留，业务查询继续")
        if self._delivery is None:
            result = await self._reply_text(
                stream_id,
                fallback_text,
                success=True,
            )
        else:
            sent = await self._delivery.send_image_or_text(
                stream_id=stream_id,
                render=render,
                fallback_text=fallback_text,
                rendering_enabled=self.settings.rendering.enabled,
            )
            result = sent, fallback_text, 2 if sent else 1
        if result[0] and activity_identity is not None:
            await self._deliver_activity_notifications(
                stream_id, activity_identity.scope.value, f"activity-query:{activity_identity.message_id}"
            )
        return result

    async def _deliver_receipt(
        self,
        *,
        stream_id: str,
        receipt: CommandReceipt,
        render: Callable[[], Awaitable[RenderedImage]],
        fallback_text: str,
        track_progress: bool = True,
    ) -> tuple[bool, str, int]:
        receipts = self._receipt_service
        if receipts is None:
            return False, "抓猪回执服务尚未就绪。", 1
        if track_progress:
            await self._process_achievement_receipt(receipt)
            await self._process_weekly_competition_receipt(receipt)
        if not await receipts.claim_send(receipt.receipt_id):
            if track_progress:
                await self._deliver_achievement_notifications(
                    stream_id=stream_id,
                    receipt=receipt,
                )
                await self._deliver_achievement_backfill_summary(
                    stream_id=stream_id,
                    player_id=receipt.player_id,
                )
                await self._deliver_weekly_competition_award(
                    stream_id=stream_id,
                    player_id=receipt.player_id,
                )
            return True, "该消息已处理，不重复公示。", 0
        if self._delivery is None:
            try:
                sent = await self._send_text_capability(fallback_text, stream_id)
            except Exception:
                sent = False
                self.ctx.logger.exception("抓猪回执纯文字发送失败")
        else:
            sent = await self._delivery.send_image_or_text(
                stream_id=stream_id,
                render=render,
                fallback_text=fallback_text,
                rendering_enabled=self.settings.rendering.enabled,
            )
        if sent:
            marked = await receipts.mark_sent(receipt.receipt_id)
            if not marked:
                self.ctx.logger.error(
                    "抓猪回执已发送但无法标记完成，receipt_id=%s",
                    receipt.receipt_id,
                )
            if track_progress:
                await self._deliver_achievement_notifications(
                    stream_id=stream_id,
                    receipt=receipt,
                )
                await self._deliver_achievement_backfill_summary(
                    stream_id=stream_id,
                    player_id=receipt.player_id,
                )
                await self._deliver_weekly_competition_award(
                    stream_id=stream_id,
                    player_id=receipt.player_id,
                )
            return True, fallback_text, 2
        await receipts.mark_failed(
            receipt.receipt_id,
            "图片和纯文字发送均未成功",
        )
        return False, fallback_text, 1

    async def _deliver_text_receipt(
        self,
        *,
        stream_id: str,
        receipt: CommandReceipt,
    ) -> tuple[bool, str, int]:
        """只发送一次已经提交的纯文字管理回执。"""

        receipts = self._receipt_service
        if receipts is None:
            return False, "抓猪回执服务尚未就绪。", 1
        await self._process_achievement_receipt(receipt)
        await self._process_weekly_competition_receipt(receipt)
        if not await receipts.claim_send(receipt.receipt_id):
            await self._deliver_achievement_notifications(
                stream_id=stream_id,
                receipt=receipt,
            )
            await self._deliver_achievement_backfill_summary(
                stream_id=stream_id,
                player_id=receipt.player_id,
            )
            await self._deliver_weekly_competition_award(
                stream_id=stream_id,
                player_id=receipt.player_id,
            )
            return True, "该消息已处理，不重复公示。", 0
        try:
            sent = await self._send_text_capability(receipt.text_summary, stream_id)
        except Exception as exc:
            await receipts.mark_failed(receipt.receipt_id, str(exc))
            raise
        if sent:
            marked = await receipts.mark_sent(receipt.receipt_id)
            if not marked:
                self.ctx.logger.error(
                    "抓猪纯文字回执已发送但无法标记完成，receipt_id=%s",
                    receipt.receipt_id,
                )
            await self._deliver_achievement_notifications(
                stream_id=stream_id,
                receipt=receipt,
            )
            await self._deliver_achievement_backfill_summary(
                stream_id=stream_id,
                player_id=receipt.player_id,
            )
            await self._deliver_weekly_competition_award(
                stream_id=stream_id,
                player_id=receipt.player_id,
            )
            return True, receipt.text_summary, 2
        await receipts.mark_failed(receipt.receipt_id, "纯文字发送未成功")
        return False, receipt.text_summary, 1

    async def _process_achievement_receipt(self, receipt: CommandReceipt) -> None:
        """Consume the committed receipt as an idempotent achievement event."""

        service = self._achievement_service
        if service is None or not receipt.player_id or not self.settings.features.achievements_enabled:
            return
        try:
            await service.process_receipt(receipt)
        except Exception:
            # The main command is already committed.  Keep its delivery healthy;
            # the unchanged receipt remains a durable retry source.
            self.ctx.logger.exception(
                "成就事件处理失败，等待同一业务回执重试：receipt=%s",
                receipt.receipt_id,
            )

    async def _process_weekly_competition_receipt(self, receipt: CommandReceipt) -> None:
        """Consume one committed receipt as an idempotent weekly score entry."""

        service = self._weekly_competition_service
        if service is None or not receipt.player_id or not self.settings.features.weekly_competitions_enabled:
            return
        try:
            await service.process_receipt(receipt)
        except Exception:
            # The business receipt is already committed and remains a durable
            # source for the next backfill/query, so scoring cannot break play.
            self.ctx.logger.exception(
                "周冲榜计分失败，等待回执补录：receipt=%s",
                receipt.receipt_id,
            )

    async def _deliver_achievement_notifications(
        self,
        *,
        stream_id: str,
        receipt: CommandReceipt,
    ) -> None:
        service = self._achievement_service
        if service is None or not receipt.player_id or not self.settings.features.achievements_enabled:
            return
        await self._deliver_activity_notifications(stream_id, receipt.scope_id, receipt.receipt_id)

    async def _deliver_activity_notifications(self, stream_id: str, scope_id: str, receipt_id: str) -> None:
        service = self._achievement_service
        if service is None or not self.settings.features.achievements_enabled:
            return
        players = await service.notification_players(scope_id=scope_id, receipt_id=receipt_id)
        for player_id in players:
            await self._deliver_player_achievement_notifications(stream_id, player_id, receipt_id)

    async def _deliver_player_achievement_notifications(self, stream_id: str, player_id: str, receipt_id: str) -> None:
        service = cast(AchievementService, self._achievement_service)
        unlocks = await service.pending_unlocks(player_id=player_id, receipt_id=receipt_id)
        if not unlocks:
            return
        unlock_ids = await service.claim_notifications(
            player_id=player_id,
            receipt_id=receipt_id,
        )
        if not unlock_ids:
            return
        display_name = await service.player_display_name(player_id)
        renderer = cast(PigCatcherRenderer, self._renderer)
        fallback = display_name + "\n" + format_achievement_unlocks(unlocks)
        try:
            if self._delivery is None:
                sent = await self._send_text_capability(fallback, stream_id)
            else:
                sent = await self._delivery.send_image_or_text(
                    stream_id=stream_id,
                    render=lambda: renderer.render_achievement_unlock(achievement_unlock_view(display_name, unlocks)),
                    fallback_text=fallback,
                    rendering_enabled=self.settings.rendering.enabled,
                )
        except Exception as exc:
            await service.mark_notifications(unlock_ids, sent=False, error=str(exc))
            self.ctx.logger.exception("成就解锁卡投递失败")
            return
        await service.mark_notifications(
            unlock_ids,
            sent=bool(sent),
            error="" if sent else "图片和文字均未发送成功",
        )

    async def _deliver_achievement_backfill_summary(
        self,
        *,
        stream_id: str,
        player_id: str | None,
    ) -> None:
        service = self._achievement_service
        if service is None or not player_id or not self.settings.features.achievements_enabled:
            return
        claimed = await service.claim_backfill_summary(player_id=player_id)
        if claimed is None:
            return
        unlock_ids, summary = claimed
        fallback = (
            "【PiG Dream! 历史成就结算】\n"
            f"{summary.display_name} 一次性解锁 {summary.unlocked_count} 项，"
            f"获得 {summary.total_points} 成就点。\n"
            "奖励：" + ("、".join(reward_label(item) for item in summary.rewards) or "成就点")
        )
        renderer = cast(PigCatcherRenderer, self._renderer)
        try:
            if self._delivery is None:
                sent = await self._send_text_capability(fallback, stream_id)
            else:
                sent = await self._delivery.send_image_or_text(
                    stream_id=stream_id,
                    render=lambda: renderer.render_achievement_backfill_summary(
                        achievement_backfill_summary_view(summary)
                    ),
                    fallback_text=fallback,
                    rendering_enabled=self.settings.rendering.enabled,
                )
        except Exception as exc:
            await service.mark_notifications(unlock_ids, sent=False, error=str(exc))
            self.ctx.logger.exception("历史成就汇总卡投递失败")
            return
        await service.mark_notifications(
            unlock_ids,
            sent=bool(sent),
            error="" if sent else "图片和文字均未发送成功",
        )

    async def _deliver_weekly_competition_award(
        self,
        *,
        stream_id: str,
        player_id: str | None,
    ) -> None:
        service = self._weekly_competition_service
        if service is None or not player_id or not self.settings.features.weekly_competitions_enabled:
            return
        award = await service.claim_pending_award(player_id=player_id)
        if award is None:
            return
        fallback = format_weekly_award_summary(award)
        renderer = cast(PigCatcherRenderer, self._renderer)
        try:
            if self._delivery is None:
                sent = await self._send_text_capability(fallback, stream_id)
            else:
                sent = await self._delivery.send_image_or_text(
                    stream_id=stream_id,
                    render=lambda: renderer.render_weekly_competition_award(weekly_competition_award_view(award)),
                    fallback_text=fallback,
                    rendering_enabled=self.settings.rendering.enabled,
                )
        except Exception as exc:
            await service.mark_award_notification(award.award_id, sent=False, error=str(exc))
            self.ctx.logger.exception("周冲榜结算卡投递失败")
            return
        await service.mark_award_notification(
            award.award_id,
            sent=bool(sent),
            error="" if sent else "图片和文字均未发送成功",
        )

    async def _deliver_regulation_notices(
        self,
        *,
        stream_id: str,
        notice_ids: tuple[str, ...],
    ) -> None:
        """逐条投递中性提醒；只有真实发送成功才启动后续升级计数。"""

        service = self._regulation_service
        if service is None:
            return
        for notice_id in dict.fromkeys(notice_ids):
            notice = await service.claim_notice(notice_id)
            if notice is None:
                continue
            try:
                sent = await self._send_text_capability(notice.message_text, stream_id)
            except Exception as exc:
                await service.mark_notice_failed(notice.notice_id, str(exc))
                self.ctx.logger.exception(
                    "自动监管提醒发送失败：case=%s，notice=%s",
                    notice.case_id,
                    notice.notice_id,
                )
                continue
            if sent:
                if not await service.mark_notice_sent(notice.notice_id):
                    self.ctx.logger.error(
                        "自动监管提醒已发送但无法标记完成：case=%s，notice=%s",
                        notice.case_id,
                        notice.notice_id,
                    )
            else:
                await service.mark_notice_failed(notice.notice_id, "纯文字发送未成功")

    async def _render_pig_card(
        self,
        pig: PigView,
        *,
        mode_label: str,
        catch: CatchResult | None = None,
    ) -> RenderedImage:
        renderer = self._renderer
        if renderer is None:
            raise RuntimeError("抓猪渲染器尚未就绪。")
        view = pig_card_view(pig, mode_label=mode_label, catch=catch)
        if self._achievement_service is not None and (
            self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
        ):
            cosmetics = await self._achievement_service.cosmetics_for_player(pig.owner_player_id)
            view = replace(
                view,
                achievement_title=cosmetics.title_id,
                achievement_frame=cosmetics.frame_id,
                achievement_badge=cosmetics.badge_name,
                achievement_badges=cosmetics.badge_ids,
                achievement_badge_capacity=cosmetics.badge_capacity,
            )
        data_dir = Path(self.ctx.paths.data_dir).resolve()
        source_path = pig_media_path(data_dir, pig)
        if pig.media_visible and pig.is_animated and source_path is not None and source_path.is_file():
            composer = self._animation_composer
            if composer is None:
                raise RuntimeError("抓猪动画合成器尚未就绪。")
            base = await renderer.render_pig_card_base(view)
            return await composer.compose(
                base=base.image,
                source_path=source_path,
                slot=replace(base.media_slot, fit=pig.image_fit),
            )
        return await renderer.render_static_pig_card(view, source_path)

    async def _send_image_file(
        self,
        stream_id: str,
        relative_path: str,
    ) -> bool:
        """Send one on-disk image file as a standalone message."""

        data_dir = Path(self.ctx.paths.data_dir).resolve()
        try:
            source_path = media_path(data_dir, relative_path)
        except Exception:
            self.ctx.logger.exception("备用图片路径解析失败：%s", relative_path)
            return False
        if not source_path.is_file():
            return False
        try:
            payload = source_path.read_bytes()
            encoded = base64.b64encode(payload).decode("ascii")
            return bool(
                await asyncio.wait_for(
                    self.ctx.send.image(encoded, stream_id),
                    timeout=self.settings.rendering.image_send_timeout_ms / 1000,
                )
            )
        except TimeoutError:
            self.ctx.logger.warning(
                "备用图片独立发送等待超时（%s ms）；发送结果不确定，不再重试：%s",
                self.settings.rendering.image_send_timeout_ms,
                relative_path,
            )
            return False
        except Exception:
            self.ctx.logger.exception("备用图片独立发送失败：%s", relative_path)
            return False

    async def _render_food_card(
        self,
        food: FoodView,
        *,
        mode_label: str,
        cooking: CookingResult | None = None,
    ) -> RenderedImage:
        renderer = self._renderer
        if renderer is None:
            raise RuntimeError("美食渲染器尚未就绪。")
        view = food_card_view(food, mode_label=mode_label, cooking=cooking)
        if self._achievement_service is not None and (
            self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
        ):
            cosmetics = await self._achievement_service.cosmetics_for_player(food.owner_player_id)
            view = replace(
                view,
                achievement_title=cosmetics.title_id,
                achievement_frame=cosmetics.frame_id,
                achievement_badge=cosmetics.badge_name,
                achievement_badges=cosmetics.badge_ids,
                achievement_badge_capacity=cosmetics.badge_capacity,
            )
        data_dir = Path(self.ctx.paths.data_dir).resolve()
        source_path = food_media_path(data_dir, food)
        if food.media_visible and food.is_animated and source_path is not None and source_path.is_file():
            composer = self._animation_composer
            if composer is None:
                raise RuntimeError("美食动画合成器尚未就绪。")
            base = await renderer.render_food_card_base(view)
            return await composer.compose(
                base=base.image,
                source_path=source_path,
                slot=replace(base.media_slot, fit=food.image_fit),
            )
        return await renderer.render_static_food_card(view, source_path)

    async def _command_error(
        self,
        *,
        stream_id: str,
        operation: str,
        error: Exception,
    ) -> tuple[bool, str, int]:
        if isinstance(error, PigCatcherError):
            return await self._reply_text(stream_id, str(error), success=False)
        self.ctx.logger.exception("%s命令处理失败", operation)
        return await self._reply_text(
            stream_id,
            f"{operation}暂时不可用，请稍后再试。",
            success=False,
        )

    @Command(
        "pig_catcher_help",
        description="查看抓猪插件纯文字指令帮助",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/抓猪帮助(?:\s+(?P<topic>\S+))?\s*$",
    )
    async def handle_help(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        """返回纯文字帮助，不触发图片渲染和任何玩法状态。"""

        if not self.settings.plugin.enabled:
            return await self._reply_text(
                stream_id,
                "抓猪插件当前已在管理面板中停用。",
                success=False,
            )
        try:
            identity = extract_command_identity(stream_id, kwargs)
            decision = self._access_policy().evaluate(
                group_id=identity.scope.group_id,
                user_id=identity.user_id,
            )
            if not decision.allowed:
                if self.settings.access.notify_denied:
                    return await self._reply_text(
                        identity.stream_id,
                        decision.reason,
                        success=False,
                    )
                return False, "", 0
            if await self._is_operationally_blacklisted(identity):
                if self.settings.access.notify_denied:
                    return await self._reply_text(
                        identity.stream_id,
                        self.settings.access.denied_message,
                        success=False,
                    )
                return False, "", 0
            if not self.settings.features.help_enabled:
                return await self._reply_text(
                    identity.stream_id,
                    "管理面板已关闭“抓猪帮助”功能。",
                    success=False,
                )
            text = format_help(matched_group(kwargs, "topic"), settings=self.settings)
            return await self._reply_text(identity.stream_id, text, success=True)
        except CommandContextError as exc:
            return await self._reply_text(stream_id, str(exc), success=False)
        except Exception:
            self.ctx.logger.exception("抓猪帮助命令处理失败")
            return await self._reply_text(
                stream_id,
                "抓猪帮助暂时不可用，请稍后再试。",
                success=False,
            )

    @Command(
        "pig_catcher_reset_quota",
        description="由插件管理员重置当前群的本时段抓猪次数",
        pattern=r"^/重置\s*$",
    )
    async def handle_reset_quota(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        """让已配置的插件管理员审计式重置命令所在群的当前额度。"""

        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.catching_enabled,
            feature_label="抓猪",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        if not self._access_policy().is_admin(
            platform=identity.scope.platform,
            user_id=identity.user_id,
            admin_user_ids=self.settings.access.admin_user_ids,
        ):
            self.ctx.logger.warning(
                "拒绝非管理员执行抓猪额度重置：platform=%s，scope=%s，user_id=%s",
                identity.scope.platform,
                identity.scope.value,
                identity.user_id,
            )
            return await self._reply_text(
                identity.stream_id,
                "只有插件配置中的管理员可以使用 /重置。QQ 官方机器人请配置成员 OpenID，不能用数字 QQ 号代替。",
                success=False,
            )
        try:
            service = cast(CatchQuotaResetService, self._quota_reset_service)
            result = await service.backup_and_reset_from_command(
                data_dir=Path(self.ctx.paths.data_dir).resolve(),
                identity=identity,
            )
            if result.receipt is None:
                raise RuntimeError("群内额度重置没有生成幂等回执。")
            self.ctx.logger.info(
                "群内抓猪额度已精准重置：scope=%s，window=%s，cleared=%s，players=%s，audit=%s，backup=%s",
                result.scope_id,
                result.window.label,
                result.cleared_catches,
                result.affected_players,
                result.audit_event_id,
                result.backup_path,
            )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="重置抓猪次数",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_help",
        description="查看仅限插件管理员使用的猪管命令",
        pattern=r"^/猪管帮助\s*$",
    )
    async def handle_admin_help(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        text = "\n".join(
            (
                "【抓猪插件·管理员指令】",
                "/猪管发币 <@玩家|用户ID> <数量>",
                "/猪管全员发币 <数量>",
                "/猪管扣币 <@玩家|用户ID> <数量>（余额可为负）",
                "/猪管全员扣币 <数量>（余额可为负）",
                "/猪管发猪 <@玩家|用户ID> <猪名或模板ID> [4-16位字母数字编号]",
                "/猪管发菜 <@玩家|用户ID> <美食名或模板ID> [4-16位字母数字编号]",
                "/猪管删猪 <@玩家|用户ID> <猪名#编号>",
                "/猪管删菜 <@玩家|用户ID> <美食名#编号>",
                "/猪管黑名单",
                "/猪管黑名单 <加入|移除> <插件|赠送|交易> <@玩家|用户ID> [原因]",
                "/猪管监管 [案件号]",
                "/猪管监管解除 <案件号> [原因]",
                "/猪管重置玩家 <@玩家|用户ID>",
                "/猪管重置比划 <@玩家|用户ID|全员>（主动与被挑战机会一起重置）",
                "",
                "所有操作只作用于当前群；全员指当前群已登记玩家。",
                "管理员发放资产不增加抓猪/做菜统计；删除保留历史实例与图鉴。",
            )
        )
        return await self._reply_text(identity.stream_id, text, success=True)

    @Command(
        "pig_catcher_admin_regulation",
        description="插件管理员查看当前群自动监管案件",
        pattern=r"^/猪管监管(?:\s+(?P<case_id>\S+))?\s*$",
    )
    async def handle_admin_regulation(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        service = self._regulation_service
        if service is None:
            return await self._reply_text(
                identity.stream_id,
                "自动监管服务尚未就绪，请稍后再试。",
                success=False,
            )
        try:
            status_labels = {
                "watching": "已提醒观察",
                "supervised": "监管中",
                "social-restricted": "社交功能临时限制",
                "plugin-restricted": "插件临时限制",
                "closed": "已关闭",
                "dismissed": "已人工解除",
            }
            case_id = matched_group(kwargs, "case_id").strip()
            enabled_text = (
                "已启用" if service.scope_enabled(identity.scope.value) else "未启用（不会新建案件或自动限制）"
            )
            if not case_id:
                cases = await service.list_cases(scope_id=identity.scope.value)
                lines = [
                    "【猪管·自动监管】",
                    f"当前群：{identity.scope.value}",
                    f"状态：{enabled_text}",
                ]
                if not cases:
                    lines.append("当前没有监管案件。")
                for item in cases:
                    lines.append(
                        f"{item.case_id[:8]}｜{status_labels.get(item.status, item.status)}｜"
                        f"相关账号 {item.member_count}｜生效限制 {item.active_hold_count}｜"
                        f"更新 {item.updated_at}"
                    )
                lines.append("查看：/猪管监管 <案件号前缀>")
                return await self._reply_text(
                    identity.stream_id,
                    "\n".join(lines),
                    success=True,
                )
            detail = await service.case_detail(
                scope_id=identity.scope.value,
                case_id_prefix=case_id,
            )
            summary = detail.summary
            lines = [
                "【猪管·监管案件】",
                f"案件号：{summary.case_id[:12]}",
                f"状态：{status_labels.get(summary.status, summary.status)}",
                f"最近更新：{summary.updated_at}",
                "相关账号：",
            ]
            lines.extend(f"- {row['display_name']}（{row['platform_user_id']}）" for row in detail.members)
            active_holds = [row for row in detail.holds if row["status"] == "active"]
            if active_holds:
                lines.append("当前临时限制：")
                lines.extend(
                    f"- {row['display_name']}｜"
                    f"{'插件' if row['hold_type'] == 'plugin' else '赠送与交易'}｜"
                    f"至 {row['expires_at']}"
                    for row in active_holds
                )
            lines.append("人工解除：/猪管监管解除 <案件号> [原因]")
            return await self._reply_text(
                identity.stream_id,
                "\n".join(lines),
                success=True,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="查看自动监管案件",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_regulation_release",
        description="插件管理员人工解除当前群监管案件和临时限制",
        pattern=(
            r"^/猪管监管解除\s+(?P<case_id>\S+)"
            r"(?:\s+(?P<reason>.*?))?\s*$"
        ),
    )
    async def handle_admin_regulation_release(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        service = self._regulation_service
        if service is None:
            return await self._reply_text(
                identity.stream_id,
                "自动监管服务尚未就绪，请稍后再试。",
                success=False,
            )
        try:
            case_id = matched_group(kwargs, "case_id").strip()
            reason = matched_group(kwargs, "reason").strip() or "管理员人工复核解除"
            result = await service.release_case(
                identity=identity,
                case_id_prefix=case_id,
                reason=reason,
            )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="解除自动监管案件",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_grant_coins",
        description="插件管理员给当前群一名玩家发放猪币",
        pattern=r"^/猪管发币(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_admin_grant_coins(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._handle_admin_target_coin_adjustment(
            stream_id,
            kwargs,
            deduct=False,
        )

    @Command(
        "pig_catcher_admin_deduct_coins",
        description="插件管理员扣除当前群一名玩家的猪币，允许负余额",
        pattern=r"^/猪管扣币(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_admin_deduct_coins(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._handle_admin_target_coin_adjustment(
            stream_id,
            kwargs,
            deduct=True,
        )

    async def _handle_admin_target_coin_adjustment(
        self,
        stream_id: str,
        kwargs: dict[str, Any],
        *,
        deduct: bool,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            mention = self._optional_mention_target(kwargs)
            target = parse_admin_target_arguments(
                matched_group(kwargs, "arguments"),
                mentioned_user_id=mention.user_id if mention is not None else "",
                mentioned_display_name=(mention.display_name if mention is not None else ""),
            )
            amount = parse_admin_coin_amount(target.remaining)
            result = await cast(
                AdministrationService,
                self._administration_service,
            ).adjust_coins(
                identity,
                command_name="pig-catcher.admin-coins",
                amount=-amount if deduct else amount,
                target_user_id=target.user_id,
            )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="管理员扣除猪币" if deduct else "管理员发放猪币",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_grant_coins_all",
        description="插件管理员给当前群所有已登记玩家发放猪币",
        pattern=r"^/猪管全员发币(?:\s+(?P<amount>\S+))?\s*$",
    )
    async def handle_admin_grant_coins_all(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._handle_admin_all_coin_adjustment(
            stream_id,
            kwargs,
            deduct=False,
        )

    @Command(
        "pig_catcher_admin_deduct_coins_all",
        description="插件管理员扣除当前群所有已登记玩家猪币，允许负余额",
        pattern=r"^/猪管全员扣币(?:\s+(?P<amount>\S+))?\s*$",
    )
    async def handle_admin_deduct_coins_all(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._handle_admin_all_coin_adjustment(
            stream_id,
            kwargs,
            deduct=True,
        )

    async def _handle_admin_all_coin_adjustment(
        self,
        stream_id: str,
        kwargs: dict[str, Any],
        *,
        deduct: bool,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            amount = parse_admin_coin_amount(matched_group(kwargs, "amount"))
            result = await cast(
                AdministrationService,
                self._administration_service,
            ).adjust_coins(
                identity,
                command_name="pig-catcher.admin-coins",
                amount=-amount if deduct else amount,
                all_players=True,
            )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="管理员全员扣币" if deduct else "管理员全员发币",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_grant_asset",
        description="插件管理员为当前群玩家生成并发放一只指定猪猪或美食",
        pattern=(
            r"^/猪管(?:发放|发)(?P<kind>猪猪|猪|美食|菜)"
            r"(?:\s+(?P<arguments>.*?))?\s*$"
        ),
    )
    async def handle_admin_grant_asset(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            mention = self._optional_mention_target(kwargs)
            target = parse_admin_target_arguments(
                matched_group(kwargs, "arguments"),
                mentioned_user_id=mention.user_id if mention is not None else "",
                mentioned_display_name=(mention.display_name if mention is not None else ""),
            )
            query = parse_admin_asset_grant(target.remaining)
            result = await cast(
                AdministrationService,
                self._administration_service,
            ).grant_asset(
                identity,
                command_name="pig-catcher.admin-grant-asset",
                target_user_id=target.user_id,
                asset_kind=self._admin_kind(matched_group(kwargs, "kind")),
                template_selector=query.template_selector,
                requested_short_code=query.short_code,
            )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="管理员发放资产",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_remove_asset",
        description="插件管理员从当前群玩家背包移除一只精确猪猪或美食",
        pattern=(
            r"^/猪管(?:删除|删)(?P<kind>猪猪|猪|美食|菜)"
            r"(?:\s+(?P<arguments>.*?))?\s*$"
        ),
    )
    async def handle_admin_remove_asset(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            mention = self._optional_mention_target(kwargs)
            target = parse_admin_target_arguments(
                matched_group(kwargs, "arguments"),
                mentioned_user_id=mention.user_id if mention is not None else "",
                mentioned_display_name=(mention.display_name if mention is not None else ""),
            )
            selector = parse_admin_asset_selector(target.remaining)
            result = await cast(
                AdministrationService,
                self._administration_service,
            ).remove_asset(
                identity,
                command_name="pig-catcher.admin-remove-asset",
                target_user_id=target.user_id,
                asset_kind=self._admin_kind(matched_group(kwargs, "kind")),
                selector_text=selector,
            )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="管理员删除资产",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_blacklist",
        description="插件管理员查看或修改当前群插件、赠送、交易黑名单",
        pattern=r"^/猪管黑名单(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_admin_blacklist(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            arguments = matched_group(kwargs, "arguments")
            service = cast(AdministrationService, self._administration_service)
            if not arguments:
                snapshot = await service.blacklist_snapshot(identity)
                category_labels = {
                    "plugin-access-ban": "插件",
                    "gift-transfer-ban": "赠送/收赠",
                    "trade-ban": "交易",
                }
                grouped: dict[str, list[str]] = {
                    "plugin-access-ban": [],
                    "gift-transfer-ban": [],
                    "trade-ban": [],
                }
                for row in snapshot.rows:
                    restriction_type = str(row["restriction_type"])
                    reason = str(row.get("reason") or "").strip()
                    grouped[restriction_type].append(
                        f"- {row['display_name']}（{row['platform_user_id']}）" + (f"｜{reason}" if reason else "")
                    )
                lines = [f"【猪管·当前群黑名单】\n范围：{snapshot.scope_id}"]
                for category, label in category_labels.items():
                    entries = grouped[category]
                    lines.append(f"\n{label}黑名单（{len(entries)} 人）")
                    lines.extend(entries or ["- 无"])
                static_users = tuple(self.settings.access.user_blacklist)
                lines.append(f"\n配置静态用户黑名单（{len(static_users)} 项，全局）")
                lines.extend([f"- {value}" for value in static_users] if static_users else ["- 无"])
                return await self._reply_text(
                    identity.stream_id,
                    "\n".join(lines),
                    success=True,
                )
            mention = self._optional_mention_target(kwargs)
            query = parse_admin_blacklist_query(
                arguments,
                mentioned_user_id=mention.user_id if mention is not None else "",
                mentioned_display_name=(mention.display_name if mention is not None else ""),
            )
            if (
                query.action == "add"
                and query.category == "plugin"
                and self._target_is_configured_admin(identity, query.target.user_id)
            ):
                raise MentionTargetError("配置管理员不能加入插件访问黑名单，以免失去紧急解除权限。")
            result = await service.update_blacklist(
                identity,
                command_name="pig-catcher.admin-blacklist",
                target_user_id=query.target.user_id,
                category=query.category,
                action=query.action,
                reason=query.reason,
            )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="管理员黑名单管理",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_reset_player_quota",
        description="插件管理员只重置当前群一名玩家的本时段抓猪次数",
        pattern=r"^/猪管重置玩家(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_admin_reset_player_quota(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            mention = self._optional_mention_target(kwargs)
            target = parse_admin_target_arguments(
                matched_group(kwargs, "arguments"),
                mentioned_user_id=mention.user_id if mention is not None else "",
                mentioned_display_name=(mention.display_name if mention is not None else ""),
            )
            if target.remaining:
                raise MentionTargetError("玩家额度重置命令只能指定一名玩家。")
            result = await cast(
                AdministrationService,
                self._administration_service,
            ).reset_player_quota(
                identity,
                command_name="pig-catcher.admin-reset-player-quota",
                target_user_id=target.user_id,
            )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="管理员重置玩家抓猪次数",
                error=exc,
            )

    @Command(
        "pig_catcher_admin_reset_battle_quota",
        description="插件管理员重置当前群一人或全员的主动与被挑战机会",
        pattern=r"^/猪管重置比划(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_admin_reset_battle_quota(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_admin_command(stream_id, kwargs)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            arguments = matched_group(kwargs, "arguments")
            service = cast(AdministrationService, self._administration_service)
            if arguments in {"全员", "所有人"}:
                result = await service.reset_battle_quota(
                    identity,
                    command_name="pig-catcher.admin-reset-battle-quota",
                    all_players=True,
                )
            else:
                mention = self._optional_mention_target(kwargs)
                target = parse_admin_target_arguments(
                    arguments,
                    mentioned_user_id=mention.user_id if mention is not None else "",
                    mentioned_display_name=(mention.display_name if mention is not None else ""),
                )
                if target.remaining:
                    raise MentionTargetError("比划机会重置只能指定一名玩家，或输入“全员”。")
                result = await service.reset_battle_quota(
                    identity,
                    command_name="pig-catcher.admin-reset-battle-quota",
                    target_user_id=target.user_id,
                )
            return await self._deliver_text_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="管理员重置比划机会",
                error=exc,
            )

    @Command(
        "pig_catcher_reset_quota_chance",
        description="消耗一次重置机会，为当前群重置本时段抓猪次数",
        pattern=r"^/重置额度\s*$",
    )
    async def handle_reset_quota_chance(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        """让持有六星菜“重置机会”的玩家为命令所在群重置一次额度。"""

        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.catching_enabled,
            feature_label="抓猪",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            service = cast(CatchQuotaResetService, self._quota_reset_service)
            result = await service.reset_from_quota_chance(
                data_dir=Path(self.ctx.paths.data_dir).resolve(),
                identity=identity,
            )
            if result.receipt is None:
                raise RuntimeError("重置额度机会没有生成幂等回执。")
            self.ctx.logger.info(
                "玩家使用重置机会完成群额度重置：scope=%s，window=%s，cleared=%s，players=%s，audit=%s，backup=%s",
                result.scope_id,
                result.window.label,
                result.cleared_catches,
                result.affected_players,
                result.audit_event_id,
                result.backup_path,
            )
            view = group_event_quota_reset_view(
                result,
                group_name=identity.group_name,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_group_event(view),
                fallback_text=result.receipt.text_summary,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="使用重置机会",
                error=exc,
            )

    @Command(
        "pig_catcher_catch",
        description="在当前群抓取一只猪猪",
        pattern=r"^/(?:抓猪|抓群友)\s*$",
    )
    async def handle_catch(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.catching_enabled,
            feature_label="抓猪",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            if self.settings.features.battle_enabled and self._battle_service is not None:
                battle_loot = await self._battle_service.execute_pending_loot(identity)
                if battle_loot is not None:
                    return await self._deliver_battle_result(identity, battle_loot)
            result = await cast(GameplayService, self._gameplay_service).catch(identity)
            fallback = result.receipt.text_summary or format_catch_summary(result)
            if result.receipt_created and result.technique_resolution is None and result.pig.alternate_image_relpath:
                await self._send_image_file(
                    identity.stream_id,
                    result.pig.alternate_image_relpath,
                )
            if result.technique_resolution is not None:
                view = technique_catch_event_view(
                    result,
                    catcher_name=identity.display_name,
                    catcher_player_id=identity.player_id,
                    group_name=identity.group_name,
                )
                data_dir = Path(self.ctx.paths.data_dir).resolve()
                if result.technique_resolution.generated_foods:
                    source_path = food_media_path(
                        data_dir,
                        result.technique_resolution.generated_foods[0],
                    )
                else:
                    source_path = pig_media_path(data_dir, result.pig)
                event_paths = {}
                if result.technique_resolution.generated_foods:
                    for food in result.technique_resolution.generated_foods:
                        path = food_media_path(data_dir, food)
                        if path is not None:
                            event_paths[food.short_code] = path
                elif source_path is not None:
                    event_paths[result.pig.short_code] = source_path
                return await self._deliver_receipt(
                    stream_id=identity.stream_id,
                    receipt=result.receipt,
                    render=lambda: cast(
                        PigCatcherRenderer,
                        self._renderer,
                    ).render_group_event(view, source_path, media_paths=event_paths),
                    fallback_text=fallback,
                )
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: self._render_pig_card(
                    result.pig,
                    mode_label="抓猪成功",
                    catch=result,
                ),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="抓猪",
                error=exc,
            )

    @Command(
        "pig_catcher_toggle_baogian",
        description="切换保千猪的立绘与表情包显示；背包里有多只保千猪时需指定编号",
        pattern=r"^/切换\s+猪保千(?:\s+(?P<arguments>[0-9A-Za-z]{4,16}))?\s*$",
    )
    async def handle_toggle_baogian(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.catching_enabled,
            feature_label="抓猪",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            service = cast(GameplayService, self._gameplay_service)
            short_code = matched_group(kwargs, "arguments") or None
            count, _, message = await service.toggle_baogian(
                identity,
                short_code=short_code,
            )
            if count <= 0:
                return await self._reply_text(identity.stream_id, message, success=False)
            return await self._deliver_pig_art_toggle(
                identity,
                display_name="保千猪",
                short_code=short_code,
                message=message,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="切换保千猪立绘",
                error=exc,
            )

    @Command(
        "pig_catcher_toggle_uika",
        description="切换初华猪的普通版与戴帽子版立绘",
        pattern=r"^/切换\s+初华猪(?:\s+(?P<code>[0-9A-Za-z]{4,16}))?\s*$",
    )
    async def handle_toggle_uika(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.catching_enabled,
            feature_label="抓猪",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            short_code = matched_group(kwargs, "code") or None
            count, _, message = await cast(
                GameplayService,
                self._gameplay_service,
            ).toggle_pig_art(
                identity,
                display_name="初华猪",
                alternate_label="戴帽子版立绘",
                short_code=short_code,
            )
            if count <= 0:
                return await self._reply_text(identity.stream_id, message, success=False)
            return await self._deliver_pig_art_toggle(
                identity,
                display_name="初华猪",
                short_code=short_code,
                message=message,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="切换初华猪立绘",
                error=exc,
            )

    async def _deliver_pig_art_toggle(
        self,
        identity: CommandIdentity,
        *,
        display_name: str,
        short_code: str | None,
        message: str,
    ) -> tuple[bool, str, int]:
        async def render() -> RenderedImage:
            # A post-change read is optional presentation work.  If the pig was
            # moved concurrently or rendering fails, deliver the committed message.
            selector = f"{display_name}#{short_code}" if short_code else display_name
            pig = await cast(GameplayService, self._gameplay_service).pig_detail(identity, selector)
            return await self._render_pig_card(pig, mode_label="立绘已切换")

        return await self._deliver_query(
            stream_id=identity.stream_id,
            render=render,
            fallback_text=message,
        )

    async def _activate_group_technique_command(
        self,
        stream_id: str,
        kwargs: dict[str, Any],
        *,
        technique_id: str,
        operation: str,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.cooking_enabled,
            feature_label="做菜",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(
                GameplayService,
                self._gameplay_service,
            ).activate_group_technique(
                identity,
                technique_id=technique_id,
            )
            view = technique_activation_view(
                result,
                actor_name=identity.display_name,
                actor_player_id=identity.player_id,
                group_name=identity.group_name,
            )
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: cast(
                    PigCatcherRenderer,
                    self._renderer,
                ).render_group_event(view),
                fallback_text=result.receipt.text_summary,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation=operation,
                error=exc,
            )

    @Command(
        "pig_catcher_domain_expansion",
        description="发动一次伏魔御厨子群体领域",
        pattern=r"^/领域展开\s+伏魔御厨子\s*$",
    )
    async def handle_domain_expansion(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._activate_group_technique_command(
            stream_id,
            kwargs,
            technique_id=TECHNIQUE_MALEVOLENT_KITCHEN,
            operation="领域展开 伏魔御厨子",
        )

    @Command(
        "pig_catcher_lapse_blue",
        description="发动一次术式顺转苍",
        pattern=r"^/术式顺转\s+苍\s*$",
    )
    async def handle_lapse_blue(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._activate_group_technique_command(
            stream_id,
            kwargs,
            technique_id=TECHNIQUE_LAPSE_BLUE,
            operation="术式顺转 苍",
        )

    @Command(
        "pig_catcher_reversal_red",
        description="发动一次术式反转赫",
        pattern=r"^/术式反转\s+赫\s*$",
    )
    async def handle_reversal_red(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._activate_group_technique_command(
            stream_id,
            kwargs,
            technique_id=TECHNIQUE_REVERSAL_RED,
            operation="术式反转 赫",
        )

    @Command(
        "pig_catcher_hollow_purple",
        description="消耗一次苍赫组合资格发动虚式茈",
        pattern=r"^/虚式\s+茈\s*$",
    )
    async def handle_hollow_purple(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.cooking_enabled,
            feature_label="做菜",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(
                GameplayService,
                self._gameplay_service,
            ).activate_hollow_purple(identity)
            view = technique_activation_view(
                result,
                actor_name=identity.display_name,
                actor_player_id=identity.player_id,
                group_name=identity.group_name,
            )
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            source_path = pig_media_path(data_dir, result.granted_pigs[0]) if result.granted_pigs else None
            event_paths = {}
            for pig in result.granted_pigs:
                path = pig_media_path(data_dir, pig)
                if path is not None:
                    event_paths[pig.short_code] = path
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: cast(
                    PigCatcherRenderer,
                    self._renderer,
                ).render_group_event(view, source_path, media_paths=event_paths),
                fallback_text=result.receipt.text_summary,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="虚式 茈",
                error=exc,
            )

    @Command(
        "pig_catcher_enable_batch_keep",
        description="开启批量保留：批量操作按品种保留一只最高价值的猪猪与美食",
        pattern=r"^/开启批量保留\s*$",
    )
    async def handle_enable_batch_keep(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._set_batch_keep(stream_id, kwargs, enabled=True)

    @Command(
        "pig_catcher_disable_batch_keep",
        description="关闭批量保留效果",
        pattern=r"^/关闭批量保留\s*$",
    )
    async def handle_disable_batch_keep(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._set_batch_keep(stream_id, kwargs, enabled=False)

    async def _set_batch_keep(
        self,
        stream_id: str,
        kwargs: dict[str, Any],
        *,
        enabled: bool,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=(self.settings.features.selling_enabled or self.settings.features.cooking_enabled),
            feature_label="批量保留",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            service = cast(EconomyService, self._economy_service)
            _, message = await service.set_batch_keep_highest(
                identity,
                enabled=enabled,
            )
            view = EconomyReceiptViewModel(
                eyebrow="资产保护 · 批量操作偏好",
                title="保留设置已更新",
                badge_label="当前状态",
                badge_value="已开启" if enabled else "已关闭",
                summary="已开启批量保留" if enabled else "已关闭批量保留",
                rows=(
                    EconomyReceiptRowViewModel("普通猪猪与美食", "每种保留最高价值一只" if enabled else "不额外保留"),
                    EconomyReceiptRowViewModel("联动猪", "每种始终保留最高价值一只"),
                    EconomyReceiptRowViewModel("收藏资产", "批量操作始终跳过"),
                ),
                note=message,
            )
            return await self._deliver_player_status(identity, view, fallback_text=message)
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation=("开启批量保留" if enabled else "关闭批量保留"),
                error=exc,
            )

    @Command(
        "pig_catcher_favorite",
        description="收藏保护或取消保护当前持有的猪猪和美食",
        pattern=(
            r"^/(?P<action>收藏|取消收藏)\s+"
            r"(?P<kind>猪猪|猪|美食|菜)(?:\s+(?P<selector>.*?))?\s*$"
        ),
    )
    async def handle_favorite(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.inventory_enabled,
            feature_label="收藏保护",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            kind = matched_group(kwargs, "kind")
            result = await cast(EconomyService, self._economy_service).set_favorite(
                identity,
                asset_kind=("pig" if kind in {"猪猪", "猪"} else "food"),
                selector_text=matched_group(kwargs, "selector"),
                favorite=matched_group(kwargs, "action") == "收藏",
            )
            view = EconomyReceiptViewModel(
                eyebrow="资产保护 · 收藏设置",
                title="收藏保护已开启" if result.favorite else "收藏保护已取消",
                badge_label="当前状态",
                badge_value="已收藏" if result.favorite else "未收藏",
                summary=f"{result.display_name} ×{result.target_count}",
                rows=(
                    EconomyReceiptRowViewModel("资产种类", "猪猪" if result.asset_kind == "pig" else "美食"),
                    EconomyReceiptRowViewModel("匹配数量", str(result.target_count)),
                    EconomyReceiptRowViewModel("本次变更", str(result.changed_count)),
                ),
                note=(
                    "已收藏的资产不会被批量操作自动选中。"
                    if result.favorite else "取消收藏后，仍遵循当前批量保留规则。"
                ),
            )
            return await self._deliver_player_status(
                identity,
                view,
                receipt=result.receipt,
                fallback_text=result.receipt.text_summary,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="收藏保护",
                error=exc,
            )

    @Command(
        "pig_catcher_profile",
        description="查看当前群的个人抓猪档案",
        pattern=r"^/抓猪档案\s*$",
    )
    async def handle_profile(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.profile_enabled,
            feature_label="抓猪档案",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(GameplayService, self._gameplay_service).profile(identity)
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = await self._activity_view_cosmetics(identity, profile_view(result))
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_profile(view),
                fallback_text=format_profile_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="抓猪档案",
                error=exc,
            )

    @Command(
        "pig_catcher_pig_detail",
        description="查看一只当前持有猪猪的详情",
        pattern=r"^/(?:猪猪详情|抓猪详情)(?:\s+(?P<selector>.*?))?\s*$",
    )
    async def handle_pig_detail(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.inventory_enabled,
            feature_label="背包与详情",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            selector = matched_group(kwargs, "selector")
            pig = await cast(GameplayService, self._gameplay_service).pig_detail(
                identity,
                selector,
            )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: self._render_pig_card(
                    pig,
                    mode_label="猪猪详情",
                ),
                fallback_text=format_pig_detail_summary(pig),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪猪详情",
                error=exc,
            )

    @Command(
        "pig_catcher_inventory",
        description="查看当前群的个人猪猪背包",
        pattern=r"^/猪猪背包(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_inventory(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.inventory_enabled,
            feature_label="背包与详情",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_inventory_query(matched_group(kwargs, "arguments"))
            result = await cast(GameplayService, self._gameplay_service).inventory(
                identity,
                page=query.page,
                rarity=query.rarity,
                sort=query.sort,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = await self._activity_view_cosmetics(identity, inventory_view(result))
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_inventory(
                    view,
                    inventory_media_paths(data_dir, result),
                ),
                fallback_text=format_inventory_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪猪背包",
                error=exc,
            )

    @Command(
        "pig_catcher_catalog",
        description="查看当前群的个人猪猪图鉴",
        pattern=r"^/猪猪图鉴(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_catalog(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.catalog_enabled,
            feature_label="猪猪图鉴",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_catalog_query(matched_group(kwargs, "arguments"))
            result = await cast(GameplayService, self._gameplay_service).catalog(
                identity,
                rarity=query.rarity,
                undiscovered_only=query.undiscovered_only,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = await self._activity_view_cosmetics(identity, catalog_view(result))
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_catalog(
                    view,
                    catalog_media_paths(data_dir, result),
                ),
                fallback_text=format_catalog_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪猪图鉴",
                error=exc,
            )

    @Command(
        "pig_catcher_records",
        description="查看当前群的猪猪体型与重量纪录",
        pattern=r"^/猪猪纪录(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_records(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.records_enabled,
            feature_label="群纪录",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            page = parse_records_page(matched_group(kwargs, "arguments"))
            result = await cast(GameplayService, self._gameplay_service).records(
                identity,
                page=page,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = records_view(result)
            if self._achievement_service is not None and (
                self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
            ):
                holder_ids = tuple(dict.fromkeys(
                    item.player_id
                    for item in (*view.items, *view.global_items, *view.giant_sightings)
                    if item.player_id
                ))
                # 同一页三个纪录区一次只读批查，只使用纪录的稳定持有者身份。
                equipped = await self._achievement_service.cosmetics_for_players(holder_ids) if holder_ids else {}

                def decorated(item: Any) -> Any:
                    outfit = equipped.get(item.player_id)
                    return replace(item, achievement_title=outfit.title_id) if outfit is not None else item

                view = replace(
                    view,
                    items=tuple(decorated(item) for item in view.items),
                    global_items=tuple(decorated(item) for item in view.global_items),
                    giant_sightings=tuple(decorated(item) for item in view.giant_sightings),
                )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_records(view),
                fallback_text=format_records_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪猪纪录",
                error=exc,
            )

    @Command(
        "pig_catcher_daily_giants",
        description="查看当前群北京时间今天的最大体型与最重体重排行",
        pattern=r"^/今日巨物\s*$",
    )
    async def handle_daily_giants(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.records_enabled,
            feature_label="群纪录",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(GameplayService, self._gameplay_service).daily_giants(identity)
            renderer = cast(PigCatcherRenderer, self._renderer)
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            view = daily_giants_view(result)
            if self._achievement_service is not None and (
                self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
            ):
                holder_ids = tuple(dict.fromkeys(
                    item.player_id for item in (*view.size_items, *view.weight_items) if item.player_id
                ))
                # 每人可能同时进入两个榜，先去重再做一次查询；不按当前资产所有者改归属。
                equipped = await self._achievement_service.cosmetics_for_players(holder_ids) if holder_ids else {}

                def decorated(item: Any) -> Any:
                    outfit = equipped.get(item.player_id)
                    return replace(item, achievement_title=outfit.title_id) if outfit is not None else item

                view = replace(
                    view,
                    size_items=tuple(decorated(item) for item in view.size_items),
                    weight_items=tuple(decorated(item) for item in view.weight_items),
                )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_daily_giants(
                    view,
                    daily_giants_media_paths(data_dir, result),
                ),
                fallback_text=format_daily_giants_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="今日巨物",
                error=exc,
            )

    @Command(
        "pig_catcher_use_item",
        description="装备一个拥有的抓猪或做菜道具",
        pattern=r"^/使用道具(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_use_item(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.items_enabled,
            feature_label="使用道具",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            raw_arguments = matched_group(kwargs, "arguments") or matched_group(
                kwargs,
                "item_name",
            )
            query = parse_item_use_query(raw_arguments)
            result = await cast(GameplayService, self._gameplay_service).arm_item(
                identity,
                query.item_name,
                quantity=query.quantity,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = item_receipt_view(result)
            fallback = result.receipt.text_summary or format_item_action_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_item_receipt(view),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="使用道具",
                error=exc,
            )

    @Command(
        "pig_catcher_cancel_item",
        description="取消当前抓猪或做菜道具装备",
        pattern=r"^/取消道具(?:\s+(?P<action>.*?))?\s*$",
    )
    async def handle_cancel_item(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.items_enabled,
            feature_label="使用道具",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            action_type = parse_action_type(matched_group(kwargs, "action"))
            result = await cast(GameplayService, self._gameplay_service).cancel_item(
                identity,
                action_type,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = item_receipt_view(result)
            fallback = result.receipt.text_summary or format_item_action_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_item_receipt(view),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="取消道具",
                error=exc,
            )

    @Command(
        "pig_catcher_cook",
        description="把当前持有的一只猪制作成美食",
        pattern=r"^/做菜(?:\s+(?P<selector>.*?))?\s*$",
    )
    async def handle_cook(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.cooking_enabled,
            feature_label="做菜",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(EconomyService, self._economy_service).cook(
                identity,
                matched_group(kwargs, "selector"),
            )
            fallback = result.receipt.text_summary or format_cooking_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: self._render_food_card(
                    result.foods[0],
                    mode_label="做菜成功",
                    cooking=result,
                ),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="做菜",
                error=exc,
            )

    @Command(
        "pig_catcher_batch_cook",
        description="把背包中全部符合条件的猪批量制作成美食",
        pattern=r"^/批量做菜(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_batch_cook(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.cooking_enabled,
            feature_label="批量做菜",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_batch_cook_query(matched_group(kwargs, "arguments"))
            result = await cast(EconomyService, self._economy_service).batch_cook(
                identity,
                rarity=query.rarity,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = batch_cook_view(result)
            fallback = (
                result.receipt.text_summary
                if result.receipt is not None and result.receipt.text_summary
                else format_batch_cooking_summary(result)
            )
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            media_paths = {
                food.food_instance_id: food_media_path(data_dir, food)
                for food in result.foods
                if food_media_path(data_dir, food) is not None
            }
            if result.receipt is not None:
                return await self._deliver_receipt(
                    stream_id=identity.stream_id,
                    receipt=result.receipt,
                    render=lambda: renderer.render_batch_cook(view, media_paths),
                    fallback_text=fallback,
                )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_batch_cook(view, media_paths),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="批量做菜",
                error=exc,
            )

    @Command(
        "pig_catcher_food_detail",
        description="查看一份当前持有美食的详情",
        pattern=r"^/美食详情(?:\s+(?P<selector>.*?))?\s*$",
    )
    async def handle_food_detail(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.food_inventory_enabled,
            feature_label="美食背包与详情",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            food = await cast(EconomyService, self._economy_service).food_detail(
                identity,
                matched_group(kwargs, "selector"),
            )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: self._render_food_card(
                    food,
                    mode_label="美食详情",
                ),
                fallback_text=format_food_detail_summary(food),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="美食详情",
                error=exc,
            )

    @Command(
        "pig_catcher_food_inventory",
        description="查看当前群的个人美食背包",
        pattern=r"^/美食背包(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_food_inventory(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.food_inventory_enabled,
            feature_label="美食背包与详情",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_food_inventory_query(matched_group(kwargs, "arguments"))
            result = await cast(
                EconomyService,
                self._economy_service,
            ).food_inventory(
                identity,
                page=query.page,
                rarity=query.rarity,
                sort=query.sort,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = await self._activity_view_cosmetics(identity, food_inventory_view(result))
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_food_inventory(
                    view,
                    food_inventory_media_paths(data_dir, result),
                ),
                fallback_text=format_food_inventory_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="美食背包",
                error=exc,
            )

    @Command(
        "pig_catcher_food_catalog",
        description="查看当前群的个人美食图鉴",
        pattern=r"^/美食图鉴(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_food_catalog(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.food_catalog_enabled,
            feature_label="美食图鉴",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_catalog_query(matched_group(kwargs, "arguments"))
            result = await cast(
                EconomyService,
                self._economy_service,
            ).food_catalog(
                identity,
                rarity=query.rarity,
                undiscovered_only=query.undiscovered_only,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = await self._activity_view_cosmetics(identity, food_catalog_view(result))
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_food_catalog(
                    view,
                    food_catalog_media_paths(data_dir, result),
                ),
                fallback_text=format_food_catalog_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="美食图鉴",
                error=exc,
            )

    @Command(
        "pig_catcher_roulette",
        description="消耗一次猪保千猪排轮盘机会并抽取奖励",
        pattern=r"^/转轮盘\s*$",
    )
    async def handle_roulette(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.eating_enabled,
            feature_label="猪排轮盘",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(
                EconomyService,
                self._economy_service,
            ).spin_roulette(identity)
            view = roulette_event_view(
                result,
                actor_name=identity.display_name,
                actor_player_id=identity.player_id,
                group_name=identity.group_name,
            )
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: cast(
                    PigCatcherRenderer,
                    self._renderer,
                ).render_group_event(view),
                fallback_text=result.receipt.text_summary,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="转轮盘",
                error=exc,
            )

    @Command(
        "pig_catcher_eat",
        description="食用一份当前持有的美食",
        pattern=r"^/(?:吃菜|使用美食)(?:\s+(?P<selector>.*?))?\s*$",
    )
    async def handle_eat(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.eating_enabled,
            feature_label="吃菜",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(EconomyService, self._economy_service).eat_or_confirm(
                identity,
                matched_group(kwargs, "selector"),
            )
            if isinstance(result, EatConfirmationRequest):
                return await self._reply_text(
                    identity.stream_id,
                    (
                        f"“{result.food.display_name}”只剩最后一份"
                        f"（{result.food.rarity} 星，价值 {result.food.official_value} 猪币）。\n"
                        "30 秒内发送 /是 确认食用，发送 /否 取消；超时自动退出。"
                    ),
                    success=True,
                )
            return await self._deliver_eat_result(identity, result)
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="吃菜",
                error=exc,
            )

    @Command(
        "pig_catcher_eat_confirmation",
        description="确认或取消食用最后一份同名美食",
        pattern=r"^/(?P<decision>是|否)\s*$",
    )
    async def handle_eat_confirmation(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.eating_enabled,
            feature_label="吃菜",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            accepted = matched_group(kwargs, "decision") == "是"
            result = await cast(EconomyService, self._economy_service).confirm_eat(
                identity,
                accepted=accepted,
            )
            if isinstance(result, str):
                return await self._reply_text(
                    identity.stream_id,
                    result,
                    success=True,
                )
            return await self._deliver_eat_result(identity, result)
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="确认吃菜",
                error=exc,
            )

    async def _deliver_eat_result(
        self,
        identity: CommandIdentity,
        result: EatResult,
    ) -> tuple[bool, str, int]:
        renderer = cast(PigCatcherRenderer, self._renderer)
        if result.reward_payload:
            from .pig_catcher.rendering.food_rewards import food_reward_view

            result = await cast(EconomyService, self._economy_service).visible_eat_result(identity, result)
            view = food_reward_view(result)
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            paths = {item.key: media_path(data_dir, item.image_relpath) for item in view.items if item.image_relpath}
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_food_rewards(view, paths),
                fallback_text=format_eat_summary(result),
            )
        if is_group_event_food(result):
            event_view = group_event_eat_view(
                result,
                group_name=identity.group_name,
            )
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            source_path = food_media_path(data_dir, result.food)
            fallback = result.receipt.text_summary or format_group_event_eat_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_group_event(
                    event_view,
                    source_path,
                ),
                fallback_text=fallback,
            )
        if is_special_event_food(result):
            event_view = special_event_eat_view(
                result,
                group_name=identity.group_name,
            )
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            source_path = food_media_path(data_dir, result.food)
            fallback = result.receipt.text_summary or format_eat_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_group_event(
                    event_view,
                    source_path,
                ),
                fallback_text=fallback,
            )
        view = eat_receipt_view(result)
        fallback = result.receipt.text_summary or format_eat_summary(result)
        return await self._deliver_receipt(
            stream_id=identity.stream_id,
            receipt=result.receipt,
            render=lambda: renderer.render_economy_receipt(view),
            fallback_text=fallback,
        )

    @Command(
        "pig_catcher_store",
        description="查看道具与永久升级商城",
        pattern=r"^/猪猪商城(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_store(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.store_enabled,
            feature_label="猪猪商城",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_store_query(matched_group(kwargs, "arguments"))
            if query.category == "派遣" and not self.settings.features.dispatch_enabled:
                raise DomainValidationError("管理面板已关闭“猪猪派遣”功能，派遣商城暂不可用。")
            if query.category == "巡演" and not self.settings.features.tour_enabled:
                raise DomainValidationError("管理面板已关闭“猪猪巡演”功能，巡演商城暂不可用。")
            if query.category == "对战" and not self.settings.features.battle_enabled:
                raise DomainValidationError("管理面板已关闭“猪猪对战”功能，对战商城暂不可用。")
            result = await cast(EconomyService, self._economy_service).store(
                identity,
                page=query.page,
                category=query.category,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = store_view(result)
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_store(view),
                fallback_text=format_store_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪猪商城",
                error=exc,
            )

    @Command(
        "pig_catcher_purchase",
        description="购买商城中的消耗品",
        pattern=_PURCHASE_COMMAND_PATTERN,
    )
    async def handle_purchase(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.store_enabled,
            feature_label="商城购买",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_purchase_query(matched_group(kwargs, "arguments"))
            feature_product = FEATURE_SHOP_PRODUCTS_BY_NAME.get(query.product_name)
            if feature_product is not None:
                if (
                    feature_product.system is FeatureShopSystem.DISPATCH
                    and not self.settings.features.dispatch_enabled
                ):
                    raise DomainValidationError("管理面板已关闭“猪猪派遣”功能，无法购买派遣器具。")
                if (
                    feature_product.system is FeatureShopSystem.TOUR
                    and not self.settings.features.tour_enabled
                ):
                    raise DomainValidationError("管理面板已关闭“猪猪巡演”功能，无法购买巡演器具。")
                if (
                    feature_product.system is FeatureShopSystem.BATTLE
                    and not self.settings.features.battle_enabled
                ):
                    raise DomainValidationError("管理面板已关闭“猪猪对战”功能，无法购买对战器具。")
            result = await cast(EconomyService, self._economy_service).purchase(
                identity,
                query.product_name,
                quantity=query.quantity,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = purchase_receipt_view(result)
            fallback = result.receipt.text_summary or format_purchase_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(view),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="购买",
                error=exc,
            )

    @Command(
        "pig_catcher_upgrade",
        description="升级永久猪饲料或厨具",
        pattern=_UPGRADE_COMMAND_PATTERN,
    )
    async def handle_upgrade(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.store_enabled,
            feature_label="永久升级",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            upgrade_name = parse_upgrade_name(matched_group(kwargs, "arguments"))
            result = await cast(EconomyService, self._economy_service).upgrade(
                identity,
                upgrade_name,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = purchase_receipt_view(result)
            fallback = result.receipt.text_summary or format_purchase_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(view),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="升级",
                error=exc,
            )

    @Command(
        "pig_catcher_sell_pig",
        description="按官方价值售卖一只猪猪",
        pattern=r"^/售卖猪猪(?:\s+(?P<selector>.*?))?\s*$",
    )
    async def handle_sell_pig(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.selling_enabled,
            feature_label="官方售卖",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(EconomyService, self._economy_service).sell_pig(
                identity,
                matched_group(kwargs, "selector"),
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = sale_receipt_view(result)
            fallback = result.receipt.text_summary or format_sale_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(view),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="售卖猪猪",
                error=exc,
            )

    @Command(
        "pig_catcher_sell_food",
        description="按官方价值售卖一份美食",
        pattern=r"^/售卖美食(?:\s+(?P<selector>.*?))?\s*$",
    )
    async def handle_sell_food(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.selling_enabled,
            feature_label="官方售卖",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(EconomyService, self._economy_service).sell_food(
                identity,
                matched_group(kwargs, "selector"),
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = sale_receipt_view(result)
            fallback = result.receipt.text_summary or format_sale_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(view),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="售卖美食",
                error=exc,
            )

    @Command(
        "pig_catcher_batch_sell",
        description="批量售卖全部一至三星猪猪或美食",
        pattern=r"^/批量售卖(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_batch_sell(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.selling_enabled,
            feature_label="批量售卖",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_batch_sale_query(matched_group(kwargs, "arguments"))
            result = await cast(
                EconomyService,
                self._economy_service,
            ).batch_sell_low_rarity(
                identity,
                asset_kind=query.asset_kind.value,
                max_rarity=(5 if query.display_name else 3),
                rarity=query.rarity,
                display_name=query.display_name,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = batch_sale_receipt_view(result)
            fallback = result.receipt.text_summary or format_batch_sale_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(view),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="批量售卖",
                error=exc,
            )

    @Command(
        "pig_catcher_ledger",
        description="查看当前群个人猪币流水",
        pattern=r"^/猪币账本(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_ledger(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.ledger_enabled,
            feature_label="猪币账本",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            page = parse_ledger_page(matched_group(kwargs, "arguments"))
            result = await cast(EconomyService, self._economy_service).ledger(
                identity,
                page=page,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = ledger_view(result)
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_ledger(view),
                fallback_text=format_ledger_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪币账本",
                error=exc,
            )

    @Command(
        "pig_catcher_gift",
        description="把当前持有的猪猪或美食赠送给同群成员",
        pattern=r"^/(?P<kind>猪猪|美食)赠送(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_gift(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.trading.gift_enabled,
            feature_label="群内赠送",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            target = extract_mention_target(kwargs)
            recipient = self._recipient_identity(identity, target)
            query = parse_gift_query(
                matched_group(kwargs, "arguments"),
                target_display_name=target.display_name,
                target_user_id=target.user_id,
            )
            kind = AssetKind.PIG if matched_group(kwargs, "kind") == "猪猪" else AssetKind.FOOD
            result = await cast(SocialService, self._social_service).gift(
                identity,
                recipient,
                asset_kind=kind,
                selector_text=query.selector,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            fallback = result.receipt.text_summary or format_gift_summary(result)
            if result.regulation is not None and result.regulation.blocked:
                delivered = await self._deliver_text_receipt(
                    stream_id=identity.stream_id,
                    receipt=result.receipt,
                )
            else:
                delivered = await self._deliver_receipt(
                    stream_id=identity.stream_id,
                    receipt=result.receipt,
                    render=lambda: renderer.render_economy_receipt(gift_receipt_view(result)),
                    fallback_text=fallback,
                )
            if result.regulation is not None:
                await self._deliver_regulation_notices(
                    stream_id=identity.stream_id,
                    notice_ids=result.regulation.notice_ids,
                )
            return delivered
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="群内赠送",
                error=exc,
            )

    @Command(
        "pig_catcher_trade_offer",
        description="向同群成员发起一笔五分钟双方确认交易",
        pattern=r"^/(?P<kind>猪猪|美食)交易(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_trade_offer(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.trading.trade_enabled,
            feature_label="双方确认交易",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            target = extract_mention_target(kwargs)
            recipient = self._recipient_identity(identity, target)
            query = parse_trade_offer_query(
                matched_group(kwargs, "arguments"),
                target_display_name=target.display_name,
                target_user_id=target.user_id,
            )
            kind = AssetKind.PIG if matched_group(kwargs, "kind") == "猪猪" else AssetKind.FOOD
            result = await cast(
                SocialService,
                self._social_service,
            ).create_trade(
                identity,
                recipient,
                asset_kind=kind,
                selector_text=query.selector,
                price=query.price,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            fallback = result.receipt.text_summary or format_trade_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(trade_receipt_view(result)),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="创建交易",
                error=exc,
            )

    @Command(
        "pig_catcher_trade_accept",
        description="接收方确认并完成一笔当前群交易",
        pattern=r"^/接受交易(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_trade_accept(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._handle_trade_resolution(
            stream_id,
            kwargs,
            operation="接受交易",
            action=lambda service, identity, trade_id: service.accept_trade(
                identity,
                trade_id,
            ),
        )

    @Command(
        "pig_catcher_trade_reject",
        description="接收方拒绝一笔当前群交易",
        pattern=r"^/拒绝交易(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_trade_reject(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._handle_trade_resolution(
            stream_id,
            kwargs,
            operation="拒绝交易",
            action=lambda service, identity, trade_id: service.reject_trade(
                identity,
                trade_id,
            ),
        )

    @Command(
        "pig_catcher_trade_cancel",
        description="发起方取消一笔当前群交易",
        pattern=r"^/取消交易(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_trade_cancel(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        return await self._handle_trade_resolution(
            stream_id,
            kwargs,
            operation="取消交易",
            action=lambda service, identity, trade_id: service.cancel_trade(
                identity,
                trade_id,
            ),
        )

    async def _handle_trade_resolution(
        self,
        stream_id: str,
        kwargs: dict[str, Any],
        *,
        operation: str,
        action: Callable[
            [SocialService, CommandIdentity, str],
            Awaitable[Any],
        ],
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.trading.trade_enabled,
            feature_label="双方确认交易",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            trade_id = parse_trade_id(matched_group(kwargs, "arguments"))
            result = await action(
                cast(SocialService, self._social_service),
                identity,
                trade_id,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            fallback = result.receipt.text_summary or format_trade_summary(result)
            if result.regulation is not None and result.regulation.blocked:
                delivered = await self._deliver_text_receipt(
                    stream_id=identity.stream_id,
                    receipt=result.receipt,
                )
            else:
                delivered = await self._deliver_receipt(
                    stream_id=identity.stream_id,
                    receipt=result.receipt,
                    render=lambda: renderer.render_economy_receipt(trade_receipt_view(result)),
                    fallback_text=fallback,
                )
            if result.regulation is not None:
                await self._deliver_regulation_notices(
                    stream_id=identity.stream_id,
                    notice_ids=result.regulation.notice_ids,
                )
            return delivered
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation=operation,
                error=exc,
            )

    @Command(
        "pig_catcher_trade_list",
        description="查看个人在当前群的交易记录",
        pattern=r"^/我的交易(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_trade_list(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.trading.trade_enabled,
            feature_label="双方确认交易",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_trade_list_query(matched_group(kwargs, "arguments"))
            result = await cast(
                SocialService,
                self._social_service,
            ).trade_page(
                identity,
                page=query.page,
                status=query.status,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_trade_list(trade_list_view(result)),
                fallback_text=format_trade_page_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="我的交易",
                error=exc,
            )

    @Command(
        "pig_catcher_showcase",
        description="设置或取消当前群个人猪猪和美食展示位",
        pattern=r"^/设置展示(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_showcase(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.showcase_enabled,
            feature_label="设置展示",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_showcase_query(matched_group(kwargs, "arguments"))
            result = await cast(
                SocialService,
                self._social_service,
            ).set_showcase(
                identity,
                asset_kind=query.asset_kind,
                selector_text=query.selector,
                clear=query.clear,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            fallback = result.receipt.text_summary or format_showcase_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(showcase_receipt_view(result)),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="设置展示",
                error=exc,
            )

    @Command(
        "pig_catcher_achievements",
        description="查看 PiG Dream 成就总览或分类成就册",
        pattern=r"^/猪猪成就(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_achievements(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.achievements_enabled,
            feature_label="猪猪成就",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            service = cast(AchievementService, self._achievement_service)
            renderer = cast(PigCatcherRenderer, self._renderer)
            arguments = matched_group(kwargs, "arguments").strip()
            if not arguments:
                result = await service.overview(identity)
                view = await self._activity_view_cosmetics(identity, achievement_overview_view(result))
                fallback = (
                    f"【PiG Dream! 成就总览】\n{result.display_name}："
                    f"{result.unlocked_count}/{result.total_count} 项，"
                    f"共 {result.points} 成就点。"
                )
                delivered = await self._deliver_query(
                    stream_id=identity.stream_id,
                    render=lambda: renderer.render_achievement_overview(view),
                    fallback_text=fallback,
                    activity_identity=identity,
                )
                await self._deliver_achievement_backfill_summary(
                    stream_id=identity.stream_id,
                    player_id=identity.player_id,
                )
                return delivered
            tokens = arguments.split()
            page = 1
            category: str | None = None
            if tokens and tokens[-1].isdigit():
                page = max(1, int(tokens.pop()))
            if tokens:
                category = " ".join(tokens)
            result = await service.page(identity, category=category, page=page)
            view = await self._activity_view_cosmetics(identity, achievement_page_view(result))
            fallback = "\n".join(
                [f"【{result.category}成就 · {result.page}/{result.page_count}】"]
                + [
                    f"{'✓' if entry.unlocked else '·'} {entry.tier_label} {entry.name} {entry.progress}/{entry.target}"
                    for entry in result.entries
                ]
            )
            delivered = await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_achievement_page(view),
                fallback_text=fallback,
                activity_identity=identity,
            )
            await self._deliver_achievement_backfill_summary(
                stream_id=identity.stream_id,
                player_id=identity.player_id,
            )
            return delivered
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪猪成就",
                error=exc,
            )

    @Command(
        "pig_catcher_achievement_detail",
        description="按完整名称查看一项成就详情",
        pattern=r"^/成就详情(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_achievement_detail(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.achievements_enabled,
            feature_label="成就详情",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            name = matched_group(kwargs, "arguments").strip()
            if not name:
                raise CommandContextError("请填写完整成就名，例如：/成就详情 第一次伸手")
            result = await cast(AchievementService, self._achievement_service).detail_page(identity, name)
            view = await self._activity_view_cosmetics(identity, achievement_page_view(result))
            entry = result.entries[0]
            renderer = cast(PigCatcherRenderer, self._renderer)
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_achievement_page(view),
                activity_identity=identity,
                fallback_text=(
                    f"【成就详情】{entry.name}\n{entry.description}\n"
                    f"进度 {entry.progress}/{entry.target}，"
                    f"奖励 {entry.points} 成就点。"
                ),
            )
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="成就详情", error=exc)

    async def _deliver_equipped_cosmetics(
        self,
        identity: CommandIdentity,
        *,
        source_name: str,
        source_label: str,
        equipped_count: int,
    ) -> tuple[bool, str, int]:
        view = DispatchView(
            title="外观佩戴成功",
            player_name=identity.display_name,
            subtitle="称号、边框与徽章",
            banner=f"已佩戴“{source_name}”的{source_label}外观。",
            stats=(
                DispatchLine("外观来源", source_label),
                DispatchLine("已更新", f"{equipped_count} 个部位"),
            ),
            hints=("相关玩法图片会展示当前佩戴外观。", "/取消佩戴成就 可卸下外观，奖励仍保留。"),
            presentation="cosmetics",
            scene_key="",
        )

        async def render() -> RenderedImage:
            # Read the already-equipped profile inside the render callback so an
            # optional preview failure cannot report the completed setting as failed.
            decorated = await self._activity_view_cosmetics(identity, view)
            return await cast(PigCatcherRenderer, self._renderer).render_dispatch(decorated, {})

        return await self._deliver_query(
            stream_id=identity.stream_id,
            render=render,
            fallback_text=view.text(),
        )

    @Command(
        "pig_catcher_achievement_equip",
        description="佩戴已解锁成就附带的称号、边框或徽章",
        pattern=r"^/佩戴成就(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_achievement_equip(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=(
                self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
            ),
            feature_label="佩戴成就",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            name = matched_group(kwargs, "arguments").strip()
            if not name:
                raise CommandContextError("请填写带有称号奖励的已解锁成就名。")
            weekly_service = self._weekly_competition_service
            if weekly_service is not None and self.settings.features.weekly_competitions_enabled:
                weekly_cosmetics = await weekly_service.equip_competition_cosmetics(identity, name)
                if weekly_cosmetics is not None:
                    return await self._deliver_equipped_cosmetics(
                        identity,
                        source_name=name,
                        source_label="周冲榜",
                        equipped_count=len(weekly_cosmetics),
                    )
            if not self.settings.features.achievements_enabled:
                raise CommandContextError("成就系统当前未启用，且没有找到可佩戴的周冲榜奖励。")
            cosmetics = await cast(AchievementService, self._achievement_service).equip_cosmetics_by_achievement(
                identity, name
            )
            return await self._deliver_equipped_cosmetics(
                identity,
                source_name=name,
                source_label="成就",
                equipped_count=len(cosmetics),
            )
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="佩戴成就", error=exc)

    @Command(
        "pig_catcher_achievement_badges",
        description="查看或按位置佩戴、卸下已拥有的成就徽章与周榜牌",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/成就徽章(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_achievement_badges(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id, kwargs,
            feature_enabled=(
                self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
            ),
            feature_label="成就徽章",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            from .pig_catcher.services.achievement_badges import AchievementBadgeService

            result = await AchievementBadgeService(
                cast(AchievementService, self._achievement_service),
                labels={
                    key: str(value["name"]) for key, value in COSMETIC_DEFINITIONS.items() if value["kind"] == "badge"
                },
            ).execute(identity, matched_group(kwargs, "arguments"))
            renderer = cast(PigCatcherRenderer, self._renderer)
            if result.receipt:
                return await self._deliver_receipt(
                    stream_id=identity.stream_id, receipt=result.receipt,
                    render=lambda: renderer.render_dispatch(result.view, {}), fallback_text=result.view.text(),
                    track_progress=False,
                )
            return await self._deliver_query(
                stream_id=identity.stream_id, render=lambda: renderer.render_dispatch(result.view, {}),
                fallback_text=result.view.text(),
            )
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="成就徽章", error=exc)

    @Command(
        "pig_catcher_achievement_unequip",
        description="取消当前佩戴的成就称号、边框和徽章",
        pattern=r"^/取消佩戴成就\s*$",
    )
    async def handle_achievement_unequip(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=(
                self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
            ),
            feature_label="取消佩戴成就",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            await cast(AchievementService, self._achievement_service).clear_equipped_cosmetics(identity)
            message = "已取消全部成就与周冲榜外观；已拥有的外观奖励仍然保留。"
            view = EconomyReceiptViewModel(
                eyebrow="外观管理 · 保留已解锁奖励",
                title="外观已卸下",
                badge_label="当前状态",
                badge_value="未佩戴",
                summary="称号、边框与徽章已卸下",
                rows=(EconomyReceiptRowViewModel("外观库存", "完整保留"),),
                note="使用 /佩戴成就 成就名或活动名，可重新佩戴已拥有的外观。",
            )
            return await self._deliver_player_status(identity, view, fallback_text=message)
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="取消佩戴成就", error=exc)

    @Command(
        "pig_catcher_achievement_ticket",
        description="激活一张不可交易的成就玩法券",
        pattern=r"^/使用成就券(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_achievement_ticket(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=True,
            feature_label="使用成就券",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            name = matched_group(kwargs, "arguments").strip()
            from pig_catcher.domain.activity_achievements import ACTIVITY_REWARDS

            if any(item["kind"] == "ticket" and name in {key, item["name"]} for key, item in ACTIVITY_REWARDS.items()):
                return await self._activity_reward_command(identity, "使用 " + name)
            ticket_id = await cast(AchievementService, self._achievement_service).activate_ticket(
                identity, name, track_achievements=self.settings.features.achievements_enabled
            )
            ticket_label = reward_label(AchievementReward("ticket", ticket_id, 1))
            message = f"已激活{ticket_label}，将在下一次符合条件的操作中结算。"
            view = EconomyReceiptViewModel(
                eyebrow="成就奖励 · 玩法券待命",
                title="成就券已激活",
                badge_label="当前状态",
                badge_value="待结算",
                summary=ticket_label,
                rows=(
                    EconomyReceiptRowViewModel("已激活", "1 张"),
                    EconomyReceiptRowViewModel("生效时机", "下一次符合条件的操作"),
                ),
                note="同一消息重复投递不会再次扣券；使用条件与原有效果保持不变。",
            )
            return await self._deliver_player_status(identity, view, fallback_text=message)
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="使用成就券",
                error=exc,
            )

    @Command(
        "pig_catcher_achievement_reforge",
        description="使用编号修改券或旧编号重铸券修改一件资产的短编号",
        pattern=(
            rf"^{_COMMAND_LEADING_MENTION_PATTERN}/重铸编号\s+(?P<kind>猪猪|猪|美食|菜)\s+"
            r"(?P<old_code>[A-Za-z0-9]{4,16})\s+"
            r"(?P<new_code>[A-Za-z0-9]{4,16})\s*$"
        ),
    )
    async def handle_achievement_reforge(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=True,
            feature_label="重铸编号",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            service = self._item_bag_service
            if service is None:
                raise RuntimeError("道具与奖励服务尚未就绪。")
            result = await service.reforge_identifier(
                identity,
                asset_kind=matched_group(kwargs, "kind"),
                old_code=matched_group(kwargs, "old_code"),
                new_code=matched_group(kwargs, "new_code"),
            )
            return await self._deliver_item_bag_result(identity, result)
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="重铸编号",
                error=exc,
            )

    @Command(
        "pig_catcher_achievement_chest",
        description="开启一个成就自选宝箱",
        pattern=r"^/打开成就宝箱(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_achievement_chest(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id, kwargs, feature_enabled=self.settings.features.achievements_enabled, feature_label="成就自选宝箱"
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            rewards = await cast(AchievementService, self._achievement_service).open_choice_chest(
                identity, matched_group(kwargs, "arguments")
            )
            labels = tuple(
                f"{COSMETIC_DEFINITIONS.get(item.reward_id, {}).get('name', '外观奖励')} ×{item.quantity}"
                if item.reward_type in {"title", "frame", "badge", "cosmetic"}
                else reward_label(item)
                for item in rewards
            )
            text = "【成就自选宝箱】\n已选择奖励：" + "、".join(labels)
            view = EconomyReceiptViewModel(
                eyebrow="成就奖励 · 自选宝箱",
                title="宝箱奖励已领取",
                badge_label="奖励种类",
                badge_value=str(len(rewards)),
                summary="成就自选宝箱已开启",
                rows=tuple(EconomyReceiptRowViewModel("已获得", label) for label in labels),
                note="奖励已进入库存，可通过 /道具背包 查看；同一消息重试不会再次开箱。",
            )
            return await self._deliver_player_status(identity, view, fallback_text=text)
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="打开成就宝箱", error=exc)

    @Command(
        "pig_catcher_achievement_memorial_pig",
        description="完成全部常规成就后领取一只未收集的公共五星纪念猪",
        pattern=r"^/领取成就纪念猪(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_achievement_memorial_pig(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.achievements_enabled,
            feature_label="领取成就纪念猪",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            result = await cast(AchievementService, self._achievement_service).claim_memorial_pig(
                identity,
                matched_group(kwargs, "arguments"),
            )
            pig = await cast(GameplayService, self._gameplay_service).pig_detail(
                identity,
                f"{result.display_name}#{result.short_code}",
            )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: self._render_pig_card(
                    pig,
                    mode_label="常规成就毕业纪念",
                ),
                fallback_text=(
                    "【PiG Dream! 常规成就毕业纪念】\n"
                    f"获得 {result.display_name}#{result.short_code}；"
                    "不计抓猪次数、概率统计或抓猪奖励。"
                ),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="领取成就纪念猪",
                error=exc,
            )

    @Command(
        "pig_catcher_achievement_ranking",
        description="查看当前群成就点排行",
        pattern=r"^/成就排行(?:\s+(?P<arguments>\d+))?\s*$",
    )
    async def handle_achievement_ranking(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id, kwargs, feature_enabled=self.settings.features.achievements_enabled, feature_label="成就排行"
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            page_text = matched_group(kwargs, "arguments")
            result = await cast(AchievementService, self._achievement_service).ranking(
                identity, page=int(page_text or 1)
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            fallback = "\n".join(
                [f"【成就排行 {result.page}/{result.page_count}】"]
                + [
                    f"{item.rank}. {item.display_name} · {item.points} 点 · {item.unlocked_count} 项"
                    for item in result.entries
                ]
            )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_achievement_ranking(achievement_ranking_view(result)),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="成就排行", error=exc)

    @Command(
        "pig_catcher_weekly_competition",
        description="查看本期 PiG Dream 周冲榜",
        pattern=r"^/(?:抓猪线|zzx)(?:\s+(?P<arguments>\d+))?\s*$",
    )
    async def handle_weekly_competition(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.weekly_competitions_enabled,
            feature_label="周冲榜",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            service = self._weekly_competition_service
            if service is None:
                raise RuntimeError("周冲榜服务尚未就绪。")
            page_text = matched_group(kwargs, "arguments")
            result = await service.leaderboard(identity, page=int(page_text or 1))
            renderer = cast(PigCatcherRenderer, self._renderer)
            delivered = await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_weekly_competition(weekly_competition_view(result)),
                fallback_text=format_weekly_competition_summary(result),
            )
            if delivered[0]:
                await self._deliver_weekly_competition_award(
                    stream_id=identity.stream_id,
                    player_id=identity.player_id,
                )
            return delivered
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="周冲榜",
                error=exc,
            )

    @Command(
        "pig_catcher_ranking",
        description="查看当前群综合、抓猪、美食、价值、巨物、数量或猪币排行",
        pattern=r"^/猪猪排行(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_ranking(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.ranking_enabled,
            feature_label="猪猪排行",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            query = parse_ranking_query(matched_group(kwargs, "arguments"))
            result = await cast(
                SocialService,
                self._social_service,
            ).ranking(
                identity,
                ranking_type=query.ranking_type,
                page=query.page,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            view = ranking_view(result)
            if self._achievement_service is not None and (
                self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
            ):
                equipped = await self._achievement_service.cosmetics_for_players([item.key for item in view.items])
                view = replace(view, items=tuple(
                    replace(
                        item,
                        achievement_title=equipped[item.key].title_id,
                        achievement_frame=equipped[item.key].frame_id,
                        achievement_badge=equipped[item.key].badge_name,
                        achievement_badges=equipped[item.key].badge_ids,
                        achievement_badge_capacity=equipped[item.key].badge_capacity,
                    ) if item.key in equipped else item
                    for item in view.items
                ))
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_ranking(view, ranking_media_paths(data_dir, result)),
                fallback_text=format_ranking_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪猪排行",
                error=exc,
            )

    @Command(
        "pig_catcher_activity_rewards",
        description="查看成就奖励、选择专用券并确认材料自选",
        pattern=r"^/成就奖励(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_activity_rewards(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id, kwargs, feature_enabled=True, feature_label="成就奖励"
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            return await self._activity_reward_command(identity, matched_group(kwargs, "arguments"))
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="成就奖励", error=exc)

    @Command(
        "pig_catcher_item_bag",
        description="分页查看商城道具、已装备和排队份数以及奖励券库存",
        pattern="^" + _COMMAND_LEADING_MENTION_PATTERN + ITEM_BAG_PATTERN[1:],
    )
    async def handle_item_bag(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._item_bag_command(stream_id, kwargs, "bag")

    @Command(
        "pig_catcher_reward_coupon",
        description="使用编号修改券、选择猪猪自选券并确认兑换",
        pattern="^" + _COMMAND_LEADING_MENTION_PATTERN + REWARD_COUPON_PATTERN[1:],
    )
    async def handle_reward_coupon(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._item_bag_command(stream_id, kwargs, "coupon")

    async def _item_bag_command(self, stream_id: str, kwargs: dict[str, Any], section: str) -> tuple[bool, str, int]:
        label = "道具背包" if section == "bag" else "使用奖励券"
        identity, rejected = await self._prepare_command(stream_id, kwargs, feature_enabled=True, feature_label=label)
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            service = self._item_bag_service
            if service is None:
                raise RuntimeError("道具与奖励服务尚未就绪。")
            request = parse_item_bag_request(matched_group(kwargs, "arguments"), section=section)
            result = await service.execute(identity, request)
            return await self._deliver_item_bag_result(identity, result)
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation=label, error=exc)

    async def _deliver_item_bag_result(
        self, identity: CommandIdentity, result: DispatchResult
    ) -> tuple[bool, str, int]:
        renderer = self._renderer
        if renderer is None:
            raise RuntimeError("道具与奖励图片服务尚未就绪。")
        data_dir = Path(self.ctx.paths.data_dir).resolve()
        paths = {
            pig.short_code: media_path(data_dir, pig.image_relpath) for pig in result.view.pigs if pig.image_relpath
        }

        async def render() -> RenderedImage:
            return await renderer.render_dispatch(await self._activity_view_cosmetics(identity, result.view), paths)

        if result.receipt is not None:
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=render,
                fallback_text=result.receipt.text_summary,
            )
        return await self._deliver_query(stream_id=identity.stream_id, render=render, fallback_text=result.view.text())

    async def _activity_reward_command(self, identity: CommandIdentity, text: str) -> tuple[bool, str, int]:
        from pig_catcher.services.achievement_rewards import AchievementRewardService

        result = await AchievementRewardService(cast(AchievementService, self._achievement_service)).execute(
            identity, text
        )
        renderer = cast(PigCatcherRenderer, self._renderer)
        if result.receipt:
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_dispatch(result.view, {}),
                fallback_text=result.view.text(),
            )
        return await self._deliver_query(
            stream_id=identity.stream_id,
            render=lambda: renderer.render_dispatch(result.view, {}),
            fallback_text=result.view.text(),
            activity_identity=identity,
        )

    async def _activity_view_cosmetics(self, identity: CommandIdentity, view: Any) -> Any:
        """Decorate an already settled/query view; no reward or random operation occurs."""
        if self._achievement_service is None or not (
            self.settings.features.achievements_enabled or self.settings.features.weekly_competitions_enabled
        ):
            return view
        cosmetics = await self._achievement_service.cosmetics_for_player(identity.player_id)
        return replace(
            view,
            achievement_title=cosmetics.title_id,
            achievement_frame=cosmetics.frame_id,
            achievement_badge=cosmetics.badge_name,
            achievement_badges=cosmetics.badge_ids,
            achievement_badge_capacity=cosmetics.badge_capacity,
        )

    async def _dispatch_command(self, stream_id: str, kwargs: Mapping[str, Any], section: str) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.dispatch_enabled,
            feature_label="猪猪派遣",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        try:
            request = parse_dispatch_request(matched_group(kwargs, "arguments"), section=section)
            if request.action == "help":
                return await self._reply_text(identity.stream_id, DISPATCH_HELP, success=True)
            service, renderer = self._dispatch_service, self._renderer
            if service is None or renderer is None:
                raise RuntimeError("派遣服务尚未就绪。")
            result = await service.execute(identity, request)
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            paths = {
                pig.short_code: media_path(data_dir, pig.image_relpath) for pig in result.view.pigs if pig.image_relpath
            }

            async def render() -> RenderedImage:
                return await renderer.render_dispatch(await self._activity_view_cosmetics(identity, result.view), paths)

            if result.receipt is not None:
                return await self._deliver_receipt(
                    stream_id=identity.stream_id,
                    receipt=result.receipt,
                    render=render,
                    fallback_text=result.receipt.text_summary,
                )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=render,
                fallback_text=result.view.text(),
                activity_identity=identity,
            )
        except PigCatcherError as exc:
            if self._renderer is None:
                return await self._command_error(stream_id=identity.stream_id, operation="猪猪派遣", error=exc)
            view = DispatchView(
                title="派遣提示",
                player_name="猪猪远行社",
                banner=str(exc),
                hints=("/猪猪派遣 帮助 查看可复制命令。",),
            )
            delivered = await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: self._renderer.render_dispatch(view, {}),
                fallback_text=view.text(),
            )
            return False, delivered[1], delivered[2]
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="猪猪派遣", error=exc)

    @Command(
        "pig_catcher_dispatch",
        description="猪猪派遣编队、出发、确认、召回和返程",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/猪猪派遣(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_dispatch(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._dispatch_command(stream_id, kwargs, "dispatch")

    @Command(
        "pig_catcher_dispatch_bag",
        description="派遣材料、器具配方、制作与转换",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/派遣背包(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_dispatch_bag(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._dispatch_command(stream_id, kwargs, "bag")

    @Command(
        "pig_catcher_dispatch_journal",
        description="派遣游记和自然纪念品收藏",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/派遣游记(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_dispatch_journal(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._dispatch_command(stream_id, kwargs, "journal")

    @Command(
        "pig_catcher_dispatch_encounter",
        description="查看并选择旅行奇遇",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/派遣奇遇(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_dispatch_encounters(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._dispatch_command(stream_id, kwargs, "encounters")

    async def _tour_command(self, stream_id: str, kwargs: Mapping[str, Any], section: str) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.tour_enabled,
            feature_label="猪猪巡演",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        renderer = self._renderer
        try:
            arguments = matched_group(kwargs, "arguments")
            target = None
            if section == "joint" and arguments not in {"", "接受", "拒绝", "取消", "帮助", "help", "?"}:
                target = extract_mention_target(kwargs, arguments=arguments)
            request = parse_tour_request(
                arguments,
                section=section,
                entry=matched_group(kwargs, "entry"),
                target_user_id=target.user_id if target else "",
                target_name=target.display_name if target else "",
            )
            if request.action == "help":
                return await self._reply_text(identity.stream_id, TOUR_HELP, success=True)
            service = self._tour_service
            if service is None or renderer is None:
                raise RuntimeError("巡演服务尚未就绪。")
            result = await service.execute(identity, request)
            data_dir = Path(self.ctx.paths.data_dir).resolve()
            paths = {
                pig.short_code: media_path(data_dir, pig.image_relpath) for pig in result.view.pigs if pig.image_relpath
            }

            async def render() -> RenderedImage:
                return await renderer.render_tour(await self._activity_view_cosmetics(identity, result.view), paths)

            if result.receipt is not None:
                return await self._deliver_receipt(
                    stream_id=identity.stream_id,
                    receipt=result.receipt,
                    render=render,
                    fallback_text=result.view.text(),
                )
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=render,
                fallback_text=result.view.text(),
                activity_identity=identity,
            )
        except PigCatcherError as exc:
            if renderer is None:
                return await self._command_error(stream_id=identity.stream_id, operation="猪猪巡演", error=exc)
            view = TourView(
                title="巡演提示",
                player_name="巡演准备室",
                banner=str(exc),
                hints=("/猪猪巡演 帮助 查看可复制命令。",),
            )
            delivered = await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_tour(view, {}),
                fallback_text=view.text(),
            )
            return False, delivered[1], delivered[2]
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="猪猪巡演", error=exc)

    @Command(
        "pig_catcher_band",
        description="自由乐队、三套阵容、舞台养成和成员保护",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/(?P<entry>我的猪猪乐队|组建乐队|乐队编队|乐队练习)(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_band(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._tour_command(stream_id, kwargs, "band")

    @Command(
        "pig_catcher_tour",
        description="猪猪巡演三站编排、排练与结算",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/(?P<entry>猪猪巡演|巡演继续|巡演一键)(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_tour(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._tour_command(stream_id, kwargs, "tour")

    @Command(
        "pig_catcher_tour_journal",
        description="巡演站点游记和主题收藏",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/巡演游记(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_tour_journal(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._tour_command(stream_id, kwargs, "journal")

    @Command(
        "pig_catcher_joint_tour",
        description="同群双方确认的独立乐队联演",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/巡演联演(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_joint_tour(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._tour_command(stream_id, kwargs, "joint")

    async def _deliver_battle_result(
        self,
        identity: CommandIdentity,
        result: BattleResult,
    ) -> tuple[bool, str, int]:
        renderer = self._renderer
        root = Path(self.ctx.paths.data_dir).resolve()
        paths = {pig.short_code: media_path(root, pig.image_relpath) for pig in result.view.pigs if pig.image_relpath}

        async def render() -> RenderedImage:
            if renderer is None:
                raise RuntimeError("对战渲染器尚未就绪。")
            return await renderer.render_battle(await self._activity_view_cosmetics(identity, result.view), paths)

        if result.receipt:
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=render,
                fallback_text=result.view.text(),
            )
        return await self._deliver_query(
            stream_id=identity.stream_id,
            render=render,
            fallback_text=result.view.text(),
            activity_identity=identity,
        )

    async def _battle_command(self, stream_id: str, kwargs: Mapping[str, Any], section: str) -> tuple[bool, str, int]:
        identity, rejected = await self._prepare_command(
            stream_id,
            kwargs,
            feature_enabled=self.settings.features.battle_enabled
            and (section != "loot" or self.settings.features.catching_enabled),
            feature_label="猪猪对战" if section != "loot" else "战利品抓猪",
        )
        if rejected is not None or identity is None:
            return rejected or (False, "", 0)
        renderer = self._renderer
        try:
            arguments = matched_group(kwargs, "arguments")
            target = None
            if section == "challenge" and arguments not in {
                "",
                "接受",
                "拒绝",
                "取消",
                "认输",
                "确认认输",
                "帮助",
                "help",
                "?",
            }:
                target = extract_mention_target(kwargs, arguments=arguments)
            request = parse_battle_request(
                arguments,
                section=section,
                target_user_id=target.user_id if target else "",
                target_name=target.display_name if target else "",
            )
            if request.action == "help":
                return await self._reply_text(identity.stream_id, BATTLE_HELP, success=True)
            service = self._battle_service
            if service is None or renderer is None:
                raise RuntimeError("对战服务尚未就绪。")
            result = await service.execute(identity, request)
            return await self._deliver_battle_result(identity, result)
        except PigCatcherError as exc:
            if renderer is None:
                return await self._command_error(stream_id=identity.stream_id, operation="猪猪对战", error=exc)
            hint = BattleView(
                title="对战提示", player_name="对战准备室", banner=str(exc), hints=("/战斗猪 帮助 查看可复制命令。",)
            )
            delivered = await self._deliver_query(
                stream_id=identity.stream_id, render=lambda: renderer.render_battle(hint, {}), fallback_text=hint.text()
            )
            return False, delivered[1], delivered[2]
        except Exception as exc:
            return await self._command_error(stream_id=identity.stream_id, operation="猪猪对战", error=exc)

    @Command(
        "pig_catcher_battle_pig",
        description="战斗猪设置、养成、保护、轮盘与器具",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/战斗猪(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_battle_pig(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._battle_command(stream_id, kwargs, "profile")

    @Command(
        "pig_catcher_battle_challenge",
        description="同群双人挑战、接受、拒绝、认输",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/比划比划(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_battle_challenge(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._battle_command(stream_id, kwargs, "challenge")

    @Command(
        "pig_catcher_battle_count",
        description="抽取本回合出招数并结算贷款扣招",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/出招数\s*$",
    )
    async def handle_battle_count(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._battle_command(stream_id, kwargs, "count")

    @Command(
        "pig_catcher_battle_move",
        description="自动执行本回合招式连锁并展示在对应战斗猪下方",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/出招\s*$",
    )
    async def handle_battle_move(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._battle_command(stream_id, kwargs, "move")

    @Command(
        "pig_catcher_battle_ready",
        description="看完双方招式后确认本回合；双方都确认才结算胜负",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/会赢的\s*$",
    )
    async def handle_battle_ready(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._battle_command(stream_id, kwargs, "ready")

    @Command(
        "pig_catcher_battle_status",
        description="观战与查看双方当前状态",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/对战状态\s*$",
    )
    async def handle_battle_status(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._battle_command(stream_id, kwargs, "status")

    @Command(
        "pig_catcher_battle_history",
        description="本群对战与逐招历史记录",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/对战记录(?:\s+(?P<arguments>.*?))?\s*$",
    )
    async def handle_battle_history(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._battle_command(stream_id, kwargs, "history")

    @Command(
        "pig_catcher_battle_loot",
        description="败者额外五次战利品抓猪，直接交付胜者",
        pattern=rf"^{_COMMAND_LEADING_MENTION_PATTERN}/战利品抓猪\s*$",
    )
    async def handle_battle_loot(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        return await self._battle_command(stream_id, kwargs, "loot")


def create_plugin() -> PigCatcherPlugin:
    """MaiBot Runner 插件工厂。"""

    return PigCatcherPlugin()
