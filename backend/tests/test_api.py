import time

import httpx
from fastapi.testclient import TestClient

from app import media_proxy
from app.extraction import UnavailableResolver
from app.main import create_app
from app.models import MediaAsset, MediaDetails, MediaFormat
from app.settings import Settings


class FakeResolver:
    def __init__(self):
        self.calls = 0
        self.inspect_calls = 0

    async def resolve(self, url):
        self.calls += 1
        return MediaAsset(
            id=url.content_id,
            provider=url.provider.id,
            title="Example video",
            source_url=url.normalized_url,
            media_url="https://media.example/video.mp4",
            content_type="video/mp4",
        )

    async def inspect(self, url):
        self.inspect_calls += 1
        return MediaDetails(
            id=url.content_id,
            provider=url.provider.id,
            title="Example video",
            source_url=url.normalized_url,
            thumbnail_url="https://media.example/thumb.jpg",
            duration_seconds=12.5,
            formats=[
                MediaFormat(
                    id="http-720",
                    label="720p · MP4",
                    media_url="https://media.example/video-720.mp4",
                    content_type="video/mp4",
                    width=1280,
                    height=720,
                    size_bytes=1151376,
                ),
                MediaFormat(
                    id="http-360",
                    label="360p · MP4",
                    media_url="https://media.example/video-360.mp4",
                    content_type="video/mp4",
                    width=640,
                    height=360,
                    size_bytes=440232,
                ),
            ],
        )


def _settings(**overrides):
    values = {
        "rate_limit_per_minute": 10000,
        "rate_limit_burst": 10000,
    }
    values.update(overrides)
    return Settings(**values)


