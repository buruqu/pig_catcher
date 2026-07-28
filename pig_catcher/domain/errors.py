"""领域与应用层可预期错误。"""


class PigCatcherError(Exception):
    """抓猪插件错误基类。"""


class DomainValidationError(PigCatcherError, ValueError):
    """输入违反领域约束。"""


class ScopeValidationError(DomainValidationError):
    """平台或群范围不合法。"""


class SelectorValidationError(DomainValidationError):
    """资产选择器不合法。"""


class MissingMessageIdError(DomainValidationError):
    """改变状态的命令缺少稳定消息 ID。"""


class ConfigurationError(PigCatcherError):
    """运行配置无法安全应用。"""


class DatabaseError(PigCatcherError):
    """插件数据库操作失败。"""


class DatabaseNotOpenError(DatabaseError):
    """数据库尚未打开。"""


class MigrationError(DatabaseError):
    """数据库迁移失败或版本不兼容。"""


class ReceiptConflictError(DatabaseError):
    """同一幂等键对应了不同业务请求。"""


class AssetValidationError(PigCatcherError, ValueError):
    """素材清单或图片不符合导入规范。"""


class AssetImportError(PigCatcherError):
    """素材已校验但持久化或激活失败。"""


class RenderError(PigCatcherError):
    """HTML 或 PNG 渲染结果不可用。"""


class CommandContextError(PigCatcherError, ValueError):
    """命令缺少群聊身份或消息上下文。"""


class GameplayError(PigCatcherError):
    """第三轮玩法中可直接向用户说明的业务错误。"""


class NoDrawableTemplateError(GameplayError):
    """当前群没有可用于抓取的猪模板。"""


class DailyCatchLimitError(GameplayError):
    """玩家已达到当前自然日抓取上限。"""


class CatchCooldownError(GameplayError):
    """玩家仍处于抓猪冷却。"""

    def __init__(self, remaining_seconds: int) -> None:
        self.remaining_seconds = max(1, int(remaining_seconds))
        super().__init__(f"抓猪冷却中，还需等待 {self.remaining_seconds} 秒。")


class PigNotFoundError(GameplayError):
    """当前玩家的有效猪库存中找不到选择目标。"""


class AmbiguousPigSelectorError(GameplayError):
    """同名猪不唯一，需要短编号。"""


class ItemInventoryError(GameplayError):
    """玩家没有可装备或消耗的对应道具。"""


class FoodNotFoundError(GameplayError):
    """当前玩家的有效美食库存中找不到选择目标。"""


class AmbiguousFoodSelectorError(GameplayError):
    """同名美食不唯一，需要短编号。"""


class CookingTemplateError(GameplayError):
    """当前群缺少本次料理结果所需的美食模板。"""


class AssetStateConflictError(GameplayError):
    """资产已被消耗、售卖或锁定，不能执行当前操作。"""


class InsufficientBalanceError(GameplayError):
    """玩家猪币余额不足。"""


class UpgradeLimitError(GameplayError):
    """永久升级已经达到最高等级。"""


class StoreProductError(GameplayError):
    """商城商品或购买数量无效。"""


class FoodEffectError(GameplayError):
    """美食声明了当前规则版本无法安全应用的效果。"""


class LedgerReconciliationError(GameplayError):
    """玩家余额与不可变流水无法对账。"""
