"""显式斜杠命令的上下文与文字帮助。"""

from .context import extract_command_identity, matched_group
from .help import format_help

__all__ = ["extract_command_identity", "format_help", "matched_group"]
