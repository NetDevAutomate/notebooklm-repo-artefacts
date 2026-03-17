"""Minimal stubs for notebooklm package."""

from enum import StrEnum
from typing import Any

class NotebookLMClient:
    @classmethod
    async def from_storage(cls) -> NotebookLMClient: ...
    async def refresh_auth(self) -> None: ...
    async def __aenter__(self) -> NotebookLMClient: ...
    async def __aexit__(self, *args: Any) -> None: ...
    @property
    def notebooks(self) -> Any: ...
    @property
    def sources(self) -> Any: ...
    @property
    def artifacts(self) -> Any: ...

class GenerationStatus:
    task_id: str | None
    status: str
    error: str | None
    error_code: str | None
    metadata: dict[str, Any] | None
    @property
    def is_complete(self) -> bool: ...
    @property
    def is_failed(self) -> bool: ...

class ArtifactType(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"
    SLIDE_DECK = "slide_deck"
    INFOGRAPHIC = "infographic"
    REPORT = "report"
    QUIZ = "quiz"
    FLASHCARDS = "flashcards"
    MIND_MAP = "mind_map"
    DATA_TABLE = "data_table"
    UNKNOWN = "unknown"

class Artifact:
    id: str
    kind: ArtifactType
    status: int
    title: str | None
    url: str | None
    created_at: Any | None
    @property
    def is_completed(self) -> bool: ...
    @property
    def is_failed(self) -> bool: ...

class AudioFormat(StrEnum):
    DEEP_DIVE = "deep_dive"

class VideoStyle(StrEnum):
    WHITEBOARD = "whiteboard"

class InfographicDetail(StrEnum):
    STANDARD = "standard"

class InfographicOrientation(StrEnum):
    LANDSCAPE = "landscape"

class Notebook:
    id: str
    title: str

class Source:
    id: str
    title: str

class AuthError(Exception): ...
class RateLimitError(Exception): ...
class RPCError(Exception): ...
class NotebookLMError(Exception): ...