def _wait_for_job(client, job_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/downloads/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"completed", "partial", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish in time")


def test_health_and_providers():
    with TestClient(create_app(_settings())) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        providers = client.get("/api/providers").json()
        assert providers == [{"id": "x", "name": "X (Twitter)"}]


def test_api_docs_are_mounted_under_api_prefix():
    with TestClient(create_app(_settings())) as client:
        schema = client.get("/api/openapi.json")
        assert schema.status_code == 200
        assert "/api/validate" in schema.json()["paths"]
        assert client.get("/api/docs").status_code == 200


def test_validate_endpoint_accepts_x_status():
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/api/validate",
            json={"url": "https://twitter.com/opencode/status/1234567890?s=20"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["supported"] is True
    assert body["normalized_url"] == "https://x.com/i/status/1234567890"
    assert body["provider"]["id"] == "x"
    assert body["content_id"] == "1234567890"


def test_validate_endpoint_rejects_malformed_url():
    with TestClient(create_app(_settings())) as client:
        response = client.post("/api/validate", json={"url": "ftp://x.com/status/1"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_scheme"


def test_validate_endpoint_marks_unknown_provider_unsupported():
    with TestClient(create_app(_settings())) as client:
        response = client.post("/api/validate", json={"url": "https://example.com/video/1"})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["supported"] is False
    assert body["provider"] is None


def test_download_job_completes_and_second_request_hits_cache():
    resolver = FakeResolver()
    with TestClient(create_app(_settings(), resolver=resolver)) as client:
        first = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/123"]},
        )
        assert first.status_code == 202
        first_job = _wait_for_job(client, first.json()["id"])
        assert first_job["status"] == "completed"
        assert first_job["items"][0]["cache_hit"] is False
        assert first_job["items"][0]["delivery_url"] == "https://media.example/video.mp4"

        second = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/123"]},
        )
        assert second.status_code == 202
        second_job = _wait_for_job(client, second.json()["id"])
        assert second_job["items"][0]["cache_hit"] is True

        redirect = client.get("/api/media/x/123", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "https://media.example/video.mp4"

    assert resolver.calls == 1


def test_cdn_delivery_url_is_used_when_configured():
    resolver = FakeResolver()
    settings = _settings(cdn_base_url="https://cdn.example.com/media")
    with TestClient(create_app(settings, resolver=resolver)) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/456"]},
        )
        job = _wait_for_job(client, response.json()["id"])

        assert job["items"][0]["delivery_url"] == "https://cdn.example.com/media/x/456"

        redirect = client.get("/api/media/x/456", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "https://cdn.example.com/media/x/456"


def test_unavailable_resolver_reports_extraction_unavailable():
    with TestClient(create_app(_settings(), resolver=UnavailableResolver())) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/789"]},
        )
        job = _wait_for_job(client, response.json()["id"])

    assert job["status"] == "failed"
    assert job["items"][0]["error"]["code"] == "extraction_unavailable"


def test_batch_with_unsupported_entry_is_partial():
    resolver = FakeResolver()
    with TestClient(create_app(_settings(), resolver=resolver)) as client:
        response = client.post(
            "/api/downloads",
            json={
                "urls": [
                    "https://x.com/opencode/status/123",
                    "https://example.com/video/1",
                ]
            },
        )
        job = _wait_for_job(client, response.json()["id"])

    assert job["status"] == "partial"
    assert job["items"][0]["status"] == "completed"
    assert job["items"][1]["status"] == "failed"
    assert job["items"][1]["error"]["code"] == "unsupported_url"


def test_batch_without_supported_urls_is_rejected():
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://example.com/video/1", "not a url"]},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "no_supported_urls"


def test_unknown_job_returns_not_found():
    with TestClient(create_app(_settings())) as client:
        response = client.get("/api/downloads/missing")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


def test_rate_limit_returns_retry_after():
    settings = _settings(rate_limit_per_minute=2, rate_limit_burst=2)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/providers").status_code == 200
        assert client.get("/api/providers").status_code == 200
        response = client.get("/api/providers")

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"
    assert "Retry-After" in response.headers


def test_cache_miss_for_unknown_media():
    with TestClient(create_app(_settings())) as client:
        response = client.get("/api/media/x/999")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "media_not_found"


class AsyncBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"data"


def test_download_endpoint_streams_attachment_and_forwards_range(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["range"] = request.headers.get("range")
        return httpx.Response(
            206,
            headers={
                "accept-ranges": "bytes",
                "content-length": "4",
                "content-range": "bytes 0-3/10",
                "content-type": "video/mp4",
            },
            stream=AsyncBody(),
        )

    monkeypatch.setattr(
        media_proxy,
        "_build_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with TestClient(create_app(_settings(), resolver=FakeResolver())) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/123"]},
        )
        job = _wait_for_job(client, response.json()["id"])
        assert job["items"][0]["download_url"] == (
            f"/api/downloads/{job['id']}/items/0/download"
        )

        download = client.get(job["items"][0]["download_url"], headers={"Range": "bytes=0-3"})

    assert download.status_code == 206
    assert download.content == b"data"
    assert download.headers["content-disposition"] == 'attachment; filename="x-123.mp4"'
    assert download.headers["content-range"] == "bytes 0-3/10"
    assert seen == {
        "url": "https://media.example/video.mp4",
        "range": "bytes=0-3",
    }


def test_download_endpoint_rejects_private_media_source():
    class PrivateResolver(FakeResolver):
        async def resolve(self, url):
            asset = await super().resolve(url)
            return asset.model_copy(update={"media_url": "http://127.0.0.1/video.mp4"})

    with TestClient(create_app(_settings(), resolver=PrivateResolver())) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/111"]},
        )
        job = _wait_for_job(client, response.json()["id"])
        download = client.get(job["items"][0]["download_url"])

    assert download.status_code == 502
    assert download.json()["detail"]["code"] == "media_source_invalid"


def test_download_endpoint_maps_upstream_failure(monkeypatch):
    monkeypatch.setattr(
        media_proxy,
        "_build_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404))
        ),
    )

    with TestClient(create_app(_settings(), resolver=FakeResolver())) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/321"]},
        )
        job = _wait_for_job(client, response.json()["id"])
        download = client.get(job["items"][0]["download_url"])

    assert download.status_code == 502
    assert download.json()["detail"]["code"] == "media_source_unavailable"


