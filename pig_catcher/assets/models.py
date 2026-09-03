"""素材清单的严格模型和校验结果。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.display import normalize_display_tags
from ..domain.enums import (
    AssetKind,
    ConsentStatus,
    FatProfile,
    FitMode,
    Rarity,
    StatureProfile,
    TemplateScope,
)
from ..domain.errors import FoodEffectError
from ..domain.food_effects import SUPPORTED_EFFECT_IDS, resolve_food_effect
from ..domain.models import ScopeKey
from ..version import ASSET_MANIFEST_VERSION


class CollectionMetadata(BaseModel):
    """联动收藏系列中的稳定槽位与官方资料来源。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    collaboration_name: str = Field(min_length=1, max_length=80)
    collection_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    collection_name: str = Field(min_length=1, max_length=80)
    slot: int = Field(ge=1, le=100)
    total: int = Field(ge=1, le=100)
    character_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    character_name: str = Field(min_length=1, max_length=80)
    official_profile_url: str = Field(pattern=r"^https://", min_length=10, max_length=500)

    @model_validator(mode="after")
    def validate_slot(self) -> CollectionMetadata:
        if self.slot > self.total:
            raise ValueError("联动收藏槽位不能大于系列总数")
        return self


class AssetManifestEntry(BaseModel):
    """一张猪或美食素材的静态定义。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    template_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    kind: AssetKind
    display_name: str = Field(min_length=1, max_length=80)
    rarity: Rarity
    scope: TemplateScope
    group_scope_id: str | None = Field(default=None, max_length=320)
    description: str = Field(min_length=1, max_length=500)
    image: str = Field(min_length=5, max_length=300)
    fit: FitMode = FitMode.CONTAIN
    source: str = Field(min_length=1, max_length=300)
    license: str = Field(min_length=1, max_length=100)
    consent_status: ConsentStatus = ConsentStatus.NOT_REQUIRED
    length_min_cm: float | None = Field(default=None, gt=0, le=10000)
    length_max_cm: float | None = Field(default=None, gt=0, le=10000)
    weight_min_kg: float | None = Field(default=None, gt=0, le=100000)
    weight_max_kg: float | None = Field(default=None, gt=0, le=100000)
    fat_profile: FatProfile | None = None
    stature_profile: StatureProfile | None = None
    recipe_tags: list[str] = Field(default_factory=list, max_length=20)
    display_tags: list[str] = Field(default_factory=list, max_length=5)
    effect_id: str = Field(default="", max_length=80)
    effect_params: dict[
        str,
        str | int | float | bool | list[object] | dict[str, int | float],
    ] = Field(
        default_factory=dict,
        max_length=20,
    )
    paired_food_template_id: str = Field(
        default="",
        pattern=r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*)?$",
        max_length=80,
    )
    alternate_image: str = Field(
        default="",
        max_length=300,
    )
    collection: CollectionMetadata | None = None

    @field_validator("image")
    @classmethod
    def validate_image_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or value.startswith(("/", "\\")) or ".." in path.parts or "\x00" in value:
            raise ValueError("图片必须使用素材包内不含上级跳转的相对路径")
        if path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg", ".gif"}:
            raise ValueError("图片路径仅支持 PNG、JPEG、WebP 或 GIF 扩展名")
        return path.as_posix()

    @field_validator("alternate_image")
    @classmethod
    def validate_alternate_image_path(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        path = Path(value)
        if path.is_absolute() or value.startswith(("/", "\\")) or ".." in path.parts or "\x00" in value:
            raise ValueError("备用图片必须使用素材包内不含上级跳转的相对路径")
        if path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg", ".gif"}:
            raise ValueError("备用图片路径仅支持 PNG、JPEG、WebP 或 GIF 扩展名")
        return path.as_posix()

    @field_validator("display_tags")
    @classmethod
    def validate_display_tags(cls, value: list[str]) -> list[str]:
        return list(normalize_display_tags(value))

    @field_validator("recipe_tags")
    @classmethod
    def validate_recipe_tags(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw_tag in value:
            tag = str(raw_tag or "").strip()
            if not tag:
                raise ValueError("食谱标签不能包含空值")
            if len(tag) > 40:
                raise ValueError("单个食谱标签不能超过 40 个字符")
            if tag not in normalized:
                normalized.append(tag)
        return normalized

    @model_validator(mode="after")
    def validate_scope_and_kind(self) -> AssetManifestEntry:
        if self.scope is TemplateScope.COMMON:
            if self.rarity is Rarity.SIX:
                raise ValueError("六星素材不能声明为公共素材")
            if self.group_scope_id is not None:
                raise ValueError("公共素材不能填写群范围")
            if self.consent_status is not ConsentStatus.NOT_REQUIRED:
                raise ValueError("公共素材授权状态必须为 not-required")
        else:
            if self.rarity is not Rarity.SIX:
                raise ValueError("群专属素材只允许六星")
            if not self.group_scope_id:
                raise ValueError("群专属素材必须填写 group_scope_id")
            ScopeKey.parse(self.group_scope_id)
            if self.consent_status not in {ConsentStatus.GRANTED, ConsentStatus.REVOKED}:
                raise ValueError("群专属素材授权状态必须为 granted 或 revoked")

        if self.kind is AssetKind.PIG:
            required = (
                self.length_min_cm,
                self.length_max_cm,
                self.weight_min_kg,
                self.weight_max_kg,
                self.fat_profile,
            )
            if any(value is None for value in required):
                raise ValueError("猪素材必须填写体型、重量范围和肥瘦画像")
            if float(self.length_max_cm) < float(self.length_min_cm):
                raise ValueError("猪素材最大体型不能小于最小体型")
            if float(self.weight_max_kg) < float(self.weight_min_kg):
                raise ValueError("猪素材最大重量不能小于最小重量")
            if self.effect_id or self.effect_params:
                raise ValueError("猪素材不能声明美食效果")
            if self.paired_food_template_id and (
                self.scope is not TemplateScope.GROUP
                or self.rarity is not Rarity.SIX
            ):
                raise ValueError("只有群专属六星猪可以绑定定制六星菜")
        elif any(
            value is not None
            for value in (
                self.length_min_cm,
                self.length_max_cm,
                self.weight_min_kg,
                self.weight_max_kg,
                self.fat_profile,
                self.stature_profile,
            )
        ):
            raise ValueError("美食素材不能填写猪的体型、重量、肥瘦或体格画像")
        if self.kind is AssetKind.FOOD and self.collection is not None:
            raise ValueError("美食素材不能加入猪猪联动收藏系列")
        if self.kind is AssetKind.FOOD:
            if self.display_tags:
                raise ValueError("当前展示标签只用于猪猪素材")
            if self.paired_food_template_id:
                raise ValueError("美食素材不能声明对应菜模板")
            if self.effect_params and not self.effect_id:
                raise ValueError("美食填写 effect_params 时必须同时填写 effect_id")
            if self.effect_id in SUPPORTED_EFFECT_IDS:
                try:
                    resolve_food_effect(self.effect_id, self.effect_params)
                except FoodEffectError as exc:
                    raise ValueError(str(exc)) from exc
        return self


class AssetManifest(BaseModel):
    """一个可独立校验和导入的完整素材目录。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    manifest_version: Literal[1, 2, 3, 4] = ASSET_MANIFEST_VERSION
    catalog_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    source_label: str = Field(min_length=1, max_length=200)
    entries: list[AssetManifestEntry] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def validate_unique_template_ids(self) -> AssetManifest:
        template_ids = [entry.template_id for entry in self.entries]
        if len(template_ids) != len(set(template_ids)):
            raise ValueError("素材清单中存在重复 template_id")
        collection_slots: set[tuple[str, int]] = set()
        collection_definitions: dict[str, tuple[str, int, str]] = {}
        for entry in self.entries:
            collection = entry.collection
            if collection is None:
                continue
            slot_key = (collection.collection_id, collection.slot)
            if slot_key in collection_slots:
                raise ValueError(
                    f"联动收藏系列 {collection.collection_id} 存在重复槽位 {collection.slot}"
                )
            collection_slots.add(slot_key)
            definition = (
                collection.collection_name,
                collection.total,
                collection.collaboration_name,
            )
            existing = collection_definitions.setdefault(collection.collection_id, definition)
            if existing != definition:
                raise ValueError(f"联动收藏系列 {collection.collection_id} 的名称或总数不一致")
        if self.manifest_version >= 4:
            entries_by_id = {entry.template_id: entry for entry in self.entries}
            six_star_pigs = [
                entry
                for entry in self.entries
                if entry.kind is AssetKind.PIG
                and entry.scope is TemplateScope.GROUP
                and entry.rarity is Rarity.SIX
            ]
            six_star_food_ids = {
                entry.template_id
                for entry in self.entries
                if entry.kind is AssetKind.FOOD
                and entry.scope is TemplateScope.GROUP
                and entry.rarity is Rarity.SIX
            }
            paired_food_ids: list[str] = []
            for pig in six_star_pigs:
                paired_id = pig.paired_food_template_id
                if not paired_id:
                    raise ValueError(
                        f"群专属六星猪 {pig.template_id} 必须绑定对应定制六星菜"
                    )
                food = entries_by_id.get(paired_id)
                if food is None:
                    raise ValueError(
                        f"群专属六星猪 {pig.template_id} 绑定的美食模板不存在"
                    )
                if (
                    food.kind is not AssetKind.FOOD
                    or food.scope is not TemplateScope.GROUP
                    or food.rarity is not Rarity.SIX
                ):
                    raise ValueError(
                        f"群专属六星猪 {pig.template_id} 只能绑定群专属六星菜"
                    )
                if food.group_scope_id != pig.group_scope_id:
                    raise ValueError(
                        f"群专属六星猪 {pig.template_id} 不能绑定其他群的定制六星菜"
                    )
                paired_food_ids.append(paired_id)
            if len(paired_food_ids) != len(set(paired_food_ids)):
                raise ValueError("同一道定制六星菜不能绑定给多只六星猪")
            if set(paired_food_ids) != six_star_food_ids:
                raise ValueError("每道群专属六星菜必须且只能对应一只六星猪")
        return self


@dataclass(frozen=True, slots=True)
class ValidatedAsset:
    """已通过文件和图片检查的素材。"""

    entry: AssetManifestEntry
    source_path: Path
    sha256: str
    width: int
    height: int
    image_format: str
    is_animated: bool
    frame_count: int
    frame_durations_ms: tuple[int, ...]
    total_duration_ms: int
    loop_count: int | None
    has_transparency: bool
    alternate_source_path: Path | None = None
    alternate_sha256: str = ""


@dataclass(frozen=True, slots=True)
class ValidatedManifest:
    """已通过结构和文件检查的清单。"""

    manifest: AssetManifest
    source_root: Path
    source_manifest_path: Path
    catalog_hash: str
    assets: tuple[ValidatedAsset, ...]


@dataclass(frozen=True, slots=True)
class StoredCatalog:
    """已原子复制到插件数据目录的素材目录。"""

    catalog_hash: str
    root: Path
    storage_relative_path: str
