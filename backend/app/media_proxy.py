from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx

from .models import MediaAsset


_FORWARDED_HEADERS = {
    "accept-ranges",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "last-modified",
}

_EXTENSIONS = {
    "application/vnd.apple.mpegurl": ".m3u8",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


class MediaProxyError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 502,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.headers = headers


@dataclass
class MediaStream:
    status_code: int
    headers: dict[str, str]
    media_type: str
    _response: httpx.Response
    _client: httpx.AsyncClient

    async def iter_chunks(self):
        try:
            async for chunk in self._response.aiter_raw():
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        await self._response.aclose()
        await self._client.aclose()


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, read=60.0),
        follow_redirects=True,
        trust_env=False,
    )


def _parse_url(value: str):
    try:
        return urlsplit(value)
    except ValueError as exc:
        raise MediaProxyError(
            "media_source_invalid",
            "The media source URL is not valid.",
        ) from exc


def _assert_safe_url(value: str) -> None:
    parsed = _parse_url(value)
    host = (parsed.hostname or "").lower().rstrip(".")

    if parsed.scheme not in {"http", "https"} or not host:
        raise MediaProxyError(
            "media_source_invalid",
            "The media source URL is not valid.",
        )

    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise MediaProxyError(
            "media_source_invalid",
            "The media source URL is not valid.",
        )

    try:
        address = ip_address(host)
    except ValueError:
        return

    if not address.is_global:
        raise MediaProxyError(
            "media_source_invalid",
            "The media source URL is not valid.",
        )


def _is_hls(asset: MediaAsset) -> bool:
    if asset.content_type == "application/vnd.apple.mpegurl":
        return True
    return _parse_url(asset.media_url).path.lower().endswith(".m3u8")


def _filename(asset: MediaAsset) -> str:
    extension = _EXTENSIONS.get(asset.content_type, ".mp4")
    return f"x-{asset.id}{extension}"


async def open_media_stream(
    asset: MediaAsset,
    range_header: str | None = None,
) -> MediaStream:
    _assert_safe_url(asset.media_url)

    if _is_hls(asset):
        raise MediaProxyError(
            "media_not_downloadable",
            "HLS media cannot be downloaded as a single file.",
            status_code=409,
        )

    client = _build_client()
    request_headers = {"Accept-Encoding": "identity"}
    if range_header:
        request_headers["Range"] = range_header

    try:
        request = client.build_request("GET", asset.media_url, headers=request_headers)
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise MediaProxyError(
            "media_source_unavailable",
            "The media source is unavailable.",
        ) from exc

    if response.status_code == 416:
        content_range = response.headers.get("content-range")
        await response.aclose()
        await client.aclose()
        raise MediaProxyError(
            "range_not_satisfiable",
            "The requested byte range is not available.",
            status_code=416,
            headers={"Content-Range": content_range} if content_range else None,
        )

    if response.status_code not in {200, 206}:
        await response.aclose()
        await client.aclose()
        raise MediaProxyError(
            "media_source_unavailable",
            "The media source returned an error.",
        )

    try:
        _assert_safe_url(str(response.url))
    except MediaProxyError:
        await response.aclose()
        await client.aclose()
        raise

    forwarded = {
        key.lower(): value
        for key, value in response.headers.items()
        if key.lower() in _FORWARDED_HEADERS
    }
    forwarded["Content-Disposition"] = f'attachment; filename="{_filename(asset)}"'
    forwarded["Cache-Control"] = "private, no-store"
    forwarded["X-Content-Type-Options"] = "nosniff"

    return MediaStream(
        status_code=response.status_code,
        headers=forwarded,
        media_type=response.headers.get("content-type") or asset.content_type,
        _response=response,
        _client=client,
    )
