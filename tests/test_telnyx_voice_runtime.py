from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

from gateway.config import PlatformConfig

import adapter


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body if body is not None else {"data": {"id": "ok"}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._body

    async def text(self):
        return json.dumps(self._body)


class FakeSession:
    def __init__(self):
        self.posts = []
        self.closed = False

    def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        if url.endswith("/calls"):
            return FakeResponse(body={"data": {"call_control_id": "cc-out-123"}})
        return FakeResponse(body={"data": {"id": "action-123"}})

    async def close(self):
        self.closed = True


def make_adapter(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    cfg = PlatformConfig(enabled=True, extra={})
    return adapter.TelnyxVoiceCallAdapter(cfg)


def test_validate_config_accepts_env_without_extra(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    assert adapter.validate_config(PlatformConfig(enabled=True, extra={})) is True


def test_env_enablement(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    monkeypatch.setenv("TELNYX_VOICE_HOME_CHANNEL", "+15550000002")
    seed = adapter._env_enablement()
    assert seed["from_number"] == "+15550000001"
    assert seed["connection_id"] == "conn-123"
    assert seed["webhook_port"] == "8088"
    assert seed["home_channel"]["chat_id"] == "+15550000002"


@pytest.mark.asyncio
async def test_send_to_active_call_posts_speak(monkeypatch):
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.send(
        "call_control:cc-123",
        "**Hello** [docs](https://example.com)",
    )

    assert result.success is True
    assert result.message_id == "cc-123"
    assert len(fake.posts) == 1
    post = fake.posts[0]
    assert post["url"] == "https://api.telnyx.com/v2/calls/cc-123/actions/speak"
    assert post["headers"]["Authorization"] == "Bearer KEY_test"
    assert post["json"]["payload"] == "Hello docs"
    assert post["json"]["voice"] == adapter.DEFAULT_VOICE


@pytest.mark.asyncio
async def test_send_to_phone_number_creates_call(monkeypatch):
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.send("+15550000002", "Hello by phone")

    assert result.success is True
    assert result.message_id == "cc-out-123"
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls"
    assert fake.posts[0]["json"]["connection_id"] == "conn-123"
    assert fake.posts[0]["json"]["from"] == "+15550000001"
    assert fake.posts[0]["json"]["to"] == "+15550000002"
    assert fake.posts[1]["url"] == "https://api.telnyx.com/v2/calls/cc-out-123/actions/speak"


@pytest.mark.asyncio
async def test_send_rejects_invalid_target(monkeypatch):
    voice = make_adapter(monkeypatch)
    result = await voice.send("not-a-number", "hello")
    assert result.success is False
    assert "Expected E.164" in result.error


@pytest.mark.asyncio
async def test_handle_inbound_call_answers_and_emits_message(monkeypatch):
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake
    voice._greeting = "Hello, this is Hermes."
    captured = []

    async def fake_handle(event):
        captured.append(event)

    voice.handle_message = fake_handle
    payload = {
        "data": {
            "event_type": "call.initiated",
            "payload": {
                "id": "evt-1",
                "call_control_id": "cc-in-123",
                "direction": "incoming",
                "from": "+15550000002",
                "to": "+15550000001",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    await __import__("asyncio").sleep(0)

    assert response.status == 200
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls/cc-in-123/actions/answer"
    assert fake.posts[1]["url"] == "https://api.telnyx.com/v2/calls/cc-in-123/actions/speak"
    assert len(captured) == 1
    event = captured[0]
    assert event.message_id == "cc-in-123"
    assert event.source.chat_id == "call_control:cc-in-123"
    assert "Incoming Telnyx voice call" in event.text


def test_signature_required_without_public_key_is_invalid(monkeypatch):
    voice = make_adapter(monkeypatch)
    voice._require_signature = True
    voice._public_key = ""
    assert voice._validate_telnyx_signature(b"{}", {}) is False


def test_signature_rejects_wrong_key(monkeypatch):
    """Verify that a valid signature from a different key is rejected."""
    pytest.importorskip("nacl")
    import base64
    import time as _time
    from nacl.signing import SigningKey

    signing_key = SigningKey.generate()
    wrong_key = SigningKey.generate().verify_key
    public_key_b64 = base64.b64encode(bytes(wrong_key)).decode()

    body = b'{"test": true}'
    timestamp = str(int(_time.time()))
    signed = signing_key.sign(f"{timestamp}|".encode() + body)
    signature_b64 = base64.b64encode(signed.signature).decode()

    voice = make_adapter(monkeypatch)
    voice._public_key = public_key_b64
    voice._require_signature = True

    result = voice._validate_telnyx_signature(body, {
        "Telnyx-Signature-Ed25519": signature_b64,
        "Telnyx-Timestamp": timestamp,
    })
    assert result is False, "wrong key must reject"


@pytest.mark.asyncio
async def test_handle_webhook_rejects_missing_event_type(monkeypatch):
    """Payloads without event_type should be rejected (not processed)."""
    voice = make_adapter(monkeypatch)
    called = False

    async def fake_handle(event):
        nonlocal called
        called = True

    voice.handle_message = fake_handle
    payload = {"data": {"payload": {"id": "x", "call_control_id": "cc-x"}}}
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    await __import__("asyncio").sleep(0)

    assert response.status == 200
    assert called is False


def test_signature_verification_with_base64_keypair(monkeypatch):
    pytest.importorskip("nacl")
    import base64
    import time as _time
    from nacl.signing import SigningKey

    signing_key = SigningKey.generate()
    public_key_b64 = base64.b64encode(bytes(signing_key.verify_key)).decode()
    body = b'{"data":{"event_type":"call.initiated"}}'
    timestamp = str(int(_time.time()))
    signed = signing_key.sign(f"{timestamp}|".encode() + body)
    signature_b64 = base64.b64encode(signed.signature).decode()

    voice = make_adapter(monkeypatch)
    voice._public_key = public_key_b64
    voice._require_signature = True

    assert voice._validate_telnyx_signature(body, {
        "Telnyx-Signature-Ed25519": signature_b64,
        "Telnyx-Timestamp": timestamp,
    }) is True
