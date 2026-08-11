"""MaiBot WebUI 可渲染的简体中文配置模型。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator, model_validator

from ..domain.quota import normalize_quota_refresh_hours
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


def _validate_group_id(value: str) -> str:
    normalized = str(value or "").strip()
    if ":" in normalized:
        raise ValueError("目标群号只填写群 ID，不要填写平台前缀")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError("目标群号包含不允许的控制字符")
    return normalized


def _validate_platform(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized and not all(character.isalnum() or character in {"-", "_"} for character in normalized):
        raise ValueError("平台标识只能包含字母、数字、短横线或下划线")
    return normalized


def _validate_user_ids(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        if len(value) > 320 or any(ord(character) < 32 for character in value):
            raise ValueError("成员 ID/OpenID 不合法")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _validate_scope_ids(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        platform, separator, group_id = value.partition(":")
        if (
            not separator
            or not platform
            or not group_id
            or ":" in group_id
            or _validate_platform(platform) != platform
            or _validate_group_id(group_id) != group_id
        ):
            raise ValueError("自动监管作用域必须使用 platform:group_id，例如 qq:237716658")
        if value not in normalized:
            normalized.append(value)
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
    framework_phase: Literal["6"] = Field(
        default=FRAMEWORK_PHASE,
        description="当前开发交付阶段",
        frozen=True,
        json_schema_extra=_ui("交付阶段", "第六轮生产验收完成，当前为正式运行版", disabled=True),
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
    catching_enabled: bool = Field(
        default=True,
        description="是否允许抓取猪猪",
        json_schema_extra=_ui("允许抓猪", "同时控制 /抓猪 和完全等价的 /抓群友"),
    )
    profile_enabled: bool = Field(
        default=True,
        description="是否允许查看个人抓猪档案",
        json_schema_extra=_ui("允许抓猪档案", "对应 /抓猪档案，展示经验、猪币、次数与收藏进度"),
    )
    inventory_enabled: bool = Field(
        default=True,
        description="是否允许查看猪猪背包和详情",
        json_schema_extra=_ui("允许背包与详情", "对应 /猪猪背包 和 /猪猪详情（兼容 /抓猪详情）"),
    )
    catalog_enabled: bool = Field(
        default=True,
        description="是否允许查看猪猪图鉴",
        json_schema_extra=_ui("允许猪猪图鉴", "对应 /猪猪图鉴；未发现群专属素材不会泄露"),
    )
    records_enabled: bool = Field(
        default=True,
        description="是否允许查看当前群猪猪纪录",
        json_schema_extra=_ui("允许群纪录", "对应 /猪猪纪录；数据严格按平台和群隔离"),
    )
    items_enabled: bool = Field(
        default=True,
        description="是否允许装备和取消抓猪或做菜道具",
        json_schema_extra=_ui("允许使用道具", "对应 /使用道具 和 /取消道具，兼容抓猪与做菜"),
    )
    cooking_enabled: bool = Field(
        default=True,
        description="是否允许把当前持有的猪制作成美食",
        json_schema_extra=_ui("允许做菜", "对应 /做菜；成功后原料猪与做菜道具会原子消耗"),
    )
    food_inventory_enabled: bool = Field(
        default=True,
        description="是否允许查看美食背包和详情",
        json_schema_extra=_ui("允许美食背包与详情", "对应 /美食背包 和 /美食详情"),
    )
    food_catalog_enabled: bool = Field(
        default=True,
        description="是否允许查看美食图鉴",
        json_schema_extra=_ui("允许美食图鉴", "对应 /美食图鉴；未发现群专属美食不会泄露"),
    )
    eating_enabled: bool = Field(
        default=True,
        description="是否允许食用当前持有的美食",
        json_schema_extra=_ui("允许吃菜", "对应 /吃菜 和 /使用美食；成功后美食会被消耗"),
    )
    store_enabled: bool = Field(
        default=True,
        description="是否允许查看商城并购买道具或永久升级",
        json_schema_extra=_ui("允许商城与购买", "对应 /猪猪商城、/购买 和 /升级"),
    )
    selling_enabled: bool = Field(
        default=True,
        description="是否允许按官方价值售卖猪或美食",
        json_schema_extra=_ui(
            "允许官方售卖",
            "对应 /售卖猪猪、/售卖美食 和 /批量售卖；图鉴不会减少",
        ),
    )
    ledger_enabled: bool = Field(
        default=True,
        description="是否允许查看个人猪币流水和对账状态",
        json_schema_extra=_ui("允许猪币账本", "对应 /猪币账本，仅显示当前群个人流水"),
    )
    showcase_enabled: bool = Field(
        default=True,
        description="是否允许设置排行榜展示猪和展示美食",
        json_schema_extra=_ui("允许设置展示", "对应 /设置展示；展示物始终限制在当前群个人资产"),
    )
    ranking_enabled: bool = Field(
        default=True,
        description="是否允许查看当前群七类排行榜",
        json_schema_extra=_ui("允许猪猪排行", "对应 /猪猪排行；不支持跨群查询"),
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
        description="允许执行素材与维护管理操作的平台用户身份",
        json_schema_extra=_ui(
            "插件管理员",
            "每行一个身份；NapCat 可填 QQ 号，QQ 官方机器人需填成员 OpenID，也支持 platform:user_id",
        ),
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
    max_animation_frames: int = Field(
        default=300,
        ge=2,
        le=2000,
        description="单个动画素材允许的最大帧数",
        json_schema_extra=_ui("动画帧数上限", "当前正式素材最多 96 帧；默认上限保留扩展余量"),
    )
    max_animation_duration_ms: int = Field(
        default=30000,
        ge=100,
        le=600000,
        description="单个动画素材显式帧时长合计上限",
        json_schema_extra=_ui("动画时长上限", "缺少帧时长的原素材不会被篡改，卡片合成时使用兼容回退"),
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
    """抓猪分时额度、冷却和六档基础权重。"""

    __ui_label__ = "抓猪规则"
    __ui_icon__ = "target"
    __ui_order__ = 50

    daily_limit: int = Field(
        default=5,
        ge=1,
        le=1000,
        description="每位玩家在每个群每个刷新窗口可成功抓取的次数",
        json_schema_extra=_ui("每时段抓猪次数", "默认 5 次；00:00、09:00、12:00、19:00 分别重新计数"),
    )
    cooldown_seconds: int = Field(
        default=20,
        ge=0,
        le=86400,
        description="同一玩家两次抓猪之间的最短秒数",
        json_schema_extra=_ui("抓猪冷却", "默认 20 秒；设置 0 表示不限制，刷新窗口切换或手动重置后旧冷却失效"),
    )
    quota_refresh_hours: list[int] = Field(
        default_factory=lambda: [0, 9, 12, 19],
        min_length=1,
        max_length=24,
        description="北京时间每天重新开放抓猪额度的整点小时",
        json_schema_extra=_ui(
            "额度刷新小时",
            "默认 0、9、12、19；每个时段单独计算次数，必须包含 0 且不能重复",
        ),
    )
    daily_reset_timezone: Literal["Asia/Shanghai"] = Field(
        default="Asia/Shanghai",
        frozen=True,
        description="抓猪分时额度刷新所使用的时区",
        json_schema_extra=_ui("额度刷新时区", "固定按北京时间刷新，不依赖服务器时区", disabled=True),
    )
    inventory_page_size: int = Field(
        default=12,
        ge=4,
        le=16,
        description="猪猪背包每页显示数量",
        json_schema_extra=_ui("背包每页数量", "默认 12，只影响展示，不改变资产数据"),
    )
    catalog_page_size: int = Field(
        default=12,
        ge=6,
        le=20,
        description="旧版猪猪图鉴分页数量，仅为配置兼容保留",
        json_schema_extra=_ui(
            "旧版图鉴每页数量",
            "图鉴现按品质一次展示全部内容，此项仅兼容旧配置",
            disabled=True,
        ),
    )
    records_page_size: int = Field(
        default=10,
        ge=5,
        le=20,
        description="群纪录每页显示数量",
        json_schema_extra=_ui("纪录每页数量", "每个模板的体型与重量纪录分别占一行"),
    )
    rarity_1_weight: float = Field(
        default=40.0,
        ge=0,
        le=10000,
        description="一星普通家养猪的基础权重",
        json_schema_extra=_ui("一星权重", "默认 40；六档会在运行时统一归一化"),
    )
    rarity_2_weight: float = Field(
        default=30.0,
        ge=0,
        le=10000,
        description="二星美味家养猪的基础权重",
        json_schema_extra=_ui("二星权重", "默认 30；六档会在运行时统一归一化"),
    )
    rarity_3_weight: float = Field(
        default=17.0,
        ge=0,
        le=10000,
        description="三星优质家养猪的基础权重",
        json_schema_extra=_ui("三星权重", "默认 17；六档会在运行时统一归一化"),
    )
    rarity_4_weight: float = Field(
        default=8.0,
        ge=0,
        le=10000,
        description="四星极品佳肴猪的基础权重",
        json_schema_extra=_ui("四星权重", "默认 8；六档会在运行时统一归一化"),
    )
    rarity_5_weight: float = Field(
        default=4.0,
        ge=0,
        le=10000,
        description="五星传说珍馐猪的基础权重",
        json_schema_extra=_ui("五星权重", "默认 4；无六星素材时会接收六星权重"),
    )
    rarity_6_weight: float = Field(
        default=1.0,
        ge=0,
        le=10000,
        description="六星可爱猪群友的基础权重",
        json_schema_extra=_ui("六星权重", "默认 1；当前群没有授权素材时不会抽取"),
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

    @field_validator("quota_refresh_hours")
    @classmethod
    def validate_quota_refresh_hours(cls, value: list[int]) -> list[int]:
        return list(normalize_quota_refresh_hours(value))


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
        json_schema_extra=_ui(
            "六星猪出五星菜",
            "基础概率固定 90%；仅一次性高星美食效果可临时调整",
            disabled=True,
        ),
    )
    six_star_to_six_percent: Literal[10] = Field(
        default=10,
        frozen=True,
        description="六星猪做出六星菜的固定百分比",
        json_schema_extra=_ui(
            "六星猪出六星菜",
            "基础概率固定 10%，且不会出现其他品质；特定美食可单次提升",
            disabled=True,
        ),
    )
    inventory_page_size: int = Field(
        default=12,
        ge=4,
        le=16,
        description="美食背包每页显示数量",
        json_schema_extra=_ui("美食背包每页数量", "默认 12，只影响展示，不改变资产数据"),
    )
    catalog_page_size: int = Field(
        default=12,
        ge=6,
        le=20,
        description="旧版美食图鉴分页数量，仅为配置兼容保留",
        json_schema_extra=_ui(
            "旧版美食图鉴每页数量",
            "美食图鉴现按品质一次展示全部内容，此项仅兼容旧配置",
            disabled=True,
        ),
    )
    cook_cooldown_seconds: int = Field(
        default=10,
        ge=0,
        le=3600,
        description="单次做菜冷却秒数，用于防止连续做菜",
        json_schema_extra=_ui(
            "做菜冷却",
            "每次做菜后需等待该秒数才能再次做菜或批量做菜",
            disabled=False,
        ),
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
    store_page_size: int = Field(
        default=16,
        ge=4,
        le=16,
        description="旧版商城分页数量，仅为配置兼容保留",
        json_schema_extra=_ui(
            "旧版商城每页数量",
            "商城现固定单页显示全部商品，此项仅兼容旧配置",
            disabled=True,
        ),
    )
    ledger_page_size: int = Field(
        default=10,
        ge=5,
        le=20,
        description="个人猪币账本每页显示的流水数量",
        json_schema_extra=_ui("账本每页数量", "默认 10，并显示余额与流水合计对账结果"),
    )
    max_purchase_quantity: int = Field(
        default=99,
        ge=1,
        le=10000,
        description="一次购买消耗品允许的最大数量",
        json_schema_extra=_ui("单次购买上限", "只限制一次性道具；永久升级每次固定购买一级"),
    )

    @field_validator("feed_upgrade_prices", "cookware_upgrade_prices")
    @classmethod
    def validate_upgrade_prices(cls, value: list[int]) -> list[int]:
        if any(int(price) <= 0 for price in value):
            raise ValueError("升级价格必须全部为正整数")
        return [int(price) for price in value]


class TradingSection(PluginConfigBase):
    """同群赠送与双方确认交易的安全边界。"""

    __ui_label__ = "赠送与交易"
    __ui_icon__ = "handshake"
    __ui_order__ = 80

    gift_enabled: bool = Field(
        default=True,
        description="是否启用同群赠送",
        json_schema_extra=_ui("启用赠送", "启用 /猪猪赠送 和 /美食赠送；赠送立即原子转移"),
    )
    trade_enabled: bool = Field(
        default=True,
        description="是否启用两阶段玩家交易",
        json_schema_extra=_ui("启用交易", "启用报价、接受、拒绝、取消和我的交易"),
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
    trade_page_size: int = Field(
        default=8,
        ge=4,
        le=16,
        description="我的交易列表每页显示数量",
        json_schema_extra=_ui("交易列表每页数量", "默认 8，按创建时间倒序展示"),
    )


class RegulationSection(PluginConfigBase):
    """赠送与成交交易共用的群级自动监管策略。"""

    __ui_label__ = "自动监管"
    __ui_icon__ = "shield-alert"
    __ui_order__ = 82

    mode: Literal["关闭", "仅提醒", "自动执行"] = Field(
        default="自动执行",
        description="自动监管运行模式",
        json_schema_extra=_ui(
            "监管模式",
            "群内只发送行为提醒，不公开风险分、阈值或证据；仅提醒模式不会自动限制",
        ),
    )
    enabled_scope_ids: list[str] = Field(
        default_factory=lambda: [
            "qq:237716658",
            "qq-official:9EA2810F378FBD7DC3219C56CEAB3520",
        ],
        max_length=20,
        description="启用自动监管的精确平台群作用域",
        json_schema_extra=_ui(
            "启用作用域",
            "每行一个 platform:group_id；不同平台与群完全独立计算",
        ),
    )
    lookback_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="分析赠送与成交资产流向的滚动天数",
        json_schema_extra=_ui("分析窗口", "默认回看 7 天；只处理启用作用域中的流转"),
    )
    warning_score: int = Field(
        default=40,
        ge=1,
        le=200,
        description="创建内部监管案件并提醒的最低分",
        json_schema_extra=_ui(
            "内部提醒阈值",
            "仅管理员可见；群内消息不会显示分数、阈值或计算方式",
        ),
    )
    chat_activity_lookback_days: int = Field(
        default=30,
        ge=7,
        le=90,
        description="判断账号群聊活跃度时回看的滚动天数",
        json_schema_extra=_ui(
            "账号活跃窗口",
            "只使用消息数和活跃日期，不分析或保存聊天正文",
        ),
    )
    chat_activity_message_limit: int = Field(
        default=5000,
        ge=100,
        le=20000,
        description="一次账号活跃度快照最多读取的群消息条数",
        json_schema_extra=_ui(
            "活跃消息上限",
            "取窗口内最近消息；超大群可适度提高，但会增加一次查询开销",
        ),
    )
    established_min_messages: int = Field(
        default=30,
        ge=1,
        le=1000,
        description="认定群聊正常活跃账号所需的最低非命令消息数",
        json_schema_extra=_ui(
            "正常账号消息数",
            "还必须同时达到活跃天数；仅用于放宽双方互赠，不豁免异常交易",
        ),
    )
    established_min_active_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="认定群聊正常活跃账号所需的最低活跃天数",
        json_schema_extra=_ui(
            "正常账号活跃天数",
            "与消息数同时满足才会放宽双方历史互赠",
        ),
    )
    likely_alt_max_messages: int = Field(
        default=5,
        ge=0,
        le=100,
        description="疑似小号允许的群聊消息数上限",
        json_schema_extra=_ui(
            "小号消息上限",
            "还需同时满足低活跃天数、短插件账号年龄和低游戏操作数",
        ),
    )
    likely_alt_max_active_days: int = Field(
        default=2,
        ge=0,
        le=30,
        description="疑似小号允许的群聊活跃天数上限",
        json_schema_extra=_ui("小号活跃天数", "四项低活跃条件必须全部满足才会严查"),
    )
    likely_alt_max_plugin_age_days: int = Field(
        default=7,
        ge=0,
        le=90,
        description="疑似小号允许的抓猪插件账号年龄上限",
        json_schema_extra=_ui("小号插件年龄", "账号更老时不会仅因少发言被归为疑似小号"),
    )
    likely_alt_max_game_actions: int = Field(
        default=10,
        ge=0,
        le=1000,
        description="疑似小号允许的累计抓猪加做菜次数上限",
        json_schema_extra=_ui("小号游戏操作数", "与群聊活跃度及账号年龄联合判断"),
    )
    notice_cooldown_minutes: int = Field(
        default=10,
        ge=1,
        le=1440,
        description="限制期间重复尝试的提醒与升级最短间隔",
        json_schema_extra=_ui("重复提醒间隔", "防止连点命令造成刷屏或瞬间多级升级"),
    )
    social_hold_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="多次提醒后赠送与交易功能限制时长",
        json_schema_extra=_ui("社交限制时长", "默认 24 小时，到期自动恢复"),
    )
    plugin_hold_hours: int = Field(
        default=72,
        ge=1,
        le=720,
        description="首次插件临时封禁时长",
        json_schema_extra=_ui("首次插件封禁", "默认 72 小时；配置管理员不自动封禁"),
    )
    repeat_ban_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="三十天内再次进入插件封禁时长",
        json_schema_extra=_ui("再次封禁", "默认 7 天；历史处罚与新证据均保留审计"),
    )
    severe_repeat_ban_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="九十天内第三次进入插件封禁时长",
        json_schema_extra=_ui("严重重复封禁", "默认 30 天；永久封禁仍只能人工确认"),
    )

    @field_validator("enabled_scope_ids")
    @classmethod
    def validate_enabled_scope_ids(cls, value: list[str]) -> list[str]:
        return _validate_scope_ids(value)

    @model_validator(mode="after")
    def validate_account_activity_thresholds(self) -> RegulationSection:
        if self.established_min_active_days > self.chat_activity_lookback_days:
            raise ValueError("正常账号活跃天数不能大于账号活跃窗口")
        if self.likely_alt_max_active_days > self.chat_activity_lookback_days:
            raise ValueError("小号活跃天数不能大于账号活跃窗口")
        if self.likely_alt_max_messages >= self.established_min_messages:
            raise ValueError("小号消息上限必须小于正常账号最低消息数")
        if self.likely_alt_max_active_days >= self.established_min_active_days:
            raise ValueError("小号活跃天数必须小于正常账号最低活跃天数")
        return self


class RankingSection(PluginConfigBase):
    """群纪录、体格标签和排行榜规则。"""

    __ui_label__ = "展示与排行"
    __ui_icon__ = "trophy"
    __ui_order__ = 85

    ranking_page_size: int = Field(
        default=10,
        ge=5,
        le=20,
        description="每页排行榜显示人数",
        json_schema_extra=_ui("排行每页人数", "默认 10；第一页突出前三名，其余使用紧凑账簿行"),
    )
    pig_catalog_weight_percent: int = Field(
        default=60,
        ge=0,
        le=100,
        description="综合榜中猪猪图鉴完成率权重",
        json_schema_extra=_ui("猪猪图鉴权重", "默认 60%，与美食图鉴权重之和必须为 100%"),
    )
    food_catalog_weight_percent: int = Field(
        default=40,
        ge=0,
        le=100,
        description="综合榜中美食图鉴完成率权重",
        json_schema_extra=_ui("美食图鉴权重", "默认 40%，与猪猪图鉴权重之和必须为 100%"),
    )
    giant_size_threshold_cm: float = Field(
        default=120.0,
        gt=0,
        le=10000,
        description="写入全群巨物目击记录的绝对体型门槛",
        json_schema_extra=_ui("巨物体型门槛", "默认 120 cm；达到体型或重量任一门槛就会留档"),
    )
    giant_weight_threshold_kg: float = Field(
        default=350.0,
        gt=0,
        le=100000,
        description="写入全群巨物目击记录的绝对重量门槛",
        json_schema_extra=_ui("巨物重量门槛", "默认 350 kg；双项同时达到会显示更高整活等级"),
    )
    giant_sightings_limit: int = Field(
        default=6,
        ge=1,
        le=20,
        description="群纪录首页显示的最近巨物目击数量",
        json_schema_extra=_ui("巨物目击展示数", "只影响 /猪猪纪录 首页，不删除历史记录"),
    )


class RenderingSection(PluginConfigBase):
    """白色淡粉图片渲染设置。"""

    __ui_label__ = "图片展示"
    __ui_icon__ = "image"
    __ui_order__ = 90

    enabled: bool = Field(
        default=True,
        description="是否为业务结果生成图片",
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
    max_animation_bytes: int = Field(
        default=52428800,
        ge=1024,
        le=104857600,
        description="合成后 GIF 动画卡片的最大字节数",
        json_schema_extra=_ui("动画卡片大小上限", "默认 50 MiB；超过后记录失败并走纯文字兜底"),
    )
    missing_frame_duration_ms: int = Field(
        default=100,
        ge=10,
        le=10000,
        description="原动画未声明帧时长时，合成卡片采用的兼容时长",
        json_schema_extra=_ui("缺失帧时长回退", "只影响合成卡片播放；素材库中的原文件保持逐字节不变"),
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


class QuotaAdministrationSection(PluginConfigBase):
    """指定群抓猪额度的审计重置入口。"""

    __ui_label__ = "额度重置"
    __ui_icon__ = "timer-reset"
    __ui_order__ = 95

    group_id: str = Field(
        default="",
        max_length=128,
        description="需要重置当前抓猪时段的群号",
        json_schema_extra=_ui(
            "目标群号",
            "只填写一个精确群号，例如 123456789；不会重置其他群",
            placeholder="填写需要重置的群号",
        ),
    )
    platform: str = Field(
        default="",
        max_length=40,
        description="同一群号在多个平台重复时用于精确选择平台",
        json_schema_extra=_ui(
            "平台（可选）",
            "通常留空自动识别；需要时填写 qq 或 qq-official",
            placeholder="留空自动识别",
        ),
    )
    execute_current_window_reset: bool = Field(
        default=False,
        description="保存配置后立即备份数据库并重置指定群当前时段额度",
        json_schema_extra=_ui(
            "重置当前时段",
            "填写群号后打开并保存；成功后会写审计记录并自动恢复为关闭",
        ),
    )
    boost_window_limit: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="提额度数；大于 0 时保存后为指定群当前时段提升额度并重置",
        json_schema_extra=_ui(
            "提额度数",
            "如 15；大于 0 时保存后立即为指定群当前时段提额并重置该群额度，窗口切换后自动恢复；0 表示不提升",
        ),
    )

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str) -> str:
        return _validate_group_id(value)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        return _validate_platform(value)


class BlacklistAdministrationSection(PluginConfigBase):
    """赠送/收赠与交易黑名单的一次性管理入口。"""

    __ui_label__ = "社交黑名单"
    __ui_icon__ = "shield-ban"
    __ui_order__ = 96

    group_id: str = Field(
        default="",
        max_length=128,
        description="需要管理黑名单的精确群号",
        json_schema_extra=_ui(
            "目标群号",
            "只填写一个精确群号；所有成员必须已经在该群使用过抓猪插件",
            placeholder="填写群号或 QQ 官方群 OpenID",
        ),
    )
    platform: str = Field(
        default="",
        max_length=40,
        description="同一群号在多个平台重复时用于精确选择平台",
        json_schema_extra=_ui(
            "平台（可选）",
            "通常留空自动识别；需要时填写 qq 或 qq-official",
            placeholder="留空自动识别",
        ),
    )
    user_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="需要加入或解除黑名单的成员 ID/OpenID",
        json_schema_extra=_ui(
            "成员 ID/OpenID",
            "每行一个；QQ 官方填写成员 OpenID，不接受昵称",
        ),
    )
    gift_action: Literal["不操作", "加入黑名单", "解除黑名单"] = Field(
        default="不操作",
        description="对赠送与收赠黑名单执行的动作",
        json_schema_extra=_ui(
            "赠送/收赠黑名单",
            "命中后不能赠送，也不能接收任何猪猪或美食",
        ),
    )
    trade_action: Literal["不操作", "加入黑名单", "解除黑名单"] = Field(
        default="不操作",
        description="对猪猪与美食交易黑名单执行的动作",
        json_schema_extra=_ui(
            "交易黑名单",
            "加入后会同时取消相关待处理交易并释放资产锁",
        ),
    )
    reason: str = Field(
        default="管理面板人工复核",
        min_length=1,
        max_length=500,
        description="写入审计记录的操作原因",
        json_schema_extra=_ui(
            "操作原因",
            "请填写可供后续复核的原因，不会发送到群聊",
            **{"input_type": "textarea", "x-widget": "textarea"},
        ),
    )
    execute_blacklist_update: bool = Field(
        default=False,
        description="保存配置后立即备份数据库并执行本次黑名单变更",
        json_schema_extra=_ui(
            "执行黑名单变更",
            "确认群号、成员和动作后打开并保存；触发开关会立即自动关闭",
        ),
    )

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str) -> str:
        return _validate_group_id(value)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        return _validate_platform(value)

    @field_validator("user_ids")
    @classmethod
    def validate_user_ids(cls, value: list[str]) -> list[str]:
        return _validate_user_ids(value)


class AnnouncementAdministrationSection(PluginConfigBase):
    """通过最近活跃聊天流发送一次公告。"""

    __ui_label__ = "群公告发送"
    __ui_icon__ = "megaphone"
    __ui_order__ = 97

    group_id: str = Field(
        default="",
        max_length=128,
        description="需要发送公告的精确群号",
        json_schema_extra=_ui(
            "目标群号",
            "使用该群最近活跃的 MaiBot 聊天流和机器人线路",
            placeholder="填写群号或 QQ 官方群 OpenID",
        ),
    )
    platform: str = Field(
        default="",
        max_length=40,
        description="同一群号在多个平台重复时用于精确选择平台",
        json_schema_extra=_ui(
            "平台（可选）",
            "通常留空自动识别；需要时填写 qq 或 qq-official",
            placeholder="留空自动识别",
        ),
    )
    content: str = Field(
        default="",
        max_length=4000,
        description="要由机器人发送到目标群的公告正文",
        json_schema_extra=_ui(
            "公告正文",
            "QQ 官方机器人默认需要目标群 5 分钟内存在可用的被动回复上下文",
            placeholder="填写公告正文",
            **{"input_type": "textarea", "x-widget": "textarea"},
        ),
    )
    execute_send: bool = Field(
        default=False,
        description="保存配置后立即尝试向目标群发送一次公告",
        json_schema_extra=_ui(
            "立即发送公告",
            "发送前会先关闭触发开关并写审计记录；失败不会自动重试或重复发送",
        ),
    )

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str) -> str:
        return _validate_group_id(value)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        return _validate_platform(value)


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
    """抓猪插件第五轮完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)
    access: AccessSection = Field(default_factory=AccessSection)
    storage: StorageSection = Field(default_factory=StorageSection)
    assets: AssetsSection = Field(default_factory=AssetsSection)
    catching: CatchingSection = Field(default_factory=CatchingSection)
    cooking: CookingSection = Field(default_factory=CookingSection)
    economy: EconomySection = Field(default_factory=EconomySection)
    trading: TradingSection = Field(default_factory=TradingSection)
    regulation: RegulationSection = Field(default_factory=RegulationSection)
    ranking: RankingSection = Field(default_factory=RankingSection)
    rendering: RenderingSection = Field(default_factory=RenderingSection)
    quota_administration: QuotaAdministrationSection = Field(
        default_factory=QuotaAdministrationSection
    )
    blacklist_administration: BlacklistAdministrationSection = Field(
        default_factory=BlacklistAdministrationSection
    )
    announcement_administration: AnnouncementAdministrationSection = Field(
        default_factory=AnnouncementAdministrationSection
    )
    maintenance: MaintenanceSection = Field(default_factory=MaintenanceSection)

    @model_validator(mode="after")
    def validate_cross_section_rules(self) -> PigCatcherConfig:
        self.catching.weights()
        if (
            self.quota_administration.execute_current_window_reset
            and not self.quota_administration.group_id
        ):
            raise ValueError("执行额度重置前必须填写目标群号")
        blacklist = self.blacklist_administration
        if blacklist.execute_blacklist_update:
            if not blacklist.group_id:
                raise ValueError("执行黑名单变更前必须填写目标群号")
            if not blacklist.user_ids:
                raise ValueError("执行黑名单变更前必须填写成员 ID/OpenID")
            if blacklist.gift_action == "不操作" and blacklist.trade_action == "不操作":
                raise ValueError("执行黑名单变更前至少选择一种加入或解除动作")
        announcement = self.announcement_administration
        if announcement.execute_send:
            if not announcement.group_id:
                raise ValueError("发送公告前必须填写目标群号")
            if not announcement.content.strip():
                raise ValueError("发送公告前必须填写公告正文")
        if (
            self.quota_administration.execute_current_window_reset
            or blacklist.execute_blacklist_update
            or announcement.execute_send
        ) and not self.plugin.enabled:
            raise ValueError("执行控制面板操作前必须先启用抓猪插件")
        if self.cooking.six_star_to_five_percent + self.cooking.six_star_to_six_percent != 100:
            raise ValueError("六星猪料理概率必须合计 100%")
        if (
            self.ranking.pig_catalog_weight_percent
            + self.ranking.food_catalog_weight_percent
            != 100
        ):
            raise ValueError("综合榜的猪猪与美食图鉴权重必须合计 100%")
        return self
