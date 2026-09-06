"""Integer-only luck packets: coins are conserved, never wagered or purchased."""

import math
from dataclasses import dataclass

from .errors import DomainValidationError

PACKET_MAX_COINS = 10_000_000
PACKET_MAX_COUNT = 100
PACKET_LIFETIME_HOURS = 24
PACKET_ACTIVE_LIMIT = 10


@dataclass(frozen=True, slots=True)
class PacketRequest:
    total: int
    count: int
    greeting: str = "祝大家猪运亨通！"


def parse_packet_request(text: str) -> PacketRequest:
    parts = str(text or "").strip().split(maxsplit=2)
    if len(parts) < 2 or not all(value.isascii() and value.isdecimal() for value in parts[:2]):
        raise DomainValidationError("用法：/发红包 总猪币数 红包个数 [祝福语]，例如 /发红包 1000 10")
    # Bound before int conversion, including Python's very-long-integer guard.
    if any(len(value) > 8 for value in parts[:2]):
        raise DomainValidationError("红包金额或个数过大。")
    total, count = map(int, parts[:2])
    validate_packet(total, count)
    greeting = parts[2].strip() if len(parts) > 2 else "祝大家猪运亨通！"
    if len(greeting) > 80 or any(ord(char) < 32 for char in greeting):
        raise DomainValidationError("红包祝福语限80字，不包含换行或控制字符。")
    return PacketRequest(total, count, greeting)


def validate_packet(total: int, count: int) -> None:
    if type(total) is not int or type(count) is not int:
        raise DomainValidationError("红包金额和个数必须是整数。")
    if not 1 <= total <= PACKET_MAX_COINS or not 1 <= count <= PACKET_MAX_COUNT:
        raise DomainValidationError("每包总额1～1000万猪币，拆成1～100个红包。")
    if total < count:
        raise DomainValidationError("每个红包至少1猪币，总额不能小于红包个数。")


def draw_packet_amount(remaining: int, count: int, roll: float) -> int:
    """Bounded double-mean draw; the final participant receives the remainder."""
    if count < 1 or remaining < count or not math.isfinite(roll) or not 0 <= roll < 1:
        raise ValueError("红包剩余状态或随机数无效。")
    if count == 1:
        return remaining
    upper = min(remaining - count + 1, max(1, 2 * remaining // count))
    return min(upper, int(roll * upper) + 1)
