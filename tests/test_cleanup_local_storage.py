"""Safety boundaries for the explicit local-artifact cleanup tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import cleanup_local_storage


def test_cleanup_requires_explicit_opt_in_for_reparse_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    artifacts = repository / "artifacts"
    artifacts.mkdir(parents=True)
    monkeypatch.setattr(cleanup_local_storage, "REPO_ROOT", repository)
    monkeypatch.setattr(cleanup_local_storage, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(
        cleanup_local_storage,
        "_is_reparse_point",
        lambda path: Path(path) == artifacts,
    )

    with pytest.raises(RuntimeError, match="--allow-root-reparse-point"):
        cleanup_local_storage._validated_root(allow_root_reparse_point=False)

    resolved, is_reparse = cleanup_local_storage._validated_root(
        allow_root_reparse_point=True
    )
    assert resolved == artifacts.resolve()
    assert is_reparse is True


def test_cleanup_candidates_are_confined_to_the_resolved_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    artifacts = repository / "artifacts"
    candidate = artifacts / "old-uat"
    candidate.mkdir(parents=True)
    (candidate / "report.json").write_bytes(b"{}")
    monkeypatch.setattr(cleanup_local_storage, "REPO_ROOT", repository)
    monkeypatch.setattr(cleanup_local_storage, "ARTIFACTS_ROOT", artifacts)

    root, is_reparse = cleanup_local_storage._validated_root(
        allow_root_reparse_point=False
    )
    candidates = cleanup_local_storage._candidates(root, older_than_days=0)

    assert is_reparse is False
    assert [Path(item.path) for item in candidates] == [candidate.resolve()]
    assert candidates[0].bytes == 2
