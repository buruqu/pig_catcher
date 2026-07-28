"""幂等命令收据仓储。"""

from __future__ import annotations

from uuid import uuid4

import aiosqlite

from ...domain.enums import ReceiptSendStatus
from ...domain.errors import ReceiptConflictError
from ...domain.models import CommandReceipt, ReceiptReservation
from ..database import DatabaseSession


def _receipt_from_row(row: aiosqlite.Row) -> CommandReceipt:
    return CommandReceipt(
        receipt_id=str(row["receipt_id"]),
        idempotency_key=str(row["idempotency_key"]),
        scope_id=str(row["scope_id"]),
        command_name=str(row["command_name"]),
        request_fingerprint=str(row["request_fingerprint"]),
        result_type=str(row["result_type"]),
        result_object_id=str(row["result_object_id"]),
        text_summary=str(row["text_summary"]),
        send_status=ReceiptSendStatus(str(row["send_status"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class ReceiptRepository:
    """创建、领取和完成收据，不拥有事务。"""

    async def reserve(
        self,
        session: DatabaseSession,
        *,
        idempotency_key: str,
        scope_id: str,
        player_id: str | None,
        command_name: str,
        request_fingerprint: str,
        result_type: str,
        result_object_id: str,
        result_json: str,
        text_summary: str,
        now: str,
    ) -> ReceiptReservation:
        receipt_id = uuid4().hex
        cursor = await session.execute(
            """
            INSERT INTO command_receipts(
                receipt_id, idempotency_key, scope_id, player_id, command_name,
                request_fingerprint, result_type, result_object_id, result_json,
                text_summary, send_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (
                receipt_id,
                idempotency_key,
                scope_id,
                player_id,
                command_name,
                request_fingerprint,
                result_type,
                result_object_id,
                result_json,
                text_summary,
                now,
                now,
            ),
        )
        row = await session.fetch_one(
            "SELECT * FROM command_receipts WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        if row is None:
            raise ReceiptConflictError("幂等收据写入后无法读取。")
        receipt = _receipt_from_row(row)
        if receipt.scope_id != scope_id or receipt.command_name != command_name:
            raise ReceiptConflictError("同一幂等键已经被其他范围或命令占用。")
        if receipt.request_fingerprint != request_fingerprint:
            raise ReceiptConflictError("同一消息 ID 对应了不同的业务参数。")
        return ReceiptReservation(receipt=receipt, created=cursor.rowcount == 1)

    async def get_by_key(self, session: DatabaseSession, idempotency_key: str) -> CommandReceipt | None:
        row = await session.fetch_one(
            "SELECT * FROM command_receipts WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return _receipt_from_row(row) if row is not None else None

    async def claim_send(self, session: DatabaseSession, *, receipt_id: str, now: str) -> bool:
        cursor = await session.execute(
            """
            UPDATE command_receipts
            SET send_status = 'claimed', claimed_at = ?, updated_at = ?
            WHERE receipt_id = ? AND send_status = 'pending'
            """,
            (now, now, receipt_id),
        )
        return cursor.rowcount == 1

    async def mark_sent(self, session: DatabaseSession, *, receipt_id: str, now: str) -> bool:
        cursor = await session.execute(
            """
            UPDATE command_receipts
            SET send_status = 'sent', sent_at = ?, send_error = '', updated_at = ?
            WHERE receipt_id = ? AND send_status = 'claimed'
            """,
            (now, now, receipt_id),
        )
        return cursor.rowcount == 1

    async def mark_failed(
        self,
        session: DatabaseSession,
        *,
        receipt_id: str,
        error: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE command_receipts
            SET send_status = 'failed', send_error = ?, updated_at = ?
            WHERE receipt_id = ? AND send_status = 'claimed'
            """,
            (str(error or "")[:1000], now, receipt_id),
        )
        return cursor.rowcount == 1
