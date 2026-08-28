"""SQLite primitives for the v2 achievement system.

This repository owns no transactions.  It intentionally exposes generic
progress and reward operations so future achievement packs can reuse the same
storage without schema changes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from ...domain.enums import AssetKind
from ...domain.errors import AssetStateConflictError, DomainValidationError
from ..database import DatabaseSession
from .asset_codes import AssetCodeRepository


class AchievementRepository:
    async def sync_definition(
        self,
        session: DatabaseSession,
        *,
        achievement_id: str,
        definition_version: int,
        name: str,
        category: str,
        tier: str,
        hidden: bool,
        points: int,
        description: str,
        hint: str,
        condition_json: str,
        rewards_json: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO achievement_definition_snapshots(
                achievement_id, definition_version, name, category, tier,
                hidden, points, description, hint, condition_json,
                rewards_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(achievement_id, definition_version) DO NOTHING
            """,
            (
                achievement_id,
                definition_version,
                name,
                category,
                tier,
                int(hidden),
                points,
                description,
                hint,
                condition_json,
                rewards_json,
                now,
            ),
        )

    async def ensure_profile(self, session: DatabaseSession, *, player_id: str, now: str) -> None:
        await session.execute(
            """
            INSERT INTO achievement_profiles(player_id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(player_id) DO NOTHING
            """,
            (player_id, now, now),
        )

    async def insert_event(
        self,
        session: DatabaseSession,
        *,
        event_id: str,
        receipt_id: str,
        player_id: str,
        scope_id: str,
        event_type: str,
        payload_json: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            INSERT OR IGNORE INTO achievement_events(
                event_id, receipt_id, player_id, scope_id,
                event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, receipt_id, player_id, scope_id, event_type, payload_json, now),
        )
        return cursor.rowcount == 1

    async def progress_rows(self, session: DatabaseSession, *, player_id: str) -> dict[str, dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT achievement_id, definition_version, progress_value,
                   state_json, unlocked_at, updated_at
            FROM achievement_progress WHERE player_id = ?
            """,
            (player_id,),
        )
        return {str(row["achievement_id"]): dict(row) for row in rows}

    async def upsert_progress(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        achievement_id: str,
        definition_version: int,
        progress_value: int,
        state_json: str,
        unlocked_at: str | None,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO achievement_progress(
                player_id, achievement_id, definition_version,
                progress_value, state_json, unlocked_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_id, achievement_id) DO UPDATE SET
                definition_version = excluded.definition_version,
                progress_value = CASE
                    WHEN achievement_progress.unlocked_at IS NOT NULL
                    THEN achievement_progress.progress_value
                    ELSE excluded.progress_value
                END,
                state_json = CASE
                    WHEN achievement_progress.unlocked_at IS NOT NULL
                    THEN achievement_progress.state_json
                    ELSE excluded.state_json
                END,
                unlocked_at = COALESCE(achievement_progress.unlocked_at, excluded.unlocked_at),
                updated_at = excluded.updated_at
            """,
            (player_id, achievement_id, definition_version, progress_value, state_json, unlocked_at, now),
        )

    async def insert_unlock(
        self,
        session: DatabaseSession,
        *,
        unlock_id: str,
        player_id: str,
        scope_id: str,
        achievement_id: str,
        definition_version: int,
        source_event_id: str,
        source_receipt_id: str,
        points_awarded: int,
        rewards_json: str,
        notification_status: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            INSERT OR IGNORE INTO achievement_unlocks(
                unlock_id, player_id, scope_id, achievement_id,
                definition_version, source_event_id, source_receipt_id,
                points_awarded, rewards_json, notification_status,
                unlocked_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unlock_id,
                player_id,
                scope_id,
                achievement_id,
                definition_version,
                source_event_id,
                source_receipt_id,
                points_awarded,
                rewards_json,
                notification_status,
                now,
                now,
            ),
        )
        if cursor.rowcount != 1:
            return False
        await session.execute(
            """
            UPDATE achievement_profiles
            SET achievement_points = achievement_points + ?, updated_at = ?
            WHERE player_id = ?
            """,
            (points_awarded, now, player_id),
        )
        return True

    async def grant_reward(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        reward_type: str,
        reward_id: str,
        quantity: int,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO achievement_reward_inventory(
                player_id, reward_type, reward_id, quantity, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(player_id, reward_type, reward_id) DO UPDATE SET
                quantity = achievement_reward_inventory.quantity + excluded.quantity,
                updated_at = excluded.updated_at
            """,
            (player_id, reward_type, reward_id, quantity, now),
        )

    async def claim_milestone(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        milestone_points: int,
        rewards_json: str,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            INSERT OR IGNORE INTO achievement_milestone_claims(
                player_id, milestone_points, rewards_json, claimed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (player_id, milestone_points, rewards_json, now),
        )
        return cursor.rowcount == 1

    async def replace_scope_targets(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        achievement_id: str,
        targets: Sequence[tuple[str, str]],
        now: str,
    ) -> None:
        existing = await session.fetch_one(
            """
            SELECT 1 FROM achievement_scope_targets
            WHERE scope_id = ? AND achievement_id = ? LIMIT 1
            """,
            (scope_id, achievement_id),
        )
        if existing is not None:
            return
        await session.executemany(
            """
            INSERT OR IGNORE INTO achievement_scope_targets(
                scope_id, achievement_id, target_key, target_label, captured_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ((scope_id, achievement_id, key, label, now) for key, label in targets),
        )

    async def captured_scope_achievement_ids(self, session: DatabaseSession, *, scope_id: str) -> set[str]:
        rows = await session.fetch_all(
            """
            SELECT DISTINCT achievement_id
            FROM achievement_scope_targets
            WHERE scope_id=?
            """,
            (scope_id,),
        )
        return {str(row["achievement_id"]) for row in rows}

    async def backfill_status(self, session: DatabaseSession, *, player_id: str) -> str | None:
        row = await session.fetch_one(
            "SELECT status FROM achievement_backfill_state WHERE player_id=?",
            (player_id,),
        )
        return str(row["status"]) if row is not None else None

    async def claim_backfill(self, session: DatabaseSession, *, player_id: str, now: str) -> bool:
        cursor = await session.execute(
            """
            UPDATE achievement_backfill_state
            SET status='running', last_error='', started_at=COALESCE(started_at, ?),
                completed_at=NULL, updated_at=?
            WHERE player_id=? AND status IN ('pending', 'failed')
            """,
            (now, now, player_id),
        )
        return cursor.rowcount == 1

    async def complete_backfill(self, session: DatabaseSession, *, player_id: str, now: str) -> None:
        await session.execute(
            """
            UPDATE achievement_backfill_state
            SET status='completed', last_error='', completed_at=?, updated_at=?
            WHERE player_id=? AND status='running'
            """,
            (now, now, player_id),
        )

    async def scope_target_counts(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        player_id: str,
        achievement_id: str,
        mode: str,
    ) -> tuple[int, int]:
        target_row = await session.fetch_one(
            "SELECT COUNT(*) AS count FROM achievement_scope_targets WHERE scope_id = ? AND achievement_id = ?",
            (scope_id, achievement_id),
        )
        target = int(target_row["count"] if target_row else 0)
        if target == 0:
            return 0, 0
        if mode == "catalog":
            row = await session.fetch_one(
                """
                SELECT COUNT(DISTINCT target.target_key) AS count
                FROM achievement_scope_targets AS target
                JOIN pig_catalog_entries AS catalog
                  ON catalog.template_id = target.target_key
                 AND catalog.player_id = ?
                WHERE target.scope_id = ? AND target.achievement_id = ?
                """,
                (player_id, scope_id, achievement_id),
            )
        else:
            comparator = (
                "pig.size_percentile >= 0.92 AND pig.weight_percentile >= 0.88"
                if mode == "giant"
                else "pig.size_percentile <= 0.08 AND pig.weight_percentile <= 0.15"
            )
            row = await session.fetch_one(
                f"""
                SELECT COUNT(DISTINCT target.target_key) AS count
                FROM achievement_scope_targets AS target
                JOIN pig_instances AS pig
                  ON pig.template_id = target.target_key
                 AND pig.owner_player_id = ?
                 AND pig.scope_id = ?
                 AND pig.state = 'active'
                 AND {comparator}
                 AND COALESCE(json_extract(pig.random_snapshot_json, '$.source'), '') <> 'admin-grant'
                WHERE target.scope_id = ? AND target.achievement_id = ?
                """,
                (player_id, scope_id, scope_id, achievement_id),
            )
        return int(row["count"] if row else 0), target

    async def metric_snapshot(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        historical: bool = False,
    ) -> dict[str, int]:
        """Return reliable achievement metrics without rescanning receipts by default.

        The historical query is intentionally reserved for the one-time Schema 35
        backfill. Runtime processing keeps counters in ``achievement_progress`` and
        only reads the small, already-maintained profile/catalogue aggregates.
        """

        if historical:
            row = await session.fetch_one(
                """
            SELECT p.coin_balance, p.experience,
                   COALESCE(s.total_catches, 0) AS total_catches,
                   COALESCE(s.total_cooks, 0) AS total_cooks,
                   (SELECT COUNT(*) FROM pig_catalog_entries c WHERE c.player_id=p.player_id) AS pig_catalog_count,
                   (SELECT COUNT(*) FROM food_catalog_entries c WHERE c.player_id=p.player_id) AS food_catalog_count,
                   (SELECT COUNT(*) FROM pig_instances i
                     JOIN command_receipts r ON r.result_object_id=i.pig_instance_id
                    WHERE r.player_id=p.player_id AND r.result_type='pig' AND i.rarity=5) AS five_star_pigs,
                   (SELECT COUNT(*) FROM pig_instances i
                     JOIN command_receipts r ON r.result_object_id=i.pig_instance_id
                    WHERE r.player_id=p.player_id AND r.result_type='pig' AND i.rarity=6) AS six_star_pigs,
                   (SELECT COUNT(DISTINCT f.food_instance_id)
                      FROM command_receipts r, json_each(r.result_json, '$.food_instance_ids') j
                      JOIN food_instances f ON f.food_instance_id=j.value
                     WHERE r.player_id=p.player_id
                       AND r.result_type IN ('cooking','batch-cooking')
                       AND f.rarity=5) AS five_star_foods,
                   (SELECT COUNT(DISTINCT f.food_instance_id)
                      FROM command_receipts r, json_each(r.result_json, '$.food_instance_ids') j
                      JOIN food_instances f ON f.food_instance_id=j.value
                     WHERE r.player_id=p.player_id
                       AND r.result_type IN ('cooking','batch-cooking')
                       AND f.rarity=6) AS six_star_foods,
                   (SELECT COUNT(*) FROM command_receipts r
                     WHERE r.player_id=p.player_id AND r.result_type='food-consumed') AS foods_eaten,
                   (SELECT COUNT(*) FROM giant_sightings g
                     WHERE g.player_id=p.player_id) AS giant_sightings,
                   (SELECT COUNT(*) FROM giant_sightings g
                     WHERE g.player_id=p.player_id AND g.size_qualified=1) AS size_board_entries,
                   (SELECT COUNT(*) FROM giant_sightings g
                     WHERE g.player_id=p.player_id AND g.weight_qualified=1) AS weight_board_entries,
                   (SELECT COUNT(*) FROM giant_sightings g
                     WHERE g.player_id=p.player_id
                       AND g.size_qualified=1 AND g.weight_qualified=1) AS dual_board_entries,
                   (SELECT COUNT(*) FROM command_receipts r
                     WHERE r.player_id=p.player_id AND r.result_type='pig'
                       AND json_extract(r.result_json, '$.global_size_record')=1) AS size_record_breaks,
                   (SELECT COUNT(*) FROM command_receipts r
                     WHERE r.player_id=p.player_id AND r.result_type='pig'
                       AND json_extract(r.result_json, '$.global_weight_record')=1) AS weight_record_breaks,
                   (SELECT COUNT(*) FROM pig_instances i
                     WHERE i.owner_player_id=p.player_id AND i.state='active' AND i.is_favorite=1)
                   + (SELECT COUNT(*) FROM food_instances i
                     WHERE i.owner_player_id=p.player_id AND i.state='active' AND i.is_favorite=1) AS favorite_assets,
                   COALESCE((SELECT metric_value FROM achievement_metric_counters m
                     WHERE m.player_id=p.player_id
                       AND m.metric_key='ordinary_coins_earned'), 0) AS ordinary_coins_earned,
                   p.coin_balance - COALESCE((SELECT metric_value FROM achievement_metric_counters m
                     WHERE m.player_id=p.player_id
                       AND m.metric_key='admin_coin_adjustment_net'), 0) AS ordinary_balance
            FROM players p
            LEFT JOIN player_statistics s ON s.player_id=p.player_id
            WHERE p.player_id=?
                """,
                (player_id,),
            )
        else:
            row = await session.fetch_one(
                """
                SELECT p.coin_balance, p.experience,
                       COALESCE(s.total_catches, 0) AS total_catches,
                       COALESCE(s.total_cooks, 0) AS total_cooks,
                       (SELECT COUNT(*) FROM pig_catalog_entries c
                         WHERE c.player_id=p.player_id) AS pig_catalog_count,
                       (SELECT COUNT(*) FROM food_catalog_entries c
                         WHERE c.player_id=p.player_id) AS food_catalog_count,
                       (SELECT COUNT(*) FROM pig_instances i
                         WHERE i.owner_player_id=p.player_id
                           AND i.state='active' AND i.is_favorite=1)
                       + (SELECT COUNT(*) FROM food_instances i
                         WHERE i.owner_player_id=p.player_id
                           AND i.state='active' AND i.is_favorite=1) AS favorite_assets,
                       COALESCE((SELECT metric_value FROM achievement_metric_counters m
                         WHERE m.player_id=p.player_id
                           AND m.metric_key='ordinary_coins_earned'), 0) AS ordinary_coins_earned,
                       p.coin_balance - COALESCE((SELECT metric_value FROM achievement_metric_counters m
                         WHERE m.player_id=p.player_id
                           AND m.metric_key='admin_coin_adjustment_net'), 0) AS ordinary_balance
                FROM players p
                LEFT JOIN player_statistics s ON s.player_id=p.player_id
                WHERE p.player_id=?
                """,
                (player_id,),
            )
        return {key: int(value or 0) for key, value in dict(row or {}).items()}

    async def owned_collection_ids(self, session: DatabaseSession, *, player_id: str, collection_id: str) -> set[str]:
        rows = await session.fetch_all(
            """
            SELECT DISTINCT c.template_id
            FROM pig_catalog_entries c
            JOIN pig_templates t ON t.template_id=c.template_id
            WHERE c.player_id=? AND t.collection_id=?
            """,
            (player_id, collection_id),
        )
        return {str(row["template_id"]) for row in rows}

    async def visible_template_rows(
        self, session: DatabaseSession, *, scope_id: str, collection_id: str | None = None
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT DISTINCT t.template_id, t.display_name, t.collection_id
            FROM pig_templates t
            LEFT JOIN scope_pig_templates st
              ON st.template_id=t.template_id AND st.scope_id=?
            WHERE t.enabled=1 AND t.consent_status <> 'revoked'
              AND (t.scope_type='common' OR (st.authorized=1 AND st.consent_status='granted'))
              AND (? IS NULL OR t.collection_id=?)
            ORDER BY t.template_id
            """,
            (scope_id, collection_id, collection_id),
        )
        return [dict(row) for row in rows]

    async def pig_row(self, session: DatabaseSession, *, pig_instance_id: str) -> dict[str, object] | None:
        row = await session.fetch_one(
            "SELECT * FROM pig_instances WHERE pig_instance_id=?",
            (pig_instance_id,),
        )
        return dict(row) if row is not None else None

    async def food_rows(self, session: DatabaseSession, *, food_instance_ids: Sequence[str]) -> list[dict[str, object]]:
        if not food_instance_ids:
            return []
        placeholders = ",".join("?" for _ in food_instance_ids)
        rows = await session.fetch_all(
            f"SELECT * FROM food_instances WHERE food_instance_id IN ({placeholders})",
            tuple(food_instance_ids),
        )
        return [dict(row) for row in rows]

    async def transfer_partner_ids(self, session: DatabaseSession, *, player_id: str, transfer_type: str) -> set[str]:
        rows = await session.fetch_all(
            """
            SELECT DISTINCT CASE
                WHEN from_player_id=? THEN to_player_id ELSE from_player_id END AS partner_id
            FROM asset_transfer_events
            WHERE transfer_type=? AND (from_player_id=? OR to_player_id=?)
            """,
            (player_id, transfer_type, player_id, player_id),
        )
        return {str(row["partner_id"]) for row in rows}

    async def food_effect_source_names(self, session: DatabaseSession, *, effect_entry_ids: Sequence[str]) -> set[str]:
        if not effect_entry_ids:
            return set()
        placeholders = ",".join("?" for _ in effect_entry_ids)
        rows = await session.fetch_all(
            f"""
            SELECT DISTINCT food.display_name_snapshot
            FROM player_food_effects effect
            JOIN food_instances food
              ON food.food_instance_id=effect.source_food_instance_id
            WHERE effect.effect_entry_id IN ({placeholders})
            """,
            tuple(effect_entry_ids),
        )
        return {str(row["display_name_snapshot"]) for row in rows}

    async def roulette_outcomes(self, session: DatabaseSession, *, player_id: str) -> set[str]:
        rows = await session.fetch_all(
            """
            SELECT DISTINCT CAST(json_extract(result_json, '$.outcome') AS TEXT) AS outcome
            FROM command_receipts
            WHERE player_id=? AND result_type='roulette-spin'
              AND CAST(json_extract(result_json, '$.outcome') AS INTEGER) BETWEEN 1 AND 6
            """,
            (player_id,),
        )
        return {str(row["outcome"]) for row in rows}

    async def sushi_instance_ids(self, session: DatabaseSession, *, player_id: str) -> set[str]:
        rows = await session.fetch_all(
            """
            SELECT DISTINCT food.food_instance_id
            FROM food_instances food
            WHERE food.display_name_snapshot='猪寿司拼盘'
              AND COALESCE(json_extract(food.random_snapshot_json, '$.source'), '') <> 'admin-grant'
              AND (
                  food.owner_player_id=?
                  OR EXISTS(
                      SELECT 1
                      FROM command_receipts receipt,
                           json_each(receipt.result_json, '$.food_instance_ids') generated
                      WHERE receipt.player_id=?
                        AND receipt.result_type IN ('cooking', 'batch-cooking')
                        AND generated.value=food.food_instance_id
                  )
                  OR EXISTS(
                      SELECT 1 FROM asset_transfer_events transfer
                      WHERE transfer.asset_kind='food'
                        AND transfer.asset_instance_id=food.food_instance_id
                        AND transfer.to_player_id=?
                  )
              )
            """,
            (player_id, player_id, player_id),
        )
        return {str(row["food_instance_id"]) for row in rows}

    async def technique_color_counts(self, session: DatabaseSession, *, player_id: str) -> tuple[int, int]:
        row = await session.fetch_one(
            "SELECT blue_activations, red_activations FROM player_technique_progress WHERE player_id=?",
            (player_id,),
        )
        return (
            int(row["blue_activations"] if row else 0),
            int(row["red_activations"] if row else 0),
        )

    async def pending_unlock_rows(
        self, session: DatabaseSession, *, player_id: str, receipt_id: str
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT u.unlock_id, u.achievement_id, d.name, d.tier, d.points,
                   d.description, u.rewards_json, u.unlocked_at
            FROM achievement_unlocks u
            JOIN achievement_definition_snapshots d
              ON d.achievement_id=u.achievement_id
             AND d.definition_version=u.definition_version
            WHERE u.player_id=? AND u.source_receipt_id=?
              AND u.notification_status='pending'
            ORDER BY u.unlocked_at, u.unlock_id
            """,
            (player_id, receipt_id),
        )
        return [dict(row) for row in rows]

    async def backfill_summary_rows(self, session: DatabaseSession, *, player_id: str) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT u.unlock_id, u.achievement_id, d.name, d.tier, d.points,
                   d.description, u.rewards_json, u.unlocked_at
            FROM achievement_unlocks u
            JOIN achievement_definition_snapshots d
              ON d.achievement_id=u.achievement_id
             AND d.definition_version=u.definition_version
            WHERE u.player_id=? AND u.notification_status='summary'
            ORDER BY d.points DESC, u.unlocked_at, u.unlock_id
            """,
            (player_id,),
        )
        return [dict(row) for row in rows]

    async def update_notification_status(
        self,
        session: DatabaseSession,
        *,
        unlock_ids: Sequence[str],
        from_status: str,
        to_status: str,
        error: str,
        now: str,
    ) -> bool:
        if not unlock_ids:
            return False
        placeholders = ",".join("?" for _ in unlock_ids)
        cursor = await session.execute(
            f"""
            UPDATE achievement_unlocks
            SET notification_status=?, notification_error=?, updated_at=?
            WHERE unlock_id IN ({placeholders}) AND notification_status=?
            """,
            (to_status, error[:500], now, *unlock_ids, from_status),
        )
        return cursor.rowcount == len(unlock_ids)

    async def profile_row(self, session: DatabaseSession, *, player_id: str) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT p.display_name, ap.achievement_points, ap.equipped_title_id,
                   ap.equipped_frame_id, ap.showcase_achievement_id,
                   (SELECT COUNT(*) FROM achievement_unlocks u WHERE u.player_id=p.player_id) AS unlocked_count
            FROM players p
            JOIN achievement_profiles ap ON ap.player_id=p.player_id
            WHERE p.player_id=?
            """,
            (player_id,),
        )
        return dict(row) if row is not None else None

    async def list_achievement_rows(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        category: str | None,
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT d.achievement_id, d.name, d.category, d.tier, d.hidden,
                   d.points, d.description, d.hint, d.condition_json,
                   d.rewards_json, COALESCE(p.progress_value, 0) AS progress_value,
                   COALESCE(p.state_json, '{}') AS state_json, p.unlocked_at
            FROM achievement_definition_snapshots d
            LEFT JOIN achievement_progress p
              ON p.achievement_id=d.achievement_id AND p.player_id=?
            WHERE d.definition_version=(
                SELECT MAX(d2.definition_version)
                FROM achievement_definition_snapshots d2
                WHERE d2.achievement_id=d.achievement_id
            ) AND (? IS NULL OR d.category=?)
            ORDER BY d.hidden, d.category, d.points, d.achievement_id
            """,
            (player_id, category, category),
        )
        return [dict(row) for row in rows]

    async def ranking_rows(
        self, session: DatabaseSession, *, scope_id: str, limit: int, offset: int
    ) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT p.player_id, p.display_name, a.achievement_points,
                   COUNT(u.unlock_id) AS unlocked_count,
                   DENSE_RANK() OVER(ORDER BY a.achievement_points DESC, COUNT(u.unlock_id) DESC) AS rank
            FROM players p
            JOIN achievement_profiles a ON a.player_id=p.player_id
            LEFT JOIN achievement_unlocks u ON u.player_id=p.player_id
            WHERE p.scope_id=?
            GROUP BY p.player_id
            ORDER BY a.achievement_points DESC, unlocked_count DESC, p.created_at, p.player_id
            LIMIT ? OFFSET ?
            """,
            (scope_id, limit, offset),
        )
        return [dict(row) for row in rows]

    async def ranking_count(self, session: DatabaseSession, *, scope_id: str) -> int:
        row = await session.fetch_one(
            """
            SELECT COUNT(*) AS count
            FROM achievement_profiles a
            JOIN players p ON p.player_id=a.player_id
            WHERE p.scope_id=?
            """,
            (scope_id,),
        )
        return int(row["count"] if row else 0)

    async def update_equipped_title(self, session: DatabaseSession, *, player_id: str, title_id: str, now: str) -> bool:
        if title_id:
            owned = await session.fetch_one(
                """
                SELECT 1 FROM achievement_reward_inventory
                WHERE player_id=? AND reward_type='title'
                  AND reward_id=? AND quantity>0
                """,
                (player_id, title_id),
            )
            if owned is None:
                return False
        await session.execute(
            "UPDATE achievement_profiles SET equipped_title_id=?, updated_at=? WHERE player_id=?",
            (title_id, now, player_id),
        )
        return True

    async def update_equipped_cosmetics(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        title_id: str | None,
        frame_id: str | None,
        showcase_achievement_id: str | None,
        now: str,
    ) -> bool:
        for reward_type, reward_id in (("title", title_id), ("frame", frame_id)):
            if not reward_id:
                continue
            owned = await session.fetch_one(
                """
                SELECT 1 FROM achievement_reward_inventory
                WHERE player_id=? AND reward_type=? AND reward_id=? AND quantity>0
                """,
                (player_id, reward_type, reward_id),
            )
            if owned is None:
                return False
        await session.execute(
            """
            UPDATE achievement_profiles
            SET equipped_title_id=COALESCE(?, equipped_title_id),
                equipped_frame_id=COALESCE(?, equipped_frame_id),
                showcase_achievement_id=COALESCE(?, showcase_achievement_id),
                updated_at=?
            WHERE player_id=?
            """,
            (title_id, frame_id, showcase_achievement_id, now, player_id),
        )
        return True

    async def clear_equipped_cosmetics(self, session: DatabaseSession, *, player_id: str, now: str) -> None:
        await session.execute(
            """
            UPDATE achievement_profiles
            SET equipped_title_id='', equipped_frame_id='',
                showcase_achievement_id='', updated_at=?
            WHERE player_id=?
            """,
            (now, player_id),
        )

    async def reward_rows(self, session: DatabaseSession, *, player_id: str) -> list[dict[str, object]]:
        rows = await session.fetch_all(
            """
            SELECT reward_type, reward_id, quantity
            FROM achievement_reward_inventory
            WHERE player_id=? AND quantity>0
            ORDER BY reward_type, reward_id
            """,
            (player_id,),
        )
        return [dict(row) for row in rows]

    async def regular_unlock_count(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        achievement_ids: Sequence[str],
    ) -> int:
        row = await session.fetch_one(
            """
            SELECT COUNT(*) AS count
            FROM achievement_unlocks
            WHERE player_id=? AND achievement_id IN (SELECT value FROM json_each(?))
            """,
            (player_id, _json_array(achievement_ids)),
        )
        return int(row["count"] if row else 0)

    async def memorial_pig_template(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        template_name: str,
    ) -> dict[str, object] | None:
        row = await session.fetch_one(
            """
            SELECT template.*
            FROM pig_templates template
            LEFT JOIN pig_catalog_entries catalog
              ON catalog.player_id=? AND catalog.template_id=template.template_id
            WHERE template.enabled=1 AND template.consent_status <> 'revoked'
              AND template.scope_type='common' AND template.rarity=5
              AND template.template_id <> 'pig-kfc-crazy-thursday'
              AND catalog.template_id IS NULL
              AND (template.display_name COLLATE NOCASE=? OR template.template_id COLLATE NOCASE=?)
            ORDER BY template.template_id LIMIT 1
            """,
            (player_id, template_name, template_name),
        )
        return dict(row) if row is not None else None

    async def consume_reward(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        reward_type: str,
        reward_id: str,
        quantity: int,
        now: str,
    ) -> bool:
        cursor = await session.execute(
            """
            UPDATE achievement_reward_inventory
            SET quantity=quantity-?, updated_at=?
            WHERE player_id=? AND reward_type=? AND reward_id=? AND quantity>=?
            """,
            (quantity, now, player_id, reward_type, reward_id, quantity),
        )
        return cursor.rowcount == 1

    async def operation_result(self, session: DatabaseSession, *, operation_key: str) -> str | None:
        row = await session.fetch_one(
            "SELECT result_json FROM achievement_operations WHERE operation_key=?",
            (operation_key,),
        )
        return str(row["result_json"]) if row is not None else None

    async def insert_operation(
        self,
        session: DatabaseSession,
        *,
        operation_key: str,
        player_id: str,
        operation_type: str,
        result_json: str,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO achievement_operations(
                operation_key, player_id, operation_type, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (operation_key, player_id, operation_type, result_json, now),
        )

    async def activate_ticket(
        self,
        session: DatabaseSession,
        *,
        effect_entry_id: str,
        player_id: str,
        ticket_id: str,
        action_type: str,
        uses: int,
        now: str,
    ) -> None:
        await session.execute(
            """
            INSERT INTO achievement_ticket_effects(
                effect_entry_id, player_id, ticket_id, action_type,
                granted_uses, consumed_uses, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (effect_entry_id, player_id, ticket_id, action_type, uses, now, now),
        )

    async def active_ticket_ids(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        action_type: str,
    ) -> set[str]:
        rows = await session.fetch_all(
            """
            SELECT DISTINCT ticket_id
            FROM achievement_ticket_effects
            WHERE player_id=? AND action_type=? AND consumed_uses<granted_uses
            """,
            (player_id, action_type),
        )
        return {str(row["ticket_id"]) for row in rows}

    async def consume_active_ticket(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        ticket_id: str,
        now: str,
    ) -> bool:
        row = await session.fetch_one(
            """
            SELECT effect_entry_id
            FROM achievement_ticket_effects
            WHERE player_id=? AND ticket_id=? AND consumed_uses<granted_uses
            ORDER BY created_at, effect_entry_id LIMIT 1
            """,
            (player_id, ticket_id),
        )
        if row is None:
            return False
        cursor = await session.execute(
            """
            UPDATE achievement_ticket_effects
            SET consumed_uses=consumed_uses+1, updated_at=?
            WHERE effect_entry_id=? AND consumed_uses<granted_uses
            """,
            (now, str(row["effect_entry_id"])),
        )
        return cursor.rowcount == 1

    async def unseen_pig_template_ids(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        template_ids: Sequence[str],
    ) -> set[str]:
        if not template_ids:
            return set()
        rows = await session.fetch_all(
            """
            SELECT value AS template_id
            FROM (SELECT value FROM json_each(?)) candidates
            WHERE value NOT IN (
                SELECT template_id FROM pig_catalog_entries WHERE player_id=?
            )
            """,
            (_json_array(template_ids), player_id),
        )
        return {str(row["template_id"]) for row in rows}

    async def unseen_food_template_ids(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        template_ids: Sequence[str],
    ) -> set[str]:
        if not template_ids:
            return set()
        rows = await session.fetch_all(
            """
            SELECT value AS template_id
            FROM json_each(?)
            WHERE value NOT IN (
                SELECT template_id FROM food_catalog_entries WHERE player_id=?
            )
            """,
            (_json_array(template_ids), player_id),
        )
        return {str(row["template_id"]) for row in rows}

    async def reforge_short_code(
        self,
        session: DatabaseSession,
        *,
        player_id: str,
        asset_kind: str,
        old_code: str,
        new_code: str,
        now: str,
    ) -> str | None:
        """旧成就券和新编号券共享活跃占用、活动保护及 UUID 选择规则。"""

        if asset_kind not in {"pig", "food"}:
            return None
        table = "pig_instances" if asset_kind == "pig" else "food_instances"
        id_column = "pig_instance_id" if asset_kind == "pig" else "food_instance_id"
        row = await session.fetch_one(
            f"""
            SELECT instance.{id_column} AS instance_id, instance.scope_id
            FROM {table} AS instance
            JOIN players AS player ON player.player_id = instance.owner_player_id
                                   AND player.scope_id = instance.scope_id
            WHERE instance.owner_player_id = ? AND instance.short_code COLLATE NOCASE = ?
              AND instance.state = 'active' AND instance.locked_trade_id IS NULL
            """,
            (player_id, old_code),
        )
        if row is None:
            return None
        try:
            result = await AssetCodeRepository().rename_owned_asset(
                session,
                asset_kind=AssetKind(asset_kind),
                asset_instance_id=str(row["instance_id"]),
                owner_player_id=player_id,
                scope_id=str(row["scope_id"]),
                new_short_code=new_code,
                now=now,
            )
        except (AssetStateConflictError, DomainValidationError):
            return None
        return result["asset_instance_id"]


def _json_array(values: Sequence[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


__all__ = ["AchievementRepository"]
