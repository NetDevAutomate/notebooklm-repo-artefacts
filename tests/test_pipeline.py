"""Tests for repo_artefacts.pipeline — state management, stage gates, and helpers.

Only pre_check/post_check methods and state persistence are tested here.
execute() methods are NOT tested as they call external APIs.
"""

from __future__ import annotations

import json
from pathlib import Path

from repo_artefacts.notebooklm import ARTEFACT_CONFIG
from repo_artefacts.pipeline import (
    CleanupStage,
    CollectStage,
    DownloadStage,
    GenerateStage,
    PipelineContext,
    PipelineState,
    PublishStage,
    StageResult,
    Status,
    UploadStage,
    _hash_file,
    _resolve_repo_name,
)

# ---------------------------------------------------------------------------
# Helpers — build PipelineContext with sensible defaults
# ---------------------------------------------------------------------------


def _make_ctx(
    tmp_path: Path,
    *,
    repo_path: Path | None = None,
    store_slug: str | None = None,
    keep_notebook: bool = False,
    notebook_id: str = "",
    artefacts: dict[str, str] | None = None,
    pdf_path: Path | None = None,
) -> PipelineContext:
    """Build a PipelineContext wired to tmp_path for state persistence."""
    rp = repo_path or tmp_path / "repo"
    output_dir = rp / "docs" / "artefacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    state = PipelineState(
        repo_name="test-repo",
        notebook_id=notebook_id,
        artefacts=artefacts or {},
    )
    return PipelineContext(
        repo_path=rp,
        store_slug=store_slug,
        output_dir=output_dir,
        keep_notebook=keep_notebook,
        state=state,
        state_path=output_dir / ".pipeline-state.json",
        pdf_path=pdf_path,
    )


# ===========================================================================
# Status enum
# ===========================================================================


class TestStatus:
    def test_values(self) -> None:
        assert Status.PASS == "pass"
        assert Status.FAIL == "fail"
        assert Status.SKIP == "skip"
        assert Status.RETRY == "retry"

    def test_is_str(self) -> None:
        for s in Status:
            assert isinstance(s, str)


# ===========================================================================
# StageResult
# ===========================================================================


class TestStageResult:
    def test_defaults(self) -> None:
        r = StageResult(Status.PASS)
        assert r.status == Status.PASS
        assert r.message == ""
        assert r.data == {}

    def test_with_message_and_data(self) -> None:
        r = StageResult(Status.FAIL, "boom", {"key": "val"})
        assert r.message == "boom"
        assert r.data == {"key": "val"}


# ===========================================================================
# PipelineState — save / load / stage helpers
# ===========================================================================


class TestPipelineState:
    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        state = PipelineState(
            repo_name="my-repo",
            notebook_id="nb-123",
            content_hash="abc",
            artefacts={"audio": "completed"},
        )
        state.set_stage("collect", "pass", extra_key="extra_val")

        path = tmp_path / "state.json"
        state.save(path)
        loaded = PipelineState.load(path)

        assert loaded.repo_name == "my-repo"
        assert loaded.notebook_id == "nb-123"
        assert loaded.content_hash == "abc"
        assert loaded.artefacts == {"audio": "completed"}
        assert loaded.stage_status("collect") == "pass"
        # Extra kwargs preserved
        assert loaded.stages["collect"]["extra_key"] == "extra_val"

    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        loaded = PipelineState.load(tmp_path / "nonexistent.json")
        assert loaded.repo_name == ""
        assert loaded.notebook_id == ""
        assert loaded.stages == {}

    def test_save_writes_valid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        PipelineState(repo_name="r").save(path)
        data = json.loads(path.read_text())
        assert data["repo_name"] == "r"

    def test_updated_at_set_on_save(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        state = PipelineState()
        state.save(path)
        data = json.loads(path.read_text())
        assert data["updated_at"] != ""

    def test_stage_status_unknown_returns_empty(self) -> None:
        state = PipelineState()
        assert state.stage_status("nonexistent") == ""

    def test_set_stage_records_timestamp(self) -> None:
        state = PipelineState()
        state.set_stage("upload", "pass")
        assert "at" in state.stages["upload"]
        assert state.stages["upload"]["status"] == "pass"

    def test_set_stage_overwrites_previous(self) -> None:
        state = PipelineState()
        state.set_stage("collect", "fail", reason="bad")
        state.set_stage("collect", "pass")
        assert state.stage_status("collect") == "pass"
        # Old reason key gone
        assert "reason" not in state.stages["collect"]

    def test_load_ignores_unknown_keys(self, tmp_path: Path) -> None:
        """Unknown keys in JSON should not cause errors."""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"repo_name": "r", "unknown_field": 42}))
        loaded = PipelineState.load(path)
        assert loaded.repo_name == "r"
        assert not hasattr(loaded, "unknown_field")


