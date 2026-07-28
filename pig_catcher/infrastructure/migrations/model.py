"""迁移值对象。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    """一个只能向前执行的 SQLite 迁移。"""

    version: int
    name: str
    statements: tuple[str, ...]