def test_download_endpoint_requires_completed_media():
    with TestClient(create_app(_settings(), resolver=UnavailableResolver())) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/654"]},
        )
        job = _wait_for_job(client, response.json()["id"])
        download = client.get(f"/api/downloads/{job['id']}/items/0/download")

    assert download.status_code == 409
    assert download.json()["detail"]["code"] == "media_not_ready"


def test_download_endpoint_rejects_unknown_index():
    with TestClient(create_app(_settings(), resolver=FakeResolver())) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/987"]},
        )
        job = _wait_for_job(client, response.json()["id"])
        download = client.get(f"/api/downloads/{job['id']}/items/4/download")

    assert download.status_code == 404
    assert download.json()["detail"]["code"] == "media_not_found"


def test_media_details_endpoint_returns_options_and_caches():
    resolver = FakeResolver()
    with TestClient(create_app(_settings(), resolver=resolver)) as client:
        first = client.post(
            "/api/media/details",
            json={"url": "https://x.com/opencode/status/123"},
        )
        second = client.post(
            "/api/media/details",
            json={"url": "https://x.com/opencode/status/123"},
        )

    assert first.status_code == 200
    body = first.json()
    assert body["provider_name"] == "X (Twitter)"
    assert body["thumbnail_url"] == "https://media.example/thumb.jpg"
    assert body["duration_seconds"] == 12.5
    assert body["cache_hit"] is False
    assert [item["id"] for item in body["formats"]] == ["http-720", "http-360"]
    assert body["formats"][0]["label"] == "720p · MP4"
    assert "media_url" not in body["formats"][0]
    assert second.json()["cache_hit"] is True
    assert resolver.inspect_calls == 1


def test_format_download_uses_selected_resolution(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4", "content-length": "4"},
            stream=AsyncBody(),
        )

    monkeypatch.setattr(
        media_proxy,
        "_build_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with TestClient(create_app(_settings(), resolver=FakeResolver())) as client:
        client.post(
            "/api/media/details",
            json={"url": "https://x.com/opencode/status/123"},
        )
        download = client.get(
            "/api/media/x/123/download",
            params={
                "format": "http-360",
                "url": "https://x.com/opencode/status/123",
            },
        )

    assert download.status_code == 200
    assert download.content == b"data"
    assert seen["url"] == "https://media.example/video-360.mp4"
    assert download.headers["content-disposition"] == 'attachment; filename="x-123-http-360.mp4"'


def test_format_download_resolves_when_details_are_missing(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4", "content-length": "4"},
            stream=AsyncBody(),
        )

    monkeypatch.setattr(
        media_proxy,
        "_build_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    resolver = FakeResolver()

    with TestClient(create_app(_settings(), resolver=resolver)) as client:
        download = client.get(
            "/api/media/x/123/download",
            params={
                "format": "http-720",
                "url": "https://x.com/opencode/status/123",
            },
        )

    assert download.status_code == 200
    assert seen["url"] == "https://media.example/video-720.mp4"
    assert resolver.inspect_calls == 1


def test_media_details_reports_unavailable_resolver():
    with TestClient(create_app(_settings(), resolver=UnavailableResolver())) as client:
        response = client.post(
            "/api/media/details",
            json={"url": "https://x.com/opencode/status/123"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "extraction_unavailable"


def test_media_details_rejects_unsupported_url():
    with TestClient(create_app(_settings(), resolver=FakeResolver())) as client:
        response = client.post(
            "/api/media/details",
            json={"url": "https://example.com/video/1"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_url"


def test_format_download_returns_not_found_without_cached_details():
    with TestClient(create_app(_settings(), resolver=FakeResolver())) as client:
        response = client.get("/api/media/x/123/download")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "media_details_expired"


def test_format_download_rejects_unknown_resolution():
    with TestClient(create_app(_settings(), resolver=FakeResolver())) as client:
        client.post(
            "/api/media/details",
            json={"url": "https://x.com/opencode/status/123"},
        )
        download = client.get(
            "/api/media/x/123/download",
            params={"format": "missing"},
        )

    assert download.status_code == 400
    assert download.json()["detail"]["code"] == "format_not_found"
