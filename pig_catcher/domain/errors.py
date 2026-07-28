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
