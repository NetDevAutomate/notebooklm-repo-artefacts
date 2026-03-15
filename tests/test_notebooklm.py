"""Tests for repo_artefacts.notebooklm module (mocked, no API calls)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from notebooklm import Artifact, ArtifactType

from repo_artefacts.notebooklm import (
    ARTEFACT_CONFIG,
    DOWNLOAD_MAP,
    KIND_TO_NAME,
    MAX_RETRIES,
    NAME_TO_KIND,
    _artifact_config_name,
    _delete_existing_by_type,
    _poll_by_type,
    _request_artefact,
    _snapshot_artefact_ids,
)

# ---------------------------------------------------------------------------
# Test helpers — build upstream Artifact objects for mocking
# ---------------------------------------------------------------------------

# Upstream ArtifactStatus int codes (from notebooklm._artifacts)
_PROCESSING = 1
_PENDING = 2
_COMPLETED = 3
_FAILED = 4

# Upstream ArtifactTypeCode int codes
_TYPE_AUDIO = 1
_TYPE_VIDEO = 3
_TYPE_INFOGRAPHIC = 7
_TYPE_SLIDE_DECK = 8


def _make_artifact(
    artifact_id: str = "art-1",
    type_code: int = _TYPE_AUDIO,
    status: int = _COMPLETED,
    title: str = "Test",
) -> Artifact:
    """Build an upstream Artifact with the given type/status codes."""
    return Artifact(
        id=artifact_id,
        title=title,
        _artifact_type=type_code,
        status=status,
    )


@dataclass
class FakeStatus:
    task_id: str = "fake-task-id"
    status: str = "in_progress"
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


# ---------------------------------------------------------------------------
# Type mapping tests
# ---------------------------------------------------------------------------


def test_name_to_kind_covers_config() -> None:
    assert set(NAME_TO_KIND.keys()) == set(ARTEFACT_CONFIG.keys())


def test_name_to_kind_uses_string_enum() -> None:
    """All values should be ArtifactType string enum members."""
    for name, kind in NAME_TO_KIND.items():
        assert isinstance(kind, ArtifactType), f"{name} maps to {type(kind)}, not ArtifactType"


def test_kind_to_name_is_reverse_of_name_to_kind() -> None:
    for name, kind in NAME_TO_KIND.items():
        assert KIND_TO_NAME[kind] == name


def test_artifact_config_name_maps_correctly() -> None:
    audio = _make_artifact(type_code=_TYPE_AUDIO)
    assert _artifact_config_name(audio) == "audio"

    video = _make_artifact(type_code=_TYPE_VIDEO)
    assert _artifact_config_name(video) == "video"

    slides = _make_artifact(type_code=_TYPE_SLIDE_DECK)
    assert _artifact_config_name(slides) == "slides"

    infographic = _make_artifact(type_code=_TYPE_INFOGRAPHIC)
    assert _artifact_config_name(infographic) == "infographic"


def test_artifact_config_name_returns_none_for_unknown() -> None:
    unknown = _make_artifact(type_code=99)
    assert _artifact_config_name(unknown) is None


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_artefact_config_has_all_types() -> None:
    assert set(ARTEFACT_CONFIG.keys()) == {"audio", "video", "slides", "infographic"}


def test_artefact_config_has_method_key() -> None:
    for name, cfg in ARTEFACT_CONFIG.items():
        assert "method" in cfg, f"{name} missing 'method'"
        assert "instructions" in cfg, f"{name} missing 'instructions'"


def test_download_map_covers_all_types() -> None:
    labels = {entry.label for entry in DOWNLOAD_MAP}
    assert labels == {"audio", "video", "slides", "infographic"}


def test_download_map_filenames_are_standard() -> None:
    expected = {
        "audio_overview.mp3",
        "video_overview.mp4",
        "slides.pdf",
        "infographic.png",
    }
    actual = {entry.filename for entry in DOWNLOAD_MAP}
    assert actual == expected


def test_max_retries_is_reasonable() -> None:
    assert 1 <= MAX_RETRIES <= 5


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


async def test_snapshot_artefact_ids() -> None:
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="id-1", type_code=_TYPE_AUDIO, status=_COMPLETED),
            _make_artifact(artifact_id="id-2", type_code=_TYPE_VIDEO, status=_FAILED),
        ]
    )
    result = await _snapshot_artefact_ids(client, "nb-1")
    assert "id-1" in result["audio"]
    assert "id-2" in result["video"]
    assert result["slides"] == set()


# ---------------------------------------------------------------------------
# Poll by type
# ---------------------------------------------------------------------------


async def test_poll_by_type_detects_completion() -> None:
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="new-id", type_code=_TYPE_AUDIO, status=_COMPLETED)
        ]
    )
    status = await _poll_by_type(client, "nb-1", "audio", set())
    assert status == "completed"


async def test_poll_by_type_detects_failure() -> None:
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="new-id", type_code=_TYPE_INFOGRAPHIC, status=_FAILED)
        ]
    )
    status = await _poll_by_type(client, "nb-1", "infographic", set())
    assert status == "failed"


async def test_poll_by_type_in_progress() -> None:
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="new-id", type_code=_TYPE_SLIDE_DECK, status=_PROCESSING)
        ]
    )
    status = await _poll_by_type(client, "nb-1", "slides", set())
    assert status == "in_progress"


async def test_poll_by_type_known_completed_still_reported() -> None:
    """Known completed IDs still report completed status."""
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="old-id", type_code=_TYPE_AUDIO, status=_COMPLETED)
        ]
    )
    status = await _poll_by_type(client, "nb-1", "audio", {"old-id"})
    assert status == "completed"


async def test_poll_ignores_other_types() -> None:
    """Polling for audio should not match a video artefact."""
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="vid-1", type_code=_TYPE_VIDEO, status=_COMPLETED)
        ]
    )
    status = await _poll_by_type(client, "nb-1", "audio", set())
    assert status == "in_progress"


# ---------------------------------------------------------------------------
# Delete existing by type
# ---------------------------------------------------------------------------


async def test_delete_existing_deletes_failed() -> None:
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="good-id", type_code=_TYPE_INFOGRAPHIC, status=_COMPLETED),
            _make_artifact(artifact_id="bad-id", type_code=_TYPE_INFOGRAPHIC, status=_FAILED),
        ]
    )
    client.artifacts.delete = AsyncMock()
    # Default: delete ALL existing (including completed)
    await _delete_existing_by_type(client, "nb-1", "infographic")
    assert client.artifacts.delete.call_count == 2


async def test_delete_existing_failed_only() -> None:
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="good-id", type_code=_TYPE_INFOGRAPHIC, status=_COMPLETED),
            _make_artifact(artifact_id="bad-id", type_code=_TYPE_INFOGRAPHIC, status=_FAILED),
        ]
    )
    client.artifacts.delete = AsyncMock()
    # failed_only=True: only delete failed, leave completed
    await _delete_existing_by_type(client, "nb-1", "infographic", failed_only=True)
    client.artifacts.delete.assert_called_once_with("nb-1", "bad-id")


async def test_delete_existing_skips_other_types() -> None:
    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            _make_artifact(artifact_id="ok-id", type_code=_TYPE_AUDIO, status=_COMPLETED)
        ]
    )
    client.artifacts.delete = AsyncMock()
    # Audio shouldn't be deleted when targeting infographic
    await _delete_existing_by_type(client, "nb-1", "infographic")
    client.artifacts.delete.assert_not_called()


# ---------------------------------------------------------------------------
# Request artefact
# ---------------------------------------------------------------------------


async def test_request_artefact_audio() -> None:
    client = MagicMock()
    client.artifacts.generate_audio = AsyncMock(return_value=FakeStatus())
    result = await _request_artefact(client, "nb-1", "audio")
    assert result.task_id == "fake-task-id"
    client.artifacts.generate_audio.assert_called_once()


async def test_request_artefact_invalid_type() -> None:
    client = MagicMock()
    with pytest.raises(KeyError):
        await _request_artefact(client, "nb-1", "podcast")