# ===========================================================================
# PipelineContext.save_state
# ===========================================================================


class TestPipelineContext:
    def test_save_state_delegates_to_state(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.state.repo_name = "delegated"
        ctx.save_state()
        loaded = PipelineState.load(ctx.state_path)
        assert loaded.repo_name == "delegated"


# ===========================================================================
# _hash_file helper
# ===========================================================================


class TestHashFile:
    def test_consistent_hash_for_same_content(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello world")
        f2.write_text("hello world")
        assert _hash_file(f1) == _hash_file(f2)

    def test_different_hash_for_different_content(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")
        assert _hash_file(f1) != _hash_file(f2)

    def test_returns_64_char_hex_string(self, tmp_path: Path) -> None:
        f = tmp_path / "file.bin"
        f.write_bytes(b"\x00" * 100)
        h = _hash_file(f)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty"
        f.write_bytes(b"")
        h = _hash_file(f)
        assert len(h) == 64
        # Verify it's deterministic
        assert h == _hash_file(f)

    def test_handles_large_file(self, tmp_path: Path) -> None:
        """Ensure chunked reading works for files larger than the 8192-byte buffer."""
        f = tmp_path / "large.bin"
        f.write_bytes(b"x" * 20_000)
        h = _hash_file(f)
        assert len(h) == 64


# ===========================================================================
# _resolve_repo_name helper
# ===========================================================================


class TestResolveRepoName:
    def test_falls_back_to_dir_name_without_git(self, tmp_path: Path) -> None:
        repo = tmp_path / "my-cool-project"
        repo.mkdir()
        name = _resolve_repo_name(repo)
        assert name == "my-cool-project"

    def test_uses_git_remote_when_available(self, tmp_path: Path) -> None:
        """In a real git repo with an origin, the name comes from the remote URL."""
        repo = tmp_path / "local-name"
        repo.mkdir()
        # Initialise a bare git repo with a fake origin
        import subprocess

        subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/Org/remote-name.git"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        name = _resolve_repo_name(repo)
        assert name == "remote-name"

    def test_strips_dot_git_suffix(self, tmp_path: Path) -> None:
        repo = tmp_path / "x"
        repo.mkdir()
        import subprocess

        subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:Org/foo-bar.git"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        assert _resolve_repo_name(repo) == "foo-bar"


# ===========================================================================
# CollectStage.pre_check
# ===========================================================================


class TestCollectStage:
    def test_pass_with_git_dir(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        ctx = _make_ctx(tmp_path, repo_path=repo)
        result = CollectStage().pre_check(ctx)
        assert result.status == Status.PASS

    def test_fail_without_git_dir(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        # No .git directory
        ctx = _make_ctx(tmp_path, repo_path=repo)
        result = CollectStage().pre_check(ctx)
        assert result.status == Status.FAIL
        assert "Not a git repo" in result.message

    def test_fail_nonexistent_path(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, repo_path=tmp_path / "does-not-exist")
        result = CollectStage().pre_check(ctx)
        assert result.status == Status.FAIL


# ===========================================================================
# UploadStage.pre_check
# ===========================================================================


class TestUploadStage:
    def test_fail_without_pdf(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, pdf_path=None)
        result = UploadStage().pre_check(ctx)
        assert result.status == Status.FAIL
        assert "No PDF" in result.message

    def test_fail_with_missing_pdf_file(self, tmp_path: Path) -> None:
        """pdf_path is set but the file does not exist on disk."""
        ctx = _make_ctx(tmp_path, pdf_path=tmp_path / "gone.pdf")
        result = UploadStage().pre_check(ctx)
        assert result.status == Status.FAIL

    def test_pass_with_pdf(self, tmp_path: Path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")
        ctx = _make_ctx(tmp_path, pdf_path=pdf)
        result = UploadStage().pre_check(ctx)
        assert result.status == Status.PASS

    def test_post_check_pass_with_notebook_id(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, notebook_id="nb-1")
        result = UploadStage().post_check(ctx)
        assert result.status == Status.PASS

    def test_post_check_fail_without_notebook_id(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, notebook_id="")
        result = UploadStage().post_check(ctx)
        assert result.status == Status.FAIL


# ===========================================================================
# GenerateStage.pre_check / post_check
# ===========================================================================


class TestGenerateStage:
    def test_pre_check_fail_without_notebook_id(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, notebook_id="")
        result = GenerateStage().pre_check(ctx)
        assert result.status == Status.FAIL
        assert "No notebook ID" in result.message

    def test_pre_check_pass_with_notebook_id(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, notebook_id="nb-42")
        result = GenerateStage().pre_check(ctx)
        assert result.status == Status.PASS

    def test_post_check_pass_when_all_completed(self, tmp_path: Path) -> None:
        all_completed = {name: "completed" for name in ARTEFACT_CONFIG}
        ctx = _make_ctx(tmp_path, artefacts=all_completed)
        result = GenerateStage().post_check(ctx)
        assert result.status == Status.PASS

    def test_post_check_fail_when_missing(self, tmp_path: Path) -> None:
        partial = {name: "completed" for name in list(ARTEFACT_CONFIG)[:2]}
        ctx = _make_ctx(tmp_path, artefacts=partial)
        result = GenerateStage().post_check(ctx)
        assert result.status == Status.FAIL
        assert "Not all artefacts" in result.message

    def test_post_check_fail_when_one_failed(self, tmp_path: Path) -> None:
        artefacts = {name: "completed" for name in ARTEFACT_CONFIG}
        # Sabotage one
        first_name = next(iter(ARTEFACT_CONFIG))
        artefacts[first_name] = "failed"
        ctx = _make_ctx(tmp_path, artefacts=artefacts)
        result = GenerateStage().post_check(ctx)
        assert result.status == Status.FAIL

    def test_post_check_fail_when_empty(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, artefacts={})
        result = GenerateStage().post_check(ctx)
        assert result.status == Status.FAIL


# ===========================================================================
# DownloadStage.pre_check
# ===========================================================================


class TestDownloadStage:
    def test_fail_without_notebook_id(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, notebook_id="")
        result = DownloadStage().pre_check(ctx)
        assert result.status == Status.FAIL
        assert "No notebook ID" in result.message

    def test_fail_without_completed_artefacts(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, notebook_id="nb-1", artefacts={})
        result = DownloadStage().pre_check(ctx)
        assert result.status == Status.FAIL
        assert "No completed artefacts" in result.message

    def test_fail_with_only_failed_artefacts(self, tmp_path: Path) -> None:
        artefacts = {name: "failed" for name in ARTEFACT_CONFIG}
        ctx = _make_ctx(tmp_path, notebook_id="nb-1", artefacts=artefacts)
        result = DownloadStage().pre_check(ctx)
        assert result.status == Status.FAIL

    def test_pass_when_notebook_and_completed(self, tmp_path: Path) -> None:
        artefacts = {name: "completed" for name in ARTEFACT_CONFIG}
        ctx = _make_ctx(tmp_path, notebook_id="nb-1", artefacts=artefacts)
        result = DownloadStage().pre_check(ctx)
        assert result.status == Status.PASS

    def test_pass_with_partial_completed(self, tmp_path: Path) -> None:
        """Even one completed artefact is enough to attempt download."""
        first = next(iter(ARTEFACT_CONFIG))
        ctx = _make_ctx(tmp_path, notebook_id="nb-1", artefacts={first: "completed"})
        result = DownloadStage().pre_check(ctx)
        assert result.status == Status.PASS


# ===========================================================================
# PublishStage.pre_check
# ===========================================================================


class TestPublishStage:
    def test_skip_without_store(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, store_slug=None)
        result = PublishStage().pre_check(ctx)
        assert result.status == Status.SKIP
        assert "No store" in result.message

    def test_fail_with_absolute_path(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, store_slug="/Users/someone/repo")
        result = PublishStage().pre_check(ctx)
        assert result.status == Status.FAIL
        assert "not a path" in result.message

    def test_fail_with_tilde_path(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, store_slug="~/code/repo")
        result = PublishStage().pre_check(ctx)
        assert result.status == Status.FAIL

    def test_fail_with_dotdot(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, store_slug="Org/../etc")
        result = PublishStage().pre_check(ctx)
        assert result.status == Status.FAIL

    def test_fail_with_single_segment(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, store_slug="just-a-name")
        result = PublishStage().pre_check(ctx)
        assert result.status == Status.FAIL

    def test_pass_with_org_repo(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, store_slug="NetDevAutomate/artefact-store")
        result = PublishStage().pre_check(ctx)
        assert result.status == Status.PASS

    def test_post_check_always_passes(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = PublishStage().post_check(ctx)
        assert result.status == Status.PASS


# ===========================================================================
# CleanupStage.pre_check
# ===========================================================================


class TestCleanupStage:
    def test_skip_with_keep_notebook(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, keep_notebook=True, notebook_id="nb-1")
        result = CleanupStage().pre_check(ctx)
        assert result.status == Status.SKIP
        assert "Keeping notebook" in result.message

    def test_skip_without_notebook_id(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, notebook_id="")
        result = CleanupStage().pre_check(ctx)
        assert result.status == Status.SKIP
        assert "No notebook" in result.message

    def test_skip_when_not_all_done(self, tmp_path: Path) -> None:
        """Incomplete artefacts should prevent cleanup to allow retry."""
        partial = {name: "completed" for name in list(ARTEFACT_CONFIG)[:2]}
        ctx = _make_ctx(tmp_path, notebook_id="nb-1", artefacts=partial)
        result = CleanupStage().pre_check(ctx)
        assert result.status == Status.SKIP
        assert "retry" in result.message.lower()

    def test_skip_when_artefacts_empty(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, notebook_id="nb-1", artefacts={})
        result = CleanupStage().pre_check(ctx)
        assert result.status == Status.SKIP

    def test_pass_when_all_done(self, tmp_path: Path) -> None:
        all_completed = {name: "completed" for name in ARTEFACT_CONFIG}
        ctx = _make_ctx(tmp_path, notebook_id="nb-1", artefacts=all_completed)
        result = CleanupStage().pre_check(ctx)
        assert result.status == Status.PASS

    def test_keep_notebook_takes_priority_over_all_done(self, tmp_path: Path) -> None:
        """Even with all artefacts done, keep_notebook=True should skip."""
        all_completed = {name: "completed" for name in ARTEFACT_CONFIG}
        ctx = _make_ctx(
            tmp_path,
            keep_notebook=True,
            notebook_id="nb-1",
            artefacts=all_completed,
        )
        result = CleanupStage().pre_check(ctx)
        assert result.status == Status.SKIP

    def test_post_check_always_passes(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = CleanupStage().post_check(ctx)
        assert result.status == Status.PASS


# ===========================================================================
# CollectStage.post_check
# ===========================================================================


class TestCollectStagePostCheck:
    def test_pass_with_valid_pdf(self, tmp_path: Path) -> None:
        pdf = tmp_path / "output.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")
        ctx = _make_ctx(tmp_path)
        ctx.pdf_path = pdf
        result = CollectStage().post_check(ctx)
        assert result.status == Status.PASS

    def test_fail_with_no_pdf_path(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.pdf_path = None
        result = CollectStage().post_check(ctx)
        assert result.status == Status.FAIL

    def test_fail_with_missing_pdf_file(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.pdf_path = tmp_path / "gone.pdf"
        result = CollectStage().post_check(ctx)
        assert result.status == Status.FAIL

    def test_fail_with_empty_pdf(self, tmp_path: Path) -> None:
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"")
        ctx = _make_ctx(tmp_path)
        ctx.pdf_path = pdf
        result = CollectStage().post_check(ctx)
        assert result.status == Status.FAIL
        assert "empty" in result.message.lower()


# ===========================================================================
# VerifyStage / ReadmeStage pre_check (lightweight gate tests)
# ===========================================================================


class TestVerifyStagePreCheck:
    def test_skip_without_store(self, tmp_path: Path) -> None:
        from repo_artefacts.pipeline import VerifyStage

        ctx = _make_ctx(tmp_path, store_slug=None)
        result = VerifyStage().pre_check(ctx)
        assert result.status == Status.SKIP

    def test_pass_with_store(self, tmp_path: Path) -> None:
        from repo_artefacts.pipeline import VerifyStage

        ctx = _make_ctx(tmp_path, store_slug="Org/repo")
        result = VerifyStage().pre_check(ctx)
        assert result.status == Status.PASS


class TestReadmeStagePreCheck:
    def test_skip_without_readme(self, tmp_path: Path) -> None:
        from repo_artefacts.pipeline import ReadmeStage

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        # No README.md
        ctx = _make_ctx(tmp_path, repo_path=repo, store_slug="Org/repo")
        result = ReadmeStage().pre_check(ctx)
        assert result.status == Status.SKIP
        assert "No README" in result.message

    def test_skip_without_store(self, tmp_path: Path) -> None:
        from repo_artefacts.pipeline import ReadmeStage

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "README.md").write_text("# Hello\n")
        ctx = _make_ctx(tmp_path, repo_path=repo, store_slug=None)
        result = ReadmeStage().pre_check(ctx)
        assert result.status == Status.SKIP
        assert "No store" in result.message

    def test_pass_with_readme_and_store(self, tmp_path: Path) -> None:
        from repo_artefacts.pipeline import ReadmeStage

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "README.md").write_text("# Hello\n")
        ctx = _make_ctx(tmp_path, repo_path=repo, store_slug="Org/repo")
        result = ReadmeStage().pre_check(ctx)
        assert result.status == Status.PASS


# ===========================================================================
# ALL_STAGES ordering sanity check
# ===========================================================================


class TestAllStages:
    def test_stage_order(self) -> None:
        from repo_artefacts.pipeline import ALL_STAGES

        names = [s.name for s in ALL_STAGES]
        assert names == [
            "collect",
            "upload",
            "generate",
            "download",
            "publish",
            "verify",
            "readme",
            "cleanup",
        ]

    def test_all_stages_have_required_methods(self) -> None:
        from repo_artefacts.pipeline import ALL_STAGES

        for stage in ALL_STAGES:
            assert hasattr(stage, "pre_check"), f"{stage.name} missing pre_check"
            assert hasattr(stage, "execute"), f"{stage.name} missing execute"
            assert hasattr(stage, "post_check"), f"{stage.name} missing post_check"
            assert hasattr(stage, "name"), "stage missing name attribute"
