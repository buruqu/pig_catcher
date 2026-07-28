"""MaiBot 抓猪插件 2B 正式素材框架入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, Command, MaiBotPlugin

from .pig_catcher.assets import AssetCatalogStorage
from .pig_catcher.commands import extract_command_identity, format_help, matched_group
from .pig_catcher.config import AccessPolicy, PigCatcherConfig
from .pig_catcher.domain.errors import CommandContextError
from .pig_catcher.infrastructure import PigCatcherDatabase, safe_database_path
from .pig_catcher.rendering import (
    AnimatedCardComposer,
    PigCatcherRenderer,
    RenderDelivery,
    RenderOptions,
)
from .pig_catcher.services import (
    AssetCatalogService,
    FrameworkService,
    MaintenanceOptions,
    MaintenanceRunner,
    ReceiptService,
)
from .pig_catcher.version import PLUGIN_VERSION


class PigCatcherPlugin(MaiBotPlugin):
    """开放帮助并承载正式素材、动画和后续玩法服务。"""

    config_model = PigCatcherConfig

    def __init__(self) -> None:
        super().__init__()
        self._database: PigCatcherDatabase | None = None
        self._storage: AssetCatalogStorage | None = None
        self._asset_service: AssetCatalogService | None = None
        self._framework_service: FrameworkService | None = None
        self._receipt_service: ReceiptService | None = None
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

    async def on_load(self) -> None:
        if self.settings.plugin.enabled:
            await self._open_runtime()
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
            self._receipt_service = ReceiptService(database)
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
        self._receipt_service = None
        self._framework_service = None
        self._asset_service = None
        self._storage = None
        self._database = None

    def _access_policy(self) -> AccessPolicy:
        settings = self.settings.access
        return AccessPolicy(
            group_whitelist=settings.group_whitelist,
            group_blacklist=settings.group_blacklist,
            user_whitelist=settings.user_whitelist,
            user_blacklist=settings.user_blacklist,
            denied_message=settings.denied_message,
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


def create_plugin() -> PigCatcherPlugin:
    """MaiBot Runner 插件工厂。"""

    return PigCatcherPlugin()
