"""Persist the exact custom food paired with each group-scoped six-star pig."""

from .model import Migration

MIGRATION_0007 = Migration(
    version=7,
    name="paired_six_star_recipes",
    statements=(
        (
            "ALTER TABLE pig_templates ADD COLUMN paired_food_template_id "
            "TEXT NOT NULL DEFAULT ''"
        ),
    ),
)
