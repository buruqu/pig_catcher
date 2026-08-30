"""Schema 50：战利品次数随对战规则版本保存，旧场不被追溯改写。"""

from .model import Migration

GUARDS = {
    "idx_battle_loot_actor",
    "battle_loot_natural_end",
    "battle_loot_total_insert",
    "battle_loot_only_advance",
    "battle_delivery_owner",
}

MIGRATION_0050 = Migration(
    version=50,
    name="battle-loot-versioned-total",
    statements=(
        """
        ALTER TABLE battle_loot
        ADD COLUMN total_uses INTEGER NOT NULL DEFAULT 5 CHECK(total_uses BETWEEN 1 AND 5)
        """,
        "DROP INDEX idx_battle_loot_actor",
        """
        CREATE INDEX idx_battle_loot_actor
        ON battle_loot(actor_id,scope_id,created_ms,battle_id,used,total_uses)
        """,
        "DROP TRIGGER battle_loot_natural_end",
        """
        CREATE TRIGGER battle_loot_natural_end BEFORE INSERT ON battle_loot
        WHEN NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND b.status='completed'
              AND b.scope_id=NEW.scope_id AND b.winner_id=NEW.recipient_id
              AND NEW.actor_id IN(b.initiator_id,b.opponent_id) AND NEW.actor_id!=b.winner_id
        )
        BEGIN SELECT RAISE(ABORT,'仅自然力竭结局生成战利品'); END
        """,
        "DROP TRIGGER battle_loot_only_advance",
        """
        CREATE TRIGGER battle_loot_total_insert BEFORE INSERT ON battle_loot
        WHEN NEW.used>NEW.total_uses OR NOT EXISTS(
            SELECT 1 FROM battle_matches b
            WHERE b.battle_id=NEW.battle_id AND (
                (b.definition_version=1 AND NEW.total_uses=5)
                OR (b.definition_version=2 AND NEW.total_uses=3)
            )
        )
        BEGIN SELECT RAISE(ABORT,'战利品总次数与对战规则版本不符'); END
        """,
        """
        CREATE TRIGGER battle_loot_only_advance BEFORE UPDATE ON battle_loot
        WHEN NEW.battle_id!=OLD.battle_id OR NEW.actor_id!=OLD.actor_id
          OR NEW.recipient_id!=OLD.recipient_id OR NEW.scope_id!=OLD.scope_id
          OR NEW.created_ms!=OLD.created_ms OR NEW.total_uses!=OLD.total_uses
          OR NEW.used!=OLD.used+1 OR NEW.used>NEW.total_uses
        BEGIN SELECT RAISE(ABORT,'战利品只能在本场总次数内逐次结算'); END
        """,
        "DROP TRIGGER battle_delivery_owner",
        """
        CREATE TRIGGER battle_delivery_owner BEFORE INSERT ON battle_loot_deliveries
        WHEN NOT EXISTS(
            SELECT 1 FROM battle_loot l
            JOIN pig_instances p ON p.pig_instance_id=NEW.pig_instance_id
            WHERE l.battle_id=NEW.battle_id AND l.used+1=NEW.ordinal
              AND NEW.ordinal<=l.total_uses
              AND p.owner_player_id=l.recipient_id AND p.scope_id=l.scope_id
        )
        BEGIN SELECT RAISE(ABORT,'战利品归属、顺序或总次数不符'); END
        """,
    ),
)
