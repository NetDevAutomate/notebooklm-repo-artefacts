"""Tests for store.py safety validations.

Validates:
- _validate_store_slug rejects absolute paths, empty strings, .., non org/repo
- _safe_rmtree refuses to delete directories outside the cache tree
- _store_cache_dir returns expected paths for valid slugs
"""

import pytest

from repo_artefacts.store import (
    StoreError,
    _safe_rmtree,
    _store_cache_dir,
    _validate_store_slug,
)


class TestValidateStoreSlug:
    def test_valid_slug(self):
        _validate_store_slug("NetDevAutomate/artefact-store")

    def test_rejects_absolute_path(self):
        with pytest.raises(StoreError, match="not a path"):
            _validate_store_slug("/Users/someone/code/my-repo")

    def test_rejects_tilde_path(self):
        with pytest.raises(StoreError, match="not a path"):
            _validate_store_slug("~/code/my-repo")

    def test_rejects_dotdot(self):
        with pytest.raises(StoreError, match="must not contain"):
            _validate_store_slug("Org/../etc")

    def test_rejects_empty(self):
        with pytest.raises(StoreError, match="must not be empty"):
            _validate_store_slug("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(StoreError, match="must not be empty"):
            _validate_store_slug("   ")

    def test_rejects_single_segment(self):
        with pytest.raises(StoreError, match="org/repo"):
            _validate_store_slug("just-a-name")

    def test_rejects_three_segments(self):
        with pytest.raises(StoreError, match="org/repo"):
            _validate_store_slug("a/b/c")


class TestStoreCacheDir:
    def test_returns_path_under_cache(self):
        result = _store_cache_dir("Org/repo")
        assert result.parts[-2:] == ("Org", "repo")
        assert "cache" in str(result).lower() or "repo-artefacts" in str(result)


class TestSafeRmtree:
    def test_refuses_path_outside_cache(self, tmp_path):
        target = tmp_path / "not-in-cache"
        target.mkdir()
        with pytest.raises(StoreError, match="outside the store cache"):
            _safe_rmtree(target)
        # Directory must still exist
        assert target.exists()
