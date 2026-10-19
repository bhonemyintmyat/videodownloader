import time

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import MediaAsset
from app.settings import Settings


class FakeResolver:
    def __init__(self):
        self.calls = 0

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


def test_default_resolver_reports_extraction_unavailable():
    with TestClient(create_app(_settings())) as client:
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
