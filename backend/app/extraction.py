from typing import Protocol

from .models import MediaAsset
from .url_validation import ValidatedUrl


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ExtractionUnavailable(ExtractionError):
    def __init__(self) -> None:
        super().__init__(
            "extraction_unavailable",
            "Media extraction is not configured. Configure an authorized media resolver.",
        )


class MediaResolver(Protocol):
    async def resolve(self, url: ValidatedUrl) -> MediaAsset: ...


class UnavailableResolver:
    async def resolve(self, url: ValidatedUrl) -> MediaAsset:
        raise ExtractionUnavailable()
