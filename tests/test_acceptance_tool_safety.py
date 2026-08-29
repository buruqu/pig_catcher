"""Acceptance utilities must not create/write a live source or weaken contracts."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.accept_v2_production_clone import (
    backup_readonly,
    complete_data_digest,
    expected_legacy_row,
    readonly,
)
from tools.uat_catching_and_collection import clone_formal_data, create_plugin, validate_components
from tools.uat_production_recovery import configure_plugin as recovery_config
from tools.uat_recent_mechanics import configure_plugin as mechanic_config


def seed_source(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "assets").mkdir()
    (source / "assets" / "fixture.txt").write_text("local asset", encoding="utf-8")
    path = source / "pig_catcher.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES(1,'source remains unchanged')")
    return source, path


def test_readonly_rejects_writes_and_does_not_create_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        readonly(tmp_path / "missing.sqlite3")
    assert not (tmp_path / "missing.sqlite3").exists()
    _, source = seed_source(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with readonly(source) as connection, pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("UPDATE sample SET value='not allowed'")
    target = tmp_path / "snapshot.sqlite3"
    backup_readonly(source, target)
    assert complete_data_digest(target) == complete_data_digest(source)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        backup_readonly(source, target)


def test_production_clone_accepts_only_the_reviewed_upgrade_level_mapping() -> None:
    old = {
        "player_id": "qq:group:user",
        "upgrade_type": "feed",
        "level": 4,
        "updated_at": "2026-08-28T00:00:00.000Z",
    }
    new = {**old, "level": 8}
    expected, transformed = expected_legacy_row(
        "upgrades",
        old,
        new,
        mist_food_ids=set(),
        migration_started=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert transformed is True
    assert expected == new


@pytest.mark.parametrize("filename", ["../escaped.sqlite3", "C:/escaped.sqlite3", ""])
def test_clone_rejects_path_escape_before_creating_output(tmp_path, filename):
    source, _ = seed_source(tmp_path)
    target = tmp_path / "result"
    with pytest.raises(ValueError):
        clone_formal_data(source_data_dir=source, target_data_dir=target, database_filename=filename)
    assert not target.exists()


def test_clone_is_fresh_disjoint_and_source_bytes_unchanged(tmp_path):
    source, path = seed_source(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        clone_formal_data(source_data_dir=source, target_data_dir=source / "uat", database_filename=path.name)
    target = tmp_path / "isolated"
    clone_formal_data(source_data_dir=source, target_data_dir=target, database_filename=path.name)
    assert complete_data_digest(target / path.name) == complete_data_digest(path)
    assert (target / "assets" / "fixture.txt").read_text(encoding="utf-8") == "local asset"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        clone_formal_data(source_data_dir=source, target_data_dir=target, database_filename=path.name)


def test_recovery_component_contract_covers_each_v2_feature():
    result = validate_components(create_plugin())
    assert result["home_cards"] == 1
    assert result["commands"] >= 83
    assert result["total"] == result["commands"] + 1


@pytest.mark.parametrize("configure", [recovery_config, mechanic_config])
def test_focused_uat_config_uses_current_feature_switch(configure):
    from pig_catcher.config import PigCatcherConfig

    plugin = create_plugin()
    configure(plugin)
    settings = PigCatcherConfig.model_validate(plugin.get_plugin_config_data())
    assert not settings.features.achievements_enabled
    assert settings.features.catching_enabled
