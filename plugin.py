"""MaiBot 抓猪插件第五轮显式命令入口。"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
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
    parse_batch_sale_query,
    parse_catalog_query,
    parse_food_inventory_query,
    parse_gift_query,
    parse_inventory_query,
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
from .pig_catcher.config import AccessPolicy, PigCatcherConfig
from .pig_catcher.domain.enums import AssetKind
from .pig_catcher.domain.errors import (
    CommandContextError,
    MentionTargetError,
    PigCatcherError,
)
from .pig_catcher.domain.gameplay import ITEM_DEFINITIONS
from .pig_catcher.domain.models import CommandIdentity, CommandReceipt
from .pig_catcher.infrastructure import PigCatcherDatabase, safe_database_path
from .pig_catcher.rendering import (
    AnimatedCardComposer,
    PigCatcherRenderer,
    RenderDelivery,
    RenderedImage,
    RenderOptions,
    batch_sale_receipt_view,
    catalog_media_paths,
    catalog_view,
    eat_receipt_view,
    food_card_view,
    food_catalog_media_paths,
    food_catalog_view,
    food_inventory_media_paths,
    food_inventory_view,
    food_media_path,
    gift_receipt_view,
    inventory_media_paths,
    inventory_view,
    item_receipt_view,
    ledger_view,
    pig_card_view,
    pig_media_path,
    profile_view,
    purchase_receipt_view,
    ranking_media_paths,
    ranking_view,
    records_view,
    sale_receipt_view,
    showcase_receipt_view,
    store_view,
    trade_list_view,
    trade_receipt_view,
)
from .pig_catcher.services import (
    AssetCatalogService,
    CatchQuotaResetService,
    CatchResult,
    CookingResult,
    EconomyService,
    FoodView,
    FrameworkService,
    GameplayService,
    MaintenanceOptions,
    MaintenanceRunner,
    PigView,
    ReceiptService,
    SocialService,
    format_batch_sale_summary,
    format_catalog_summary,
    format_catch_summary,
    format_cooking_summary,
    format_eat_summary,
    format_food_catalog_summary,
    format_food_detail_summary,
    format_food_inventory_summary,
    format_gift_summary,
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
)
from .pig_catcher.version import PLUGIN_VERSION

_PURCHASE_PRODUCT_PATTERN = "(?:" + "|".join(
    escape(item.display_name) for item in ITEM_DEFINITIONS
) + ")"
_PURCHASE_COMMAND_PATTERN = (
    rf"^/购买(?:\s+(?P<arguments>{_PURCHASE_PRODUCT_PATTERN}(?:\s+.*?)?))?\s*$"
)
_UPGRADE_TARGET_PATTERN = r"(?:猪饲料|饲料|猪饲料升级|厨具|厨具升级)"
_UPGRADE_COMMAND_PATTERN = (
    rf"^/升级(?:\s+(?P<arguments>{_UPGRADE_TARGET_PATTERN}))?\s*$"
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
        self._quota_reset_service: CatchQuotaResetService | None = None
        self._renderer: PigCatcherRenderer | None = None
        self._animation_composer: AnimatedCardComposer | None = None
        self._delivery: RenderDelivery | None = None
        self._maintenance: MaintenanceRunner | None = None

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
            await self._execute_pending_quota_reset(source="admin-panel-load")
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
        if self.settings.quota_administration.execute_current_window_reset:
            if self._database is None and self.settings.plugin.enabled:
                await self._open_runtime()
            await self._execute_pending_quota_reset(source="admin-panel-save")
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
                ),
            )
            animation_composer = AnimatedCardComposer(
                max_output_bytes=settings.rendering.max_animation_bytes,
                missing_frame_duration_ms=settings.rendering.missing_frame_duration_ms,
            )
            maintenance = MaintenanceRunner(
                database,
                storage,
                data_dir,
                MaintenanceOptions(
                    interval_minutes=settings.maintenance.interval_minutes,
                    run_integrity_check=settings.maintenance.run_integrity_check,
                    auto_backup_enabled=settings.storage.auto_backup_enabled,
                    backup_interval_hours=settings.storage.backup_interval_hours,
                    backup_retention_count=settings.storage.backup_retention_count,
                    staging_max_age_hours=settings.assets.staging_max_age_hours,
                ),
                logger=self.ctx.logger,
            )
            self._database = database
            self._storage = storage
            self._asset_service = asset_service
            self._framework_service = FrameworkService(database)
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
            )
            self._social_service = SocialService(
                database,
                settings.trading,
                settings.ranking,
            )
            self._receipt_service = ReceiptService(database)
            self._quota_reset_service = CatchQuotaResetService(
                database,
                refresh_hours=settings.catching.quota_refresh_hours,
                timezone_name=settings.catching.daily_reset_timezone,
                window_limit=settings.catching.daily_limit,
            )
            self._renderer = renderer
            self._animation_composer = animation_composer
            self._delivery = RenderDelivery(
                self.ctx.send,
                logger=self.ctx.logger,
                fallback_to_text=settings.rendering.fallback_to_text,
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
        self._maintenance = None
        self._delivery = None
        self._animation_composer = None
        self._renderer = None
        self._quota_reset_service = None
        self._receipt_service = None
        self._economy_service = None
        self._social_service = None
        self._gameplay_service = None
        self._framework_service = None
        self._asset_service = None
        self._storage = None
        self._database = None

    async def _execute_pending_quota_reset(self, *, source: str) -> None:
        """Execute and acknowledge the one-shot WebUI reset request."""

        administration = self.settings.quota_administration
        if not administration.execute_current_window_reset:
            return
        service = self._quota_reset_service
        if service is None:
            raise RuntimeError("抓猪额度重置服务尚未就绪。")
        result = await service.backup_and_reset_current_window(
            data_dir=Path(self.ctx.paths.data_dir).resolve(),
            group_id=administration.group_id,
            actor_user_id="maibot-admin-panel",
            source=source,
        )
        self._clear_quota_reset_trigger()
        self.ctx.logger.info(
            "抓猪额度已精准重置：scope=%s，window=%s，cleared=%s，players=%s，audit=%s，backup=%s",
            result.scope_id,
            result.window.label,
            result.cleared_catches,
            result.affected_players,
            result.audit_event_id,
            result.backup_path,
        )

    @staticmethod
    def _clear_quota_reset_trigger() -> None:
        """Atomically turn the one-shot reset switch back off in config.toml."""

        config_path = Path(__file__).resolve().with_name("config.toml")
        temporary_path = config_path.with_name(
            f".{config_path.name}.{uuid4().hex}.tmp"
        )
        document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
        administration = document.get("quota_administration")
        if administration is None:
            raise RuntimeError("config.toml 缺少 [quota_administration] 配置节。")
        administration["execute_current_window_reset"] = False
        try:
            temporary_path.write_text(tomlkit.dumps(document), encoding="utf-8")
            os.replace(temporary_path, config_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @HomeCard(
        "pig_catcher_quota_control",
        title="抓猪额度管理",
        description="按群精准重置当前抓猪时段；操作前自动在线备份并保留历史记录。",
        content=[
            {
                "type": "key_value",
                "entries": {
                    "每日刷新": "00:00 / 09:00 / 12:00 / 19:00",
                    "每段额度": "5 次 / 玩家 / 群",
                    "时段内冷却": "20 秒",
                },
            },
            {
                "type": "markdown",
                "content": "点击下方按钮，在“额度重置”中填写精确群号，打开一次性开关并保存。",
            },
            {
                "type": "actions",
                "actions": [
                    {
                        "label": "重置抓猪次数",
                        "url": "/plugin-config?plugin=local.pig-catcher",
                    }
                ],
            },
        ],
        link_url="/plugin-config?plugin=local.pig-catcher",
        link_label="打开额度重置",
        icon="timer-reset",
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

    async def _reply_text(
        self,
        stream_id: str,
        text: str,
        *,
        success: bool,
    ) -> tuple[bool, str, int]:
        sent = bool(await self.ctx.send.text(text, stream_id)) if stream_id else False
        return sent and success, text, 2 if success else 1

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

    async def _deliver_query(
        self,
        *,
        stream_id: str,
        render: Callable[[], Awaitable[RenderedImage]],
        fallback_text: str,
    ) -> tuple[bool, str, int]:
        if self._delivery is None:
            return await self._reply_text(
                stream_id,
                fallback_text,
                success=True,
            )
        sent = await self._delivery.send_image_or_text(
            stream_id=stream_id,
            render=render,
            fallback_text=fallback_text,
            rendering_enabled=self.settings.rendering.enabled,
        )
        return sent, fallback_text, 2 if sent else 1

    async def _deliver_receipt(
        self,
        *,
        stream_id: str,
        receipt: CommandReceipt,
        render: Callable[[], Awaitable[RenderedImage]],
        fallback_text: str,
    ) -> tuple[bool, str, int]:
        receipts = self._receipt_service
        if receipts is None:
            return False, "抓猪回执服务尚未就绪。", 1
        if not await receipts.claim_send(receipt.receipt_id):
            return True, "该消息已处理，不重复公示。", 0
        if self._delivery is None:
            sent = bool(await self.ctx.send.text(fallback_text, stream_id))
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
        if not await receipts.claim_send(receipt.receipt_id):
            return True, "该消息已处理，不重复公示。", 0
        try:
            sent = bool(await self.ctx.send.text(receipt.text_summary, stream_id))
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
            return True, receipt.text_summary, 2
        await receipts.mark_failed(receipt.receipt_id, "纯文字发送未成功")
        return False, receipt.text_summary, 1

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
        data_dir = Path(self.ctx.paths.data_dir).resolve()
        source_path = pig_media_path(data_dir, pig)
        if (
            pig.media_visible
            and pig.is_animated
            and source_path is not None
            and source_path.is_file()
        ):
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
        data_dir = Path(self.ctx.paths.data_dir).resolve()
        source_path = food_media_path(data_dir, food)
        if (
            food.media_visible
            and food.is_animated
            and source_path is not None
            and source_path.is_file()
        ):
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
        pattern=r"^/抓猪帮助(?:\s+(?P<topic>\S+))?\s*$",
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
            if not self.settings.features.help_enabled:
                return await self._reply_text(
                    identity.stream_id,
                    "管理面板已关闭“抓猪帮助”功能。",
                    success=False,
                )
            text = format_help(matched_group(kwargs, "topic"))
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
            result = await cast(GameplayService, self._gameplay_service).catch(identity)
            fallback = result.receipt.text_summary or format_catch_summary(result)
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
            view = profile_view(result)
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
        pattern=r"^/抓猪详情(?:\s+(?P<selector>.*?))?\s*$",
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
                operation="抓猪详情",
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
            view = inventory_view(result)
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
            view = catalog_view(result)
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
        "pig_catcher_use_item",
        description="装备一个拥有的抓猪或做菜道具",
        pattern=r"^/使用道具(?:\s+(?P<item_name>.*?))?\s*$",
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
            item_name = matched_group(kwargs, "item_name")
            result = await cast(GameplayService, self._gameplay_service).arm_item(
                identity,
                item_name,
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
            query = parse_food_inventory_query(
                matched_group(kwargs, "arguments")
            )
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
            view = food_inventory_view(result)
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
            view = food_catalog_view(result)
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
            result = await cast(EconomyService, self._economy_service).eat(
                identity,
                matched_group(kwargs, "selector"),
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = eat_receipt_view(result)
            fallback = result.receipt.text_summary or format_eat_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(view),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="吃菜",
                error=exc,
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
            result = await cast(EconomyService, self._economy_service).purchase(
                identity,
                query.product_name,
                quantity=query.quantity,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = purchase_receipt_view(result)
            fallback = result.receipt.text_summary or format_purchase_summary(
                result
            )
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
            fallback = result.receipt.text_summary or format_purchase_summary(
                result
            )
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
                max_rarity=3,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            view = batch_sale_receipt_view(result)
            fallback = result.receipt.text_summary or format_batch_sale_summary(
                result
            )
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
            kind = (
                AssetKind.PIG
                if matched_group(kwargs, "kind") == "猪猪"
                else AssetKind.FOOD
            )
            result = await cast(SocialService, self._social_service).gift(
                identity,
                recipient,
                asset_kind=kind,
                selector_text=query.selector,
            )
            renderer = cast(PigCatcherRenderer, self._renderer)
            fallback = result.receipt.text_summary or format_gift_summary(result)
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(
                    gift_receipt_view(result)
                ),
                fallback_text=fallback,
            )
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
            kind = (
                AssetKind.PIG
                if matched_group(kwargs, "kind") == "猪猪"
                else AssetKind.FOOD
            )
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
                render=lambda: renderer.render_economy_receipt(
                    trade_receipt_view(result)
                ),
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
            return await self._deliver_receipt(
                stream_id=identity.stream_id,
                receipt=result.receipt,
                render=lambda: renderer.render_economy_receipt(
                    trade_receipt_view(result)
                ),
                fallback_text=fallback,
            )
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
                render=lambda: renderer.render_trade_list(
                    trade_list_view(result)
                ),
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
                render=lambda: renderer.render_economy_receipt(
                    showcase_receipt_view(result)
                ),
                fallback_text=fallback,
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="设置展示",
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
            return await self._deliver_query(
                stream_id=identity.stream_id,
                render=lambda: renderer.render_ranking(
                    ranking_view(result),
                    ranking_media_paths(data_dir, result),
                ),
                fallback_text=format_ranking_summary(result),
            )
        except Exception as exc:
            return await self._command_error(
                stream_id=identity.stream_id,
                operation="猪猪排行",
                error=exc,
            )


def create_plugin() -> PigCatcherPlugin:
    """MaiBot Runner 插件工厂。"""

    return PigCatcherPlugin()
