import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app
from app.settings import Settings
from app.state import MemoryState, RedisState, StateBackendError, create_state


class _BrokenState:
    async def rate_limit(self, key):
        from app.rate_limit import RateLimitDecision

        return RateLimitDecision(allowed=True, limit=100, remaining=100, retry_after=0)

    async def cache_get(self, key):
        return None

    async def cache_set(self, key, value, ttl_seconds):
        return None

    async def job_get(self, job_id):
        raise StateBackendError("down")

    async def job_save(self, job_id, value, ttl_seconds):
        raise StateBackendError("down")

    async def close(self):
        return None


def _settings(**overrides):
    values = {
        "rate_limit_per_minute": 10000,
        "rate_limit_burst": 10000,
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_detect_upstash_environment(monkeypatch):
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://redis.example")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "token")

    settings = Settings.from_env()

    assert settings.redis_rest_url == "https://redis.example"
    assert settings.redis_rest_token == "token"


def test_settings_detect_vercel_kv_environment(monkeypatch):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    monkeypatch.setenv("KV_REST_API_URL", "https://kv.example")
    monkeypatch.setenv("KV_REST_API_TOKEN", "kv-token")

    settings = Settings.from_env()

    assert settings.redis_rest_url == "https://kv.example"
    assert settings.redis_rest_token == "kv-token"


def test_settings_reject_partial_redis_configuration(monkeypatch):
    monitor_vars = (
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "KV_REST_API_URL",
        "KV_REST_API_TOKEN",
    )
    for name in monitor_vars:
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("KV_REST_API_URL", "https://kv.example")

    with pytest.raises(ValueError):
        Settings.from_env()


def test_create_state_uses_memory_without_credentials():
    assert isinstance(create_state(_settings()), MemoryState)


def test_create_state_uses_redis_with_credentials():
    settings = _settings(
        redis_rest_url="https://redis.example",
        redis_rest_token="token",
    )
    state = create_state(settings)

    assert isinstance(state, RedisState)


def test_redis_state_uses_rest_commands_and_auth():
    seen_commands = []
    seen_auth = []

    def handler(request):
        seen_auth.append(request.headers.get("authorization"))
        command = json.loads(request.content.decode())
        seen_commands.append(command[0])
        if command[0] == "EVAL":
            return httpx.Response(200, json={"result": [1, "1.5", 0]})
        if command[0] == "GET":
            return httpx.Response(200, json={"result": '{"value": 1}'})
        return httpx.Response(200, json={"result": "OK"})

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        state = RedisState(
            _settings(redis_rest_url="https://redis.example", redis_rest_token="token"),
            http_client=client,
        )

        assert await state.cache_get("x:1") == '{"value": 1}'
        await state.cache_set("x:1", "payload", 60)
        assert await state.job_get("job-1") == '{"value": 1}'
        await state.job_save("job-1", "{}", 30)

        decision = await state.rate_limit("client")
        assert decision.allowed is True
        assert decision.remaining == 1

        await state.close()
        await client.aclose()

    asyncio.run(scenario())

    assert "EVAL" in seen_commands
    assert "SETEX" in seen_commands
    assert all(value == "Bearer token" for value in seen_auth)


def test_redis_state_fails_open_for_rate_limit_but_raises_for_jobs():
    def handler(request):
        return httpx.Response(500, text="backend down")

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        state = RedisState(
            _settings(redis_rest_url="https://redis.example", redis_rest_token="token"),
            http_client=client,
        )

        decision = await state.rate_limit("client")
        assert decision.allowed is True
        assert await state.cache_get("x:1") is None

        with pytest.raises(StateBackendError):
            await state.job_get("job-1")

        await state.close()
        await client.aclose()

    asyncio.run(scenario())


def test_state_backend_failure_returns_service_unavailable(monkeypatch):
    monkeypatch.setattr(main_module, "create_state", lambda settings: _BrokenState())

    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/api/downloads",
            json={"urls": ["https://x.com/opencode/status/123"]},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "state_unavailable"
