"""MaiBot WebUI 可渲染的简体中文配置模型。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator, model_validator

from ..domain.rules import normalize_weights
from ..version import FRAMEWORK_PHASE, PLUGIN_VERSION


def _ui(label: str, hint: str, **extra: object) -> dict[str, object]:
    return {"label": label, "hint": hint, **extra}


def _validate_simple_filename(value: str, field_label: str) -> str:
    normalized = str(value or "").strip()
    path = Path(normalized)
    if not normalized or path.name != normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_label}必须是数据目录内的单个文件名")
    return normalized


class PluginSection(PluginConfigBase):
    """插件启停与版本。"""

    __ui_label__ = "插件设置"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="是否启用抓猪插件",
        json_schema_extra=_ui("启用插件", "关闭后仅停止服务，不会删除数据库和素材"),
    )
    config_version: str = Field(
        default=PLUGIN_VERSION,
        description="当前配置模板版本",
        frozen=True,
        json_schema_extra=_ui("配置版本", "由插件维护，不需要手工修改", disabled=True),
    )
    framework_phase: Literal["2A"] = Field(
        default=FRAMEWORK_PHASE,
        description="当前开发交付阶段",
        frozen=True,
        json_schema_extra=_ui("框架阶段", "2A 仅开放帮助与工程基础，不伪造玩法结果", disabled=True),
    )


class FeaturesSection(PluginConfigBase):
    """当前已经闭环的功能开关。"""

    __ui_label__ = "功能开关"
    __ui_icon__ = "sliders-horizontal"
    __ui_order__ = 10

    help_enabled: bool = Field(
        default=True,
        description="是否允许查看抓猪指令帮助",
        json_schema_extra=_ui("允许抓猪帮助", "对应 /抓猪帮助；本指令始终返回便于复制的纯文字"),
    )


class AccessSection(PluginConfigBase):
    """管理群和用户访问范围。"""

    __ui_label__ = "访问控制"
    __ui_icon__ = "shield-check"
    __ui_order__ = 20

    group_whitelist: list[str] = Field(
        default_factory=list,
        max_length=500,
        description="群白名单非空时，仅列表中的群可以使用插件",
        json_schema_extra=_ui("群白名单", "每行一个群号；留空表示不限制，黑名单始终优先"),
    )
    group_blacklist: list[str] = Field(
        default_factory=list,
        max_length=500,
        description="群黑名单中的群始终不能使用插件",
        json_schema_extra=_ui("群黑名单", "每行一个群号；命中后即使在白名单中也会被拒绝"),
    )
    user_whitelist: list[str] = Field(
        default_factory=list,
        max_length=2000,
        description="用户白名单非空时，仅列表中的用户可以使用插件",
        json_schema_extra=_ui("用户白名单", "每行一个用户号；留空表示不限制，黑名单始终优先"),
    )
    user_blacklist: list[str] = Field(
        default_factory=list,
        max_length=2000,
        description="用户黑名单中的用户始终不能使用插件",
        json_schema_extra=_ui("用户黑名单", "每行一个用户号；命中后即使在白名单中也会被拒绝"),
    )
    admin_user_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="允许执行未来素材与维护管理操作的用户",
        json_schema_extra=_ui("插件管理员", "每行一个用户号；不会自动继承群管理员身份"),
    )
    notify_denied: bool = Field(
        default=True,
        description="访问被拒绝时是否发送提示",
        json_schema_extra=_ui("拒绝时提示", "关闭后黑白名单命中会静默忽略命令"),
    )
    denied_message: str = Field(
        default="当前群或账号未启用抓猪插件。",
        min_length=1,
        max_length=200,
        description="访问控制拒绝时发送的文字",
        json_schema_extra=_ui("拒绝提示文字", "仅在“拒绝时提示”开启时发送"),
    )


class StorageSection(PluginConfigBase):
    """数据库和备份设置。"""

    __ui_label__ = "数据存储"
    __ui_icon__ = "database"
    __ui_order__ = 30

    database_filename: str = Field(
        default="pig_catcher.sqlite3",
        min_length=4,
        max_length=100,
        description="SQLite 数据库文件名",
        json_schema_extra=_ui("数据库文件名", "只能填写文件名，数据库固定保存到插件数据目录"),
    )
    sqlite_busy_timeout_ms: int = Field(
        default=5000,
        ge=100,
        le=60000,
        description="SQLite 等待其他写事务释放锁的毫秒数",
        json_schema_extra=_ui("数据库忙等待", "建议保持 5000 毫秒；并发较高时可适度增加"),
    )
    auto_backup_enabled: bool = Field(
        default=True,
        description="是否由维护任务定期备份数据库",
        json_schema_extra=_ui("自动备份", "备份写入插件数据目录的 backups 子目录"),
    )
    backup_interval_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="自动备份间隔小时数",
        json_schema_extra=_ui("备份间隔", "允许 1 至 720 小时"),
    )
    backup_retention_count: int = Field(
        default=7,
        ge=1,
        le=100,
        description="最多保留的自动备份数量",
        json_schema_extra=_ui("备份保留数", "超过数量后仅删除本插件 backups 目录中的最旧备份"),
    )

    @field_validator("database_filename")
    @classmethod
    def validate_database_filename(cls, value: str) -> str:
        normalized = _validate_simple_filename(value, "数据库文件名")
        if Path(normalized).suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("数据库文件名必须使用 .db、.sqlite 或 .sqlite3 后缀")
        return normalized


class AssetsSection(PluginConfigBase):
    """素材清单和图片校验。"""

    __ui_label__ = "素材管理"
    __ui_icon__ = "images"
    __ui_order__ = 40

    manifest_filename: str = Field(
        default="assets.json",
        min_length=5,
        max_length=100,
        description="素材包中的清单文件名",
        json_schema_extra=_ui("素材清单文件", "第二轮 2B 导入素材时读取，必须是 JSON 文件"),
    )
    min_image_side: int = Field(
        default=256,
        ge=32,
        le=4096,
        description="允许导入图片的最短边像素下限",
        json_schema_extra=_ui("图片最短边", "正式素材建议不低于 1024；测试素材可在配置验证中降低"),
    )
    max_image_bytes: int = Field(
        default=12582912,
        ge=1024,
        le=52428800,
        description="单张素材图片的最大字节数",
        json_schema_extra=_ui("单图大小上限", "默认 12 MiB，超过后拒绝导入"),
    )
    staging_max_age_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="未完成素材暂存目录的最长保留小时数",
        json_schema_extra=_ui("暂存清理时间", "维护任务只清理本插件素材暂存目录"),
    )

    @field_validator("manifest_filename")
    @classmethod
    def validate_manifest_filename(cls, value: str) -> str:
        normalized = _validate_simple_filename(value, "素材清单文件名")
        if Path(normalized).suffix.lower() != ".json":
            raise ValueError("素材清单必须使用 .json 后缀")
        return normalized


class CatchingSection(PluginConfigBase):
    """抓猪频率和六档基础权重。"""

    __ui_label__ = "抓猪规则"
    __ui_icon__ = "target"
    __ui_order__ = 50

    daily_limit: int = Field(
        default=30,
        ge=1,
        le=1000,
        description="每位玩家在每个群每天可成功抓取的次数",
        json_schema_extra=_ui("每日抓猪次数", "当前设计默认 30 次；玩法命令将在后续阶段开放"),
    )
    cooldown_seconds: int = Field(
        default=60,
        ge=0,
        le=86400,
        description="同一玩家两次抓猪之间的最短秒数",
        json_schema_extra=_ui("抓猪冷却", "设置 0 表示不限制冷却，仍受每日次数约束"),
    )
    rarity_1_weight: float = Field(
        default=55.0,
        ge=0,
        le=10000,
        description="一星普通家养猪的基础权重",
        json_schema_extra=_ui("一星权重", "默认 55；六档会在运行时统一归一化"),
    )
    rarity_2_weight: float = Field(
        default=25.0,
        ge=0,
        le=10000,
        description="二星美味家养猪的基础权重",
        json_schema_extra=_ui("二星权重", "默认 25；六档会在运行时统一归一化"),
    )
    rarity_3_weight: float = Field(
        default=12.0,
        ge=0,
        le=10000,
        description="三星优质家养猪的基础权重",
        json_schema_extra=_ui("三星权重", "默认 12；六档会在运行时统一归一化"),
    )
    rarity_4_weight: float = Field(
        default=5.5,
        ge=0,
        le=10000,
        description="四星极品佳肴猪的基础权重",
        json_schema_extra=_ui("四星权重", "默认 5.5；六档会在运行时统一归一化"),
    )
    rarity_5_weight: float = Field(
        default=2.0,
        ge=0,
        le=10000,
        description="五星传说珍馐猪的基础权重",
        json_schema_extra=_ui("五星权重", "默认 2；无六星素材时会接收六星权重"),
    )
    rarity_6_weight: float = Field(
        default=0.5,
        ge=0,
        le=10000,
        description="六星可爱猪群友的基础权重",
        json_schema_extra=_ui("六星权重", "默认 0.5；当前群没有授权素材时不会抽取"),
    )
    max_feed_level: Literal[5] = Field(
        default=5,
        frozen=True,
        description="猪饲料永久升级的最高等级",
        json_schema_extra=_ui("饲料最高等级", "产品规则固定为 5 级", disabled=True),
    )
    missing_six_star_strategy: Literal["transfer-to-five"] = Field(
        default="transfer-to-five",
        frozen=True,
        description="当前群没有六星素材时的权重处理方式",
        json_schema_extra=_ui("缺少六星素材", "六星权重转入五星，不显示空图或跨群借图", disabled=True),
    )

    def weights(self) -> tuple[float, ...]:
        return normalize_weights(
            (
                self.rarity_1_weight,
                self.rarity_2_weight,
                self.rarity_3_weight,
                self.rarity_4_weight,
                self.rarity_5_weight,
                self.rarity_6_weight,
            )
        )


class CookingSection(PluginConfigBase):
    """做菜硬性规则。"""

    __ui_label__ = "做菜规则"
    __ui_icon__ = "cooking-pot"
    __ui_order__ = 60

    max_cookware_level: Literal[5] = Field(
        default=5,
        frozen=True,
        description="厨具永久升级的最高等级",
        json_schema_extra=_ui("厨具最高等级", "产品规则固定为 5 级", disabled=True),
    )
    six_star_to_five_percent: Literal[90] = Field(
        default=90,
        frozen=True,
        description="六星猪做出五星菜的固定百分比",
        json_schema_extra=_ui("六星猪出五星菜", "固定 90%，任何属性和道具都不能改变", disabled=True),
    )
    six_star_to_six_percent: Literal[10] = Field(
        default=10,
        frozen=True,
        description="六星猪做出六星菜的固定百分比",
        json_schema_extra=_ui("六星猪出六星菜", "固定 10%，且不会出现其他品质", disabled=True),
    )


class EconomySection(PluginConfigBase):
    """永久升级价格基线。"""

    __ui_label__ = "猪币经济"
    __ui_icon__ = "coins"
    __ui_order__ = 70

    max_upgrade_level: Literal[5] = Field(
        default=5,
        frozen=True,
        description="永久升级统一最高等级",
        json_schema_extra=_ui("升级最高等级", "猪饲料和厨具均固定为 5 级", disabled=True),
    )
    feed_upgrade_prices: list[int] = Field(
        default_factory=lambda: [500, 1200, 2800, 6500, 15000],
        min_length=5,
        max_length=5,
        description="猪饲料从一级到五级的购买价格",
        json_schema_extra=_ui("饲料升级价格", "按一级到五级顺序填写五个正整数"),
    )
    cookware_upgrade_prices: list[int] = Field(
        default_factory=lambda: [500, 1200, 2800, 6500, 15000],
        min_length=5,
        max_length=5,
        description="厨具从一级到五级的购买价格",
        json_schema_extra=_ui("厨具升级价格", "按一级到五级顺序填写五个正整数"),
    )

    @field_validator("feed_upgrade_prices", "cookware_upgrade_prices")
    @classmethod
    def validate_upgrade_prices(cls, value: list[int]) -> list[int]:
        if any(int(price) <= 0 for price in value):
            raise ValueError("升级价格必须全部为正整数")
        return [int(price) for price in value]


class TradingSection(PluginConfigBase):
    """未来赠送与交易的安全边界。"""

    __ui_label__ = "赠送与交易"
    __ui_icon__ = "handshake"
    __ui_order__ = 80

    gift_enabled: bool = Field(
        default=False,
        description="是否启用同群赠送",
        json_schema_extra=_ui("启用赠送", "2A 尚未注册赠送命令，后续功能完成后再开启"),
    )
    trade_enabled: bool = Field(
        default=False,
        description="是否启用两阶段玩家交易",
        json_schema_extra=_ui("启用交易", "2A 尚未注册交易命令，后续功能完成后再开启"),
    )
    max_trade_price: int = Field(
        default=1000000,
        ge=1,
        le=2147483647,
        description="一笔玩家交易允许的最高猪币价格",
        json_schema_extra=_ui("单笔价格上限", "接收交易时还会重新检查余额和物品锁"),
    )
    offer_expiry_minutes: int = Field(
        default=5,
        ge=1,
        le=1440,
        description="待处理交易报价的有效分钟数",
        json_schema_extra=_ui("报价有效期", "过期后由维护流程解锁物品"),
    )


class RenderingSection(PluginConfigBase):
    """白色淡粉图片渲染设置。"""

    __ui_label__ = "图片展示"
    __ui_icon__ = "image"
    __ui_order__ = 90

    enabled: bool = Field(
        default=True,
        description="是否为后续业务结果生成图片",
        json_schema_extra=_ui("启用图片展示", "/抓猪帮助仍然保持纯文字"),
    )
    fallback_to_text: bool = Field(
        default=True,
        description="图片生成或发送失败时是否改发文字摘要",
        json_schema_extra=_ui("失败时发文字", "业务结算不会因图片故障而回滚"),
    )
    card_width: int = Field(
        default=1200,
        ge=800,
        le=1600,
        description="渲染卡片固定宽度",
        json_schema_extra=_ui("卡片宽度", "建议保持 1200，保证群聊缩略图可读"),
    )
    viewport_height: int = Field(
        default=1600,
        ge=600,
        le=5000,
        description="HTML 渲染视口高度",
        json_schema_extra=_ui("渲染视口高度", "长列表仍应分页，不依赖无限长截图"),
    )
    device_scale_factor: float = Field(
        default=1.0,
        ge=1.0,
        le=2.0,
        description="HTML 渲染设备像素倍率",
        json_schema_extra=_ui("像素倍率", "倍率越高图片越清晰，也会显著增加文件大小"),
    )
    render_timeout_ms: int = Field(
        default=15000,
        ge=1000,
        le=60000,
        description="单次 HTML 转图片的超时毫秒数",
        json_schema_extra=_ui("渲染超时", "超时后记录日志并按配置发送文字摘要"),
    )
    max_png_bytes: int = Field(
        default=12582912,
        ge=1024,
        le=52428800,
        description="允许发送的 PNG 最大字节数",
        json_schema_extra=_ui("图片大小上限", "默认 12 MiB，超出后触发文字兜底"),
    )
    font_family: str = Field(
        default='"Noto Sans CJK SC", "Microsoft YaHei", sans-serif',
        min_length=3,
        max_length=300,
        description="渲染图片使用的本地中文字体族",
        json_schema_extra=_ui("中文字体", "不从互联网加载字体；优先使用可分发的本地 CJK 字体"),
    )

    @field_validator("font_family")
    @classmethod
    def validate_font_family(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("中文字体配置不能为空")
        if any(character in normalized for character in "{};<>\\\r\n"):
            raise ValueError("中文字体配置包含不允许的 CSS 控制字符")
        return normalized


class MaintenanceSection(PluginConfigBase):
    """数据库与暂存目录维护。"""

    __ui_label__ = "运行维护"
    __ui_icon__ = "wrench"
    __ui_order__ = 100

    enabled: bool = Field(
        default=True,
        description="是否启动轻量后台维护任务",
        json_schema_extra=_ui("启用维护任务", "负责完整性检查、自动备份和过期暂存清理"),
    )
    interval_minutes: int = Field(
        default=60,
        ge=1,
        le=1440,
        description="后台维护循环的间隔分钟数",
        json_schema_extra=_ui("维护间隔", "最短 1 分钟；正常使用建议 60 分钟"),
    )
    run_integrity_check: bool = Field(
        default=True,
        description="维护时是否执行 SQLite 快速完整性检查",
        json_schema_extra=_ui("检查数据库完整性", "发现异常只记录，不静默修改未知问题"),
    )


class PigCatcherConfig(PluginConfigBase):
    """抓猪插件完整 2A 配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)
    access: AccessSection = Field(default_factory=AccessSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    assets: AssetsSection = Field(default_factory=AssetsSection)
    catching: CatchingSection = Field(default_factory=CatchingSection)
    cooking: CookingSection = Field(default_factory=CookingSection)
    economy: EconomySection = Field(default_factory=EconomySection)
    trading: TradingSection = Field(default_factory=TradingSection)
    rendering: RenderingSection = Field(default_factory=RenderingSection)
    maintenance: MaintenanceSection = Field(default_factory=MaintenanceSection)

    @model_validator(mode="after")
    def validate_cross_section_rules(self) -> PigCatcherConfig:
        self.catching.weights()
        if self.cooking.six_star_to_five_percent + self.cooking.six_star_to_six_percent != 100:
            raise ValueError("六星猪料理概率必须合计 100%")
        return self
