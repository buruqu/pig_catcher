"""Shared selection rules for safe batch selling and cooking."""

from __future__ import annotations

from typing import Literal

from ..database import DatabaseSession

BatchAssetKind = Literal["pig", "food"]


def collaboration_pig_exclusion_sql(instance_alias: str) -> str:
    """Return a correlated SQL clause that protects every collaboration pig."""

    if not instance_alias.replace("_", "").isalnum():
        raise ValueError("invalid SQL alias")
    return f"""
      AND NOT EXISTS (
          SELECT 1
          FROM pig_templates AS protected_template
          WHERE protected_template.template_id = {instance_alias}.template_id
            AND protected_template.collection_id IS NOT NULL
            AND protected_template.collection_id != ''
      )
    """


async def highest_instance_ids_per_template(
    session: DatabaseSession,
    *,
    player_id: str,
    scope_id: str,
    asset_kind: BatchAssetKind,
    max_rarity: int,
    rarity: int | None,
) -> list[str]:
    """Select one deterministic highest-value unlocked instance per template.

    Collaboration pigs are intentionally omitted: the batch queries protect every
    collaboration instance independently of the player's optional keep setting.
    """

    if asset_kind == "pig":
        table = "pig_instances"
        id_column = "pig_instance_id"
        template_join = """
            JOIN pig_templates AS template
              ON template.template_id = candidate.template_id
        """
        template_filter = """
              AND (template.collection_id IS NULL OR template.collection_id = '')
        """
    elif asset_kind == "food":
        table = "food_instances"
        id_column = "food_instance_id"
        template_join = ""
        template_filter = ""
    else:
        raise ValueError("asset_kind must be pig or food")

    rarity_clause = (
        "AND candidate.rarity = ?"
        if rarity is not None
        else "AND candidate.rarity <= ?"
    )
    rarity_param = int(rarity) if rarity is not None else int(max_rarity)
    rows = await session.fetch_all(
        f"""
        SELECT kept.instance_id
        FROM (
            SELECT
                candidate.{id_column} AS instance_id,
                ROW_NUMBER() OVER (
                    PARTITION BY candidate.template_id
                    ORDER BY candidate.official_value DESC, candidate.{id_column} ASC
                ) AS keep_rank
            FROM {table} AS candidate
            {template_join}
            WHERE candidate.owner_player_id = ?
              AND candidate.scope_id = ?
              AND candidate.state = 'active'
              AND candidate.locked_trade_id IS NULL
              {template_filter}
              {rarity_clause}
        ) AS kept
        WHERE kept.keep_rank = 1
        ORDER BY kept.instance_id
        """,
        (player_id, scope_id, rarity_param),
    )
    return [str(row["instance_id"]) for row in rows]
