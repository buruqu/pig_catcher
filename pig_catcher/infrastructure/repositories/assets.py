"""素材目录激活和按群查询仓储。"""

from __future__ import annotations

import json

from ...assets.models import StoredCatalog, ValidatedAsset, ValidatedManifest
from ...domain.enums import AssetKind, ConsentStatus, TemplateScope
from ...domain.errors import AssetImportError
from ...domain.models import ScopeKey
from ..database import DatabaseSession
from .framework import FrameworkRepository


class AssetRepository:
    """把已验证目录映射到模板和群授权，不拥有事务。"""

    def __init__(self, framework_repository: FrameworkRepository | None = None) -> None:
        self.framework_repository = framework_repository or FrameworkRepository()

    async def activate_catalog(
        self,
        session: DatabaseSession,
        *,
        validated: ValidatedManifest,
        stored: StoredCatalog,
        now: str,
    ) -> None:
        manifest = validated.manifest
        await session.execute(
            """
            UPDATE asset_manifest_imports
            SET status = 'replaced'
            WHERE catalog_id = ? AND catalog_hash <> ?
            """,
            (manifest.catalog_id, validated.catalog_hash),
        )
        await session.execute(
            """
            INSERT INTO asset_manifest_imports(
                catalog_hash, catalog_id, manifest_version, source_label,
                storage_relpath, entry_count, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
            ON CONFLICT(catalog_hash) DO UPDATE SET
                status = 'active',
                storage_relpath = excluded.storage_relpath,
                entry_count = excluded.entry_count
            """,
            (
                validated.catalog_hash,
                manifest.catalog_id,
                manifest.manifest_version,
                manifest.source_label,
                stored.storage_relative_path,
                len(validated.assets),
                now,
            ),
        )
        await session.execute(
            """
            UPDATE pig_templates
            SET enabled = 0, updated_at = ?
            WHERE catalog_hash IN (
                SELECT catalog_hash
                FROM asset_manifest_imports
                WHERE catalog_id = ? AND catalog_hash <> ?
            )
            """,
            (now, manifest.catalog_id, validated.catalog_hash),
        )
        await session.execute(
            """
            UPDATE food_templates
            SET enabled = 0, updated_at = ?
            WHERE catalog_hash IN (
                SELECT catalog_hash
                FROM asset_manifest_imports
                WHERE catalog_id = ? AND catalog_hash <> ?
            )
            """,
            (now, manifest.catalog_id, validated.catalog_hash),
        )
        for asset in validated.assets:
            await self._ensure_stable_identity(session, asset)
            if asset.entry.kind is AssetKind.PIG:
                await self._upsert_pig(session, asset=asset, validated=validated, stored=stored, now=now)
            else:
                await self._upsert_food(session, asset=asset, validated=validated, stored=stored, now=now)

    async def _ensure_stable_identity(self, session: DatabaseSession, asset: ValidatedAsset) -> None:
        entry = asset.entry
        pig_row = await session.fetch_one(
            "SELECT rarity, scope_type FROM pig_templates WHERE template_id = ?",
            (entry.template_id,),
        )
        food_row = await session.fetch_one(
            "SELECT rarity, scope_type FROM food_templates WHERE template_id = ?",
            (entry.template_id,),
        )
        if entry.kind is AssetKind.PIG and food_row is not None:
            raise AssetImportError(f"模板 ID 已被美食占用，不能改成猪：{entry.template_id}")
        if entry.kind is AssetKind.FOOD and pig_row is not None:
            raise AssetImportError(f"模板 ID 已被猪占用，不能改成美食：{entry.template_id}")
        existing = pig_row if pig_row is not None else food_row
        if existing is not None and (
            int(existing["rarity"]) != int(entry.rarity) or str(existing["scope_type"]) != entry.scope.value
        ):
            raise AssetImportError(f"模板品质或作用域发布后不能改变：{entry.template_id}")
        if existing is not None and entry.scope is TemplateScope.GROUP:
            mapping_table = "scope_pig_templates" if entry.kind is AssetKind.PIG else "scope_food_templates"
            scope_rows = await session.fetch_all(
                f"SELECT scope_id FROM {mapping_table} WHERE template_id = ?",
                (entry.template_id,),
            )
            existing_scope_ids = {str(row["scope_id"]) for row in scope_rows}
            if existing_scope_ids and existing_scope_ids != {str(entry.group_scope_id)}:
                raise AssetImportError(f"群专属模板发布后不能迁移所属群：{entry.template_id}")

    async def _ensure_group_scope(
        self,
        session: DatabaseSession,
        *,
        scope_id: str,
        now: str,
    ) -> ScopeKey:
        scope = ScopeKey.parse(scope_id)
        await self.framework_repository.ensure_scope(
            session,
            scope=scope,
            group_name="",
            stream_id="",
            now=now,
        )
        return scope

    @staticmethod
    def _stored_image_path(stored: StoredCatalog, asset: ValidatedAsset) -> str:
        return f"{stored.storage_relative_path}/files/{asset.entry.image}"

    async def _upsert_pig(
        self,
        session: DatabaseSession,
        *,
        asset: ValidatedAsset,
        validated: ValidatedManifest,
        stored: StoredCatalog,
        now: str,
    ) -> None:
        entry = asset.entry
        enabled = int(entry.scope is TemplateScope.COMMON or entry.consent_status is ConsentStatus.GRANTED)
        await session.execute(
            """
            INSERT INTO pig_templates(
                template_id, catalog_hash, template_version, display_name, rarity,
                scope_type, description, image_relpath, image_sha256, image_fit,
                length_min, length_max, weight_min, weight_max, fat_profile,
                recipe_tags_json, source_label, license, consent_status,
                enabled, created_at, updated_at
            )
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(template_id) DO UPDATE SET
                catalog_hash = excluded.catalog_hash,
                template_version = CASE
                    WHEN pig_templates.catalog_hash = excluded.catalog_hash
                    THEN pig_templates.template_version
                    ELSE pig_templates.template_version + 1
                END,
                display_name = excluded.display_name,
                description = excluded.description,
                image_relpath = excluded.image_relpath,
                image_sha256 = excluded.image_sha256,
                image_fit = excluded.image_fit,
                length_min = excluded.length_min,
                length_max = excluded.length_max,
                weight_min = excluded.weight_min,
                weight_max = excluded.weight_max,
                fat_profile = excluded.fat_profile,
                recipe_tags_json = excluded.recipe_tags_json,
                source_label = excluded.source_label,
                license = excluded.license,
                consent_status = excluded.consent_status,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                entry.template_id,
                validated.catalog_hash,
                entry.display_name,
                int(entry.rarity),
                entry.scope.value,
                entry.description,
                self._stored_image_path(stored, asset),
                asset.sha256,
                entry.fit.value,
                entry.length_min_cm,
                entry.length_max_cm,
                entry.weight_min_kg,
                entry.weight_max_kg,
                entry.fat_profile.value,
                json.dumps(entry.recipe_tags, ensure_ascii=False, separators=(",", ":")),
                entry.source,
                entry.license,
                entry.consent_status.value,
                enabled,
                now,
                now,
            ),
        )
        if entry.scope is TemplateScope.GROUP:
            scope = await self._ensure_group_scope(session, scope_id=str(entry.group_scope_id), now=now)
            await session.execute(
                """
                INSERT INTO scope_pig_templates(
                    scope_id, template_id, authorized, consent_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_id, template_id) DO UPDATE SET
                    authorized = excluded.authorized,
                    consent_status = excluded.consent_status,
                    updated_at = excluded.updated_at
                """,
                (
                    scope.value,
                    entry.template_id,
                    int(entry.consent_status is ConsentStatus.GRANTED),
                    entry.consent_status.value,
                    now,
                    now,
                ),
            )

    async def _upsert_food(
        self,
        session: DatabaseSession,
        *,
        asset: ValidatedAsset,
        validated: ValidatedManifest,
        stored: StoredCatalog,
        now: str,
    ) -> None:
        entry = asset.entry
        enabled = int(entry.scope is TemplateScope.COMMON or entry.consent_status is ConsentStatus.GRANTED)
        await session.execute(
            """
            INSERT INTO food_templates(
                template_id, catalog_hash, template_version, display_name, rarity,
                scope_type, description, image_relpath, image_sha256, image_fit,
                recipe_tags_json, effect_id, effect_params_json, source_label,
                license, consent_status, enabled, created_at, updated_at
            )
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(template_id) DO UPDATE SET
                catalog_hash = excluded.catalog_hash,
                template_version = CASE
                    WHEN food_templates.catalog_hash = excluded.catalog_hash
                    THEN food_templates.template_version
                    ELSE food_templates.template_version + 1
                END,
                display_name = excluded.display_name,
                description = excluded.description,
                image_relpath = excluded.image_relpath,
                image_sha256 = excluded.image_sha256,
                image_fit = excluded.image_fit,
                recipe_tags_json = excluded.recipe_tags_json,
                effect_id = excluded.effect_id,
                source_label = excluded.source_label,
                license = excluded.license,
                consent_status = excluded.consent_status,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                entry.template_id,
                validated.catalog_hash,
                entry.display_name,
                int(entry.rarity),
                entry.scope.value,
                entry.description,
                self._stored_image_path(stored, asset),
                asset.sha256,
                entry.fit.value,
                json.dumps(entry.recipe_tags, ensure_ascii=False, separators=(",", ":")),
                entry.effect_id,
                entry.source,
                entry.license,
                entry.consent_status.value,
                enabled,
                now,
                now,
            ),
        )
        if entry.scope is TemplateScope.GROUP:
            scope = await self._ensure_group_scope(session, scope_id=str(entry.group_scope_id), now=now)
            await session.execute(
                """
                INSERT INTO scope_food_templates(
                    scope_id, template_id, authorized, consent_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_id, template_id) DO UPDATE SET
                    authorized = excluded.authorized,
                    consent_status = excluded.consent_status,
                    updated_at = excluded.updated_at
                """,
                (
                    scope.value,
                    entry.template_id,
                    int(entry.consent_status is ConsentStatus.GRANTED),
                    entry.consent_status.value,
                    now,
                    now,
                ),
            )

    async def list_drawable_template_ids(
        self,
        session: DatabaseSession,
        *,
        kind: AssetKind,
        scope_id: str,
    ) -> list[str]:
        if kind is AssetKind.PIG:
            rows = await session.fetch_all(
                """
                SELECT template_id
                FROM pig_templates
                WHERE enabled = 1 AND scope_type = 'common'
                UNION ALL
                SELECT template.template_id
                FROM pig_templates AS template
                JOIN scope_pig_templates AS allowed
                  ON allowed.template_id = template.template_id
                WHERE template.enabled = 1
                  AND template.scope_type = 'group'
                  AND allowed.scope_id = ?
                  AND allowed.authorized = 1
                  AND allowed.consent_status = 'granted'
                ORDER BY template_id
                """,
                (scope_id,),
            )
        else:
            rows = await session.fetch_all(
                """
                SELECT template_id
                FROM food_templates
                WHERE enabled = 1 AND scope_type = 'common'
                UNION ALL
                SELECT template.template_id
                FROM food_templates AS template
                JOIN scope_food_templates AS allowed
                  ON allowed.template_id = template.template_id
                WHERE template.enabled = 1
                  AND template.scope_type = 'group'
                  AND allowed.scope_id = ?
                  AND allowed.authorized = 1
                  AND allowed.consent_status = 'granted'
                ORDER BY template_id
                """,
                (scope_id,),
            )
        return [str(row["template_id"]) for row in rows]
