"""Schema 32: persistent JJK technique permits, group effects and pairing progress."""

from .model import Migration

MIGRATION_0032 = Migration(
    version=32,
    name="group-techniques",
    statements=(
        """
        CREATE TABLE player_technique_permits(
            player_id TEXT NOT NULL REFERENCES players(player_id),
            technique_id TEXT NOT NULL CHECK (
                technique_id IN (
                    'malevolent-kitchen',
                    'lapse-blue',
                    'reversal-red',
                    'hollow-purple',
                    'domain-gojo-bypass'
                )
            ),
            granted_uses INTEGER NOT NULL DEFAULT 0 CHECK (granted_uses >= 0),
            consumed_uses INTEGER NOT NULL DEFAULT 0 CHECK (
                consumed_uses >= 0 AND consumed_uses <= granted_uses
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(player_id, technique_id)
        )
        """,
        """
        CREATE TABLE group_technique_effects(
            effect_entry_id TEXT PRIMARY KEY,
            scope_id TEXT NOT NULL REFERENCES scopes(scope_id),
            technique_id TEXT NOT NULL CHECK (
                technique_id IN (
                    'malevolent-kitchen',
                    'lapse-blue',
                    'reversal-red'
                )
            ),
            source_player_id TEXT NOT NULL REFERENCES players(player_id),
            remaining_uses INTEGER NOT NULL CHECK (remaining_uses >= 0),
            total_uses INTEGER NOT NULL CHECK (total_uses > 0),
            status TEXT NOT NULL DEFAULT 'active' CHECK (
                status IN ('active', 'completed')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE UNIQUE INDEX idx_one_active_group_technique
        ON group_technique_effects(scope_id)
        WHERE status = 'active'
        """,
        """
        CREATE INDEX idx_group_technique_source
        ON group_technique_effects(source_player_id, status, created_at)
        """,
        """
        CREATE TABLE player_technique_progress(
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            blue_activations INTEGER NOT NULL DEFAULT 0 CHECK (blue_activations >= 0),
            red_activations INTEGER NOT NULL DEFAULT 0 CHECK (red_activations >= 0),
            purple_unlocks INTEGER NOT NULL DEFAULT 0 CHECK (purple_unlocks >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
    ),
)
