"""幂等收据应用服务。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ..domain.models import CommandReceipt, ReceiptReservation
from ..domain.ports import Clock, SystemClock
from ..infrastructure.database import PigCatcherDatabase
from ..infrastructure.repositories import ReceiptRepository


def request_fingerprint(payload: Mapping[str, Any]) -> str:
    """对结构化业务参数生成稳定指纹。"""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iso_timestamp(clock: Clock) -> str:
    return clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ReceiptService:
    """让幂等预留和发送领取分别在独立事务中完成。"""

    def __init__(
        self,
        database: PigCatcherDatabase,
        *,
        repository: ReceiptRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or ReceiptRepository()
        self.clock = clock or SystemClock()

    async def reserve(
        self,
        *,
        idempotency_key: str,
        scope_id: str,
        player_id: str | None,
        command_name: str,
        request_payload: Mapping[str, Any],
        result_type: str,
        result_object_id: str = "",
        result_payload: Mapping[str, Any] | None = None,
        text_summary: str,
    ) -> ReceiptReservation:
        now = _iso_timestamp(self.clock)
        async with self.database.transaction() as session:
            return await self.repository.reserve(
                session,
                idempotency_key=idempotency_key,
                scope_id=scope_id,
                player_id=player_id,
                command_name=command_name,
                request_fingerprint=request_fingerprint(request_payload),
                result_type=result_type,
                result_object_id=result_object_id,
                result_json=json.dumps(
                    dict(result_payload or {}),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                text_summary=text_summary,
                now=now,
            )

    async def get_by_key(self, idempotency_key: str) -> CommandReceipt | None:
        async with self.database.transaction(immediate=False) as session:
            return await self.repository.get_by_key(session, idempotency_key)

    async def claim_send(self, receipt_id: str) -> bool:
        async with self.database.transaction() as session:
            return await self.repository.claim_send(
                session,
                receipt_id=receipt_id,
                now=_iso_timestamp(self.clock),
            )

    async def mark_sent(self, receipt_id: str) -> bool:
        async with self.database.transaction() as session:
            return await self.repository.mark_sent(
                session,
                receipt_id=receipt_id,
                now=_iso_timestamp(self.clock),
            )

    async def mark_failed(self, receipt_id: str, error: str) -> bool:
        async with self.database.transaction() as session:
            return await self.repository.mark_failed(
                session,
                receipt_id=receipt_id,
                error=error,
                now=_iso_timestamp(self.clock),
            )
