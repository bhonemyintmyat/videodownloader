import importlib
from typing import Protocol

from .models import MediaAsset, MediaDetails
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
            "Media extraction is not configured. Set MEDIA_RESOLVER to a resolver implementation.",
        )


class MediaResolver(Protocol):
    async def resolve(self, url: ValidatedUrl) -> MediaAsset: ...

    async def inspect(self, url: ValidatedUrl) -> MediaDetails: ...


class UnavailableResolver:
    async def resolve(self, url: ValidatedUrl) -> MediaAsset:
        raise ExtractionUnavailable()

    async def inspect(self, url: ValidatedUrl) -> MediaDetails:
        raise ExtractionUnavailable()


def load_resolver(spec: str | None) -> MediaResolver | None:
    if not spec:
        return None

    module_name, separator, attribute_name = spec.partition(":")
    module_name = module_name.strip()
    attribute_name = attribute_name.strip()

    if not separator or not module_name or not attribute_name:
        raise ValueError("MEDIA_RESOLVER must use the module:attribute format.")

    module = importlib.import_module(module_name)
    target = getattr(module, attribute_name, None)
    if target is None:
        raise ValueError(f"MEDIA_RESOLVER target {spec!r} was not found.")

    resolver = target() if isinstance(target, type) else target
    if not callable(getattr(resolver, "resolve", None)):
        raise ValueError(f"MEDIA_RESOLVER target {spec!r} is not a media resolver.")

    return resolver
