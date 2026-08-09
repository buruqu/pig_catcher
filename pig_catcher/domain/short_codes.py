"""Case-insensitive, human-friendly asset short-code rules."""

from __future__ import annotations

import re
import secrets

from .errors import SelectorValidationError

SHORT_CODE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SHORT_CODE_MIN_LENGTH = 4
SHORT_CODE_MAX_LENGTH = 16
GENERATED_SHORT_CODE_LENGTH = 8

_SHORT_CODE_PATTERN = re.compile(
    rf"^[A-Za-z0-9]{{{SHORT_CODE_MIN_LENGTH},{SHORT_CODE_MAX_LENGTH}}}$"
)


def normalize_short_code(value: str) -> str:
    """Validate one asset code and return its case-insensitive canonical form."""

    normalized = str(value or "").strip()
    if not _SHORT_CODE_PATTERN.fullmatch(normalized):
        raise SelectorValidationError(
            "资产短编号必须由 4 至 16 位英文字母或数字组成，不区分大小写。"
        )
    return normalized.upper()


def is_valid_short_code(value: str) -> bool:
    """Return whether a value is a supported asset short code."""

    try:
        normalize_short_code(value)
    except SelectorValidationError:
        return False
    return True


def new_short_code() -> str:
    """Generate an eight-character candidate from the full base-36 alphabet."""

    return "".join(
        secrets.choice(SHORT_CODE_ALPHABET)
        for _ in range(GENERATED_SHORT_CODE_LENGTH)
    )
