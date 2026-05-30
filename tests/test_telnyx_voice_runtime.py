from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

from gateway.config import PlatformConfig

import adapter
from provisioning import (
    ProvisioningResult,
    ProvisionedState,
    delete_provisioned_state,
    load_provisioned_state,
    save_provisioned_state,
)


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


def test_validate_config_auto_provision_allows_missing_connection(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_AUTO_PROVISION", "true")
    monkeypatch.delenv("TELNYX_VOICE_FROM_NUMBER", raising=False)
    monkeypatch.delenv("TELNYX_CALL_CONTROL_CONNECTION_ID", raising=False)
    assert adapter.validate_config(PlatformConfig(enabled=True, extra={})) is True


def test_check_requirements_auto_provision_needs_only_api_key(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_AUTO_PROVISION", "true")
    monkeypatch.delenv("TELNYX_VOICE_FROM_NUMBER", raising=False)
    monkeypatch.delenv("TELNYX_CALL_CONTROL_CONNECTION_ID", raising=False)
    assert adapter.check_requirements() is True


def test_check_requirements_without_auto_provision_needs_all(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.delenv("TELNYX_VOICE_FROM_NUMBER", raising=False)
    monkeypatch.delenv("TELNYX_CALL_CONTROL_CONNECTION_ID", raising=False)
    monkeypatch.delenv("TELNYX_VOICE_AUTO_PROVISION", raising=False)
    assert adapter.check_requirements() is False


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


def test_env_enablement_auto_provision(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_AUTO_PROVISION", "true")
    monkeypatch.delenv("TELNYX_VOICE_FROM_NUMBER", raising=False)
    monkeypatch.delenv("TELNYX_CALL_CONTROL_CONNECTION_ID", raising=False)
    seed = adapter._env_enablement()
    assert seed is not None
    assert seed.get("auto_provision") is True


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
    # Speak is queued for call.answered, not sent immediately
    assert "cc-out-123" in voice._pending_speak
    assert voice._pending_speak["cc-out-123"] == "Hello by phone"
    assert len(fake.posts) == 1  # only the /calls POST, no immediate speak


@pytest.mark.asyncio
async def test_send_rejects_invalid_target(monkeypatch):
    voice = make_adapter(monkeypatch)
    result = await voice.send("not-a-number", "hello")
    assert result.success is False
    assert "Expected E.164" in result.error


@pytest.mark.asyncio
async def test_post_json_returns_error_without_session(monkeypatch):
    """_post_json should return an error dict, not create a leaked session."""
    voice = make_adapter(monkeypatch)
    voice._http_session = None  # not connected
    result = await voice._post_json("/calls", {})
    assert result.get("error")
    assert "not initialised" in result["error"]


# ---------------------------------------------------------------------------
# Call control actions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hangup_posts_hangup_action(monkeypatch):
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.hangup("cc-123")

    assert result.success is True
    assert len(fake.posts) == 1
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls/cc-123/actions/hangup"
    assert fake.posts[0]["json"]["command_id"].startswith("hermes-hangup-")


@pytest.mark.asyncio
async def test_create_conference_posts_conference_action(monkeypatch):
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.create_conference("cc-123", "my-room")

    assert result.success is True
    assert len(fake.posts) == 1
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls/cc-123/actions/conference"
    assert fake.posts[0]["json"]["name"] == "my-room"
    assert fake.posts[0]["json"]["start_conference_on_create"] is True


@pytest.mark.asyncio
async def test_start_recording_posts_record_start_action(monkeypatch):
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.start_recording("cc-123")

    assert result.success is True
    assert len(fake.posts) == 1
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls/cc-123/actions/record_start"
    assert fake.posts[0]["json"]["format"] == "mp3"
    assert fake.posts[0]["json"]["channels"] == "single"


@pytest.mark.asyncio
async def test_stop_recording_posts_record_stop_action(monkeypatch):
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.stop_recording("cc-123")

    assert result.success is True
    assert len(fake.posts) == 1
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls/cc-123/actions/record_stop"


@pytest.mark.asyncio
async def test_transfer_call_posts_transfer_action(monkeypatch):
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.transfer_call("cc-123", "+15550000099")

    assert result.success is True
    assert len(fake.posts) == 1
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls/cc-123/actions/transfer"
    assert fake.posts[0]["json"]["to"] == "+15550000099"


# ---------------------------------------------------------------------------
# Webhook handling
# ---------------------------------------------------------------------------


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
    # Webhook should ACK immediately (background tasks process answer + greet)
    assert response.status == 200

    # Give background tasks time to complete
    await __import__("asyncio").sleep(0.05)

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


@pytest.mark.asyncio
async def test_outbound_call_queues_speak_for_answered(monkeypatch):
    """Outbound call queues speak for call.answered instead of sending immediately."""
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.send("+15550000002", "Hello queued")
    assert result.success is True
    assert result.message_id == "cc-out-123"
    # Only the /calls POST; no immediate speak attempt
    assert len(fake.posts) == 1
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls"
    # Text is queued for delivery on call.answered
    assert voice._pending_speak.get("cc-out-123") == "Hello queued"


@pytest.mark.asyncio
async def test_call_answered_drains_queued_speak(monkeypatch):
    """Queued speak is delivered when call.answered webhook arrives."""
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake
    voice._greeting = None

    # Simulate a call with queued speak
    voice._active_calls["cc-ans-1"] = adapter.CallSession(
        call_control_id="cc-ans-1",
        call_session_id="sess-1",
        client_state="cs-1",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="initiated",
        started_at=0,
    )
    voice._pending_speak["cc-ans-1"] = "Queued message"

    captured = []
    async def fake_handle(event):
        captured.append(event)
    voice.handle_message = fake_handle

    payload = {
        "data": {
            "event_type": "call.answered",
            "payload": {
                "id": "evt-ans-1",
                "call_control_id": "cc-ans-1",
                "direction": "outgoing",
                "from": "+15550000001",
                "to": "+15550000002",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    # Webhook should ACK immediately
    assert response.status == 200

    # Give background task time to process
    await __import__("asyncio").sleep(0.05)

    # Queued speak should have been delivered
    assert "cc-ans-1" not in voice._pending_speak
    speak_posts = [p for p in fake.posts if "/actions/speak" in p["url"]]
    assert len(speak_posts) == 1
    assert speak_posts[0]["json"]["payload"] == "Queued message"


@pytest.mark.asyncio
async def test_hangup_cleans_up_pending_speak(monkeypatch):
    """Pending speak is removed when call hangs up without being answered."""
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    voice._active_calls["cc-hup-1"] = adapter.CallSession(
        call_control_id="cc-hup-1",
        call_session_id="sess-hup",
        client_state="cs-hup",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="initiated",
        started_at=0,
    )
    voice._pending_speak["cc-hup-1"] = "Will never be spoken"

    captured = []
    async def fake_handle(event):
        captured.append(event)
    voice.handle_message = fake_handle

    payload = {
        "data": {
            "event_type": "call.hangup",
            "payload": {
                "id": "evt-hup-1",
                "call_control_id": "cc-hup-1",
                "direction": "outgoing",
                "from": "+15550000001",
                "to": "+15550000002",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    await __import__("asyncio").sleep(0)

    assert response.status == 200
    assert "cc-hup-1" not in voice._pending_speak
    assert "cc-hup-1" not in voice._active_calls


@pytest.mark.asyncio
async def test_conference_created_webhook_is_logged(monkeypatch):
    """call.conference.created event is handled without error."""
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    voice._active_calls["cc-conf-1"] = adapter.CallSession(
        call_control_id="cc-conf-1",
        call_session_id="sess-conf",
        client_state="cs-conf",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="answered",
        started_at=0,
    )

    payload = {
        "data": {
            "event_type": "call.conference.created",
            "payload": {
                "id": "evt-conf-1",
                "call_control_id": "cc-conf-1",
                "direction": "outgoing",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    assert response.status == 200


@pytest.mark.asyncio
async def test_recording_started_webhook_is_logged(monkeypatch):
    """call.recording.started event is handled without error."""
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    voice._active_calls["cc-rec-1"] = adapter.CallSession(
        call_control_id="cc-rec-1",
        call_session_id="sess-rec",
        client_state="cs-rec",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="answered",
        started_at=0,
    )

    payload = {
        "data": {
            "event_type": "call.recording.started",
            "payload": {
                "id": "evt-rec-1",
                "call_control_id": "cc-rec-1",
                "direction": "outgoing",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    assert response.status == 200


@pytest.mark.asyncio
async def test_transferred_webhook_is_logged(monkeypatch):
    """call.transferred event is handled without error."""
    voice = make_adapter(monkeypatch)
    fake = FakeSession()
    voice._http_session = fake

    voice._active_calls["cc-xfer-1"] = adapter.CallSession(
        call_control_id="cc-xfer-1",
        call_session_id="sess-xfer",
        client_state="cs-xfer",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="answered",
        started_at=0,
    )

    payload = {
        "data": {
            "event_type": "call.transferred",
            "payload": {
                "id": "evt-xfer-1",
                "call_control_id": "cc-xfer-1",
                "direction": "outgoing",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    assert response.status == 200


def test_extract_transcript_filters_interim_results():
    """Interim (non-final) transcripts should be suppressed."""
    voice = adapter.TelnyxVoiceCallAdapter(PlatformConfig(enabled=True, extra={}))

    # is_final=False → should return empty string
    interim = voice._extract_transcript({
        "transcription_data": {"transcript": "Hel", "is_final": False}
    })
    assert interim == ""

    # is_final=True → should return the transcript
    final = voice._extract_transcript({
        "transcription_data": {"transcript": "Hello", "is_final": True}
    })
    assert final == "Hello"

    # is_final missing (legacy / non-streaming) → should still surface
    legacy = voice._extract_transcript({
        "transcription_data": {"transcript": "Hello world"}
    })
    assert legacy == "Hello world"


def test_extract_transcript_no_transcription_data():
    """Transcripts without transcription_data fall back to top-level field."""
    voice = adapter.TelnyxVoiceCallAdapter(PlatformConfig(enabled=True, extra={}))
    result = voice._extract_transcript({"transcription": "fallback text"})
    assert result == "fallback text"


# ---------------------------------------------------------------------------
# Media streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_start_posts_streaming_start_action(monkeypatch):
    """streaming_start() POSTs to /calls/{id}/actions/streaming_start with the configured stream URL."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    monkeypatch.setenv("TELNYX_VOICE_STREAM_URL", "wss://example.com/ws")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.streaming_start("cc-123")

    assert result.success is True
    assert len(fake.posts) == 1
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls/cc-123/actions/streaming_start"
    assert fake.posts[0]["json"]["stream_url"] == "wss://example.com/ws"
    assert fake.posts[0]["json"]["stream_track"] == "inbound_track"
    assert fake.posts[0]["json"]["stream_codec"] == "PCMU"
    assert fake.posts[0]["json"]["command_id"].startswith("hermes-stream-start-")


@pytest.mark.asyncio
async def test_streaming_start_with_bidirectional_options(monkeypatch):
    """streaming_start() includes bidirectional params when provided."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.streaming_start(
        "cc-123",
        stream_url="wss://example.com/ws",
        stream_track="both_tracks",
        stream_codec="G722",
        bidirectional_mode="rtp",
        bidirectional_codec="G722",
        bidirectional_sampling_rate=16000,
        bidirectional_target_legs="both",
    )

    assert result.success is True
    payload = fake.posts[0]["json"]
    assert payload["stream_url"] == "wss://example.com/ws"
    assert payload["stream_track"] == "both_tracks"
    assert payload["stream_codec"] == "G722"
    assert payload["stream_bidirectional_mode"] == "rtp"
    assert payload["stream_bidirectional_codec"] == "G722"
    assert payload["stream_bidirectional_sampling_rate"] == 16000
    assert payload["stream_bidirectional_target_legs"] == "both"


@pytest.mark.asyncio
async def test_streaming_start_rejects_without_stream_url(monkeypatch):
    """streaming_start() fails when no stream_url is configured."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    # No TELNYX_VOICE_STREAM_URL set
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake

    result = await voice.streaming_start("cc-123")

    assert result.success is False
    assert "No stream_url configured" in result.error
    assert len(fake.posts) == 0


@pytest.mark.asyncio
async def test_streaming_stop_posts_streaming_stop_action(monkeypatch):
    """streaming_stop() POSTs to /calls/{id}/actions/streaming_stop."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake
    voice._active_streams["cc-123"] = "stream-abc"

    result = await voice.streaming_stop("cc-123", stream_id="stream-abc")

    assert result.success is True
    assert len(fake.posts) == 1
    assert fake.posts[0]["url"] == "https://api.telnyx.com/v2/calls/cc-123/actions/streaming_stop"
    assert fake.posts[0]["json"]["stream_id"] == "stream-abc"
    assert fake.posts[0]["json"]["command_id"].startswith("hermes-stream-stop-")
    # active_streams should be cleaned up
    assert "cc-123" not in voice._active_streams


@pytest.mark.asyncio
async def test_streaming_stop_without_stream_id(monkeypatch):
    """streaming_stop() works without stream_id (stops all streams for the call)."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake
    voice._active_streams["cc-123"] = "stream-xyz"

    result = await voice.streaming_stop("cc-123")

    assert result.success is True
    assert "stream_id" not in fake.posts[0]["json"]
    assert "cc-123" not in voice._active_streams


@pytest.mark.asyncio
async def test_call_answered_auto_starts_streaming(monkeypatch):
    """When TELNYX_VOICE_STREAM_URL is set, streaming_start is called on call.answered."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    monkeypatch.setenv("TELNYX_VOICE_STREAM_URL", "wss://example.com/ws")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake
    voice._greeting = None

    voice._active_calls["cc-ans-2"] = adapter.CallSession(
        call_control_id="cc-ans-2",
        call_session_id="sess-2",
        client_state="cs-2",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="initiated",
        started_at=0,
    )

    captured = []
    async def fake_handle(event):
        captured.append(event)
    voice.handle_message = fake_handle

    payload = {
        "data": {
            "event_type": "call.answered",
            "payload": {
                "id": "evt-ans-2",
                "call_control_id": "cc-ans-2",
                "direction": "outgoing",
                "from": "+15550000001",
                "to": "+15550000002",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    assert response.status == 200

    await __import__("asyncio").sleep(0.05)

    # Should have made a streaming_start POST
    stream_posts = [p for p in fake.posts if "/actions/streaming_start" in p["url"]]
    assert len(stream_posts) == 1
    assert stream_posts[0]["json"]["stream_url"] == "wss://example.com/ws"


@pytest.mark.asyncio
async def test_call_hangup_stops_streaming_and_closes_ws(monkeypatch):
    """On hangup, active streams are stopped and WebSocket connections closed."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    monkeypatch.setenv("TELNYX_VOICE_STREAM_URL", "wss://example.com/ws")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake

    voice._active_calls["cc-hup-2"] = adapter.CallSession(
        call_control_id="cc-hup-2",
        call_session_id="sess-hup-2",
        client_state="cs-hup-2",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="answered",
        started_at=0,
    )
    voice._active_streams["cc-hup-2"] = "stream-456"
    # Simulate a mock WebSocket
    mock_ws = AsyncMock()
    voice._ws_connections["stream-456"] = mock_ws

    captured = []
    async def fake_handle(event):
        captured.append(event)
    voice.handle_message = fake_handle

    payload = {
        "data": {
            "event_type": "call.hangup",
            "payload": {
                "id": "evt-hup-2",
                "call_control_id": "cc-hup-2",
                "direction": "outgoing",
                "from": "+15550000001",
                "to": "+15550000002",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    assert response.status == 200

    # streaming_stop should have been called
    stop_posts = [p for p in fake.posts if "/actions/streaming_stop" in p["url"]]
    assert len(stop_posts) == 1
    assert stop_posts[0]["json"]["stream_id"] == "stream-456"
    # WebSocket should have been closed
    mock_ws.close.assert_called_once()
    # Active streams should be cleaned up
    assert "cc-hup-2" not in voice._active_streams


@pytest.mark.asyncio
async def test_streaming_started_webhook_records_stream_id(monkeypatch):
    """streaming.started webhook records the stream_id in active_streams."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake

    voice._active_calls["cc-stream-1"] = adapter.CallSession(
        call_control_id="cc-stream-1",
        call_session_id="sess-stream",
        client_state="cs-stream",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="answered",
        started_at=0,
    )

    payload = {
        "data": {
            "event_type": "streaming.started",
            "payload": {
                "id": "evt-stream-1",
                "call_control_id": "cc-stream-1",
                "stream_id": "stream-789",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    assert response.status == 200
    assert voice._active_streams["cc-stream-1"] == "stream-789"


@pytest.mark.asyncio
async def test_streaming_stopped_webhook_cleans_up(monkeypatch):
    """streaming.stopped webhook cleans up active_streams and closes ws."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake

    voice._active_calls["cc-stream-2"] = adapter.CallSession(
        call_control_id="cc-stream-2",
        call_session_id="sess-stream-2",
        client_state="cs-stream-2",
        caller_number="+15550000001",
        dialed_number="+15550000002",
        direction="outbound",
        state="answered",
        started_at=0,
    )
    voice._active_streams["cc-stream-2"] = "stream-old"
    mock_ws = AsyncMock()
    voice._ws_connections["stream-old"] = mock_ws

    payload = {
        "data": {
            "event_type": "streaming.stopped",
            "payload": {
                "id": "evt-stream-2",
                "call_control_id": "cc-stream-2",
            },
        }
    }
    request = make_mocked_request("POST", "/webhooks/telnyx/voice", headers={"Content-Type": "application/json"})
    request._read_bytes = json.dumps(payload).encode()

    response = await voice._handle_webhook(request)
    assert response.status == 200
    assert "cc-stream-2" not in voice._active_streams
    mock_ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_disconnect_stops_active_streams(monkeypatch):
    """disconnect() stops all active media streams and closes WebSockets."""
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_test")
    monkeypatch.setenv("TELNYX_VOICE_FROM_NUMBER", "+15550000001")
    monkeypatch.setenv("TELNYX_CALL_CONTROL_CONNECTION_ID", "conn-123")
    cfg = PlatformConfig(enabled=True, extra={})
    voice = adapter.TelnyxVoiceCallAdapter(cfg)
    fake = FakeSession()
    voice._http_session = fake
    voice._active_streams["cc-1"] = "s1"
    voice._active_streams["cc-2"] = "s2"
    mock_ws1 = AsyncMock()
    mock_ws2 = AsyncMock()
    voice._ws_connections["s1"] = mock_ws1
    voice._ws_connections["s2"] = mock_ws2
    # Simulate a connected runner
    from unittest.mock import MagicMock
    voice._runner = MagicMock()
    voice._runner.cleanup = AsyncMock()

    await voice.disconnect()

    # streaming_stop should have been called for both calls
    stop_posts = [p for p in fake.posts if "/actions/streaming_stop" in p["url"]]
    assert len(stop_posts) == 2
    # WebSocket connections should be closed
    mock_ws1.close.assert_called_once()
    mock_ws2.close.assert_called_once()
    # Internal state should be clean
    assert len(voice._active_streams) == 0
    assert len(voice._ws_connections) == 0


# ---------------------------------------------------------------------------
# Auto-provisioning
# ---------------------------------------------------------------------------


def test_provisioning_state_round_trip(tmp_path):
    """Provisioned state can be saved and loaded."""
    result = ProvisioningResult(
        application_id="app-123",
        connection_id="conn-456",
        from_number="+15550009999",
        number_order_id="order-789",
        phone_number_id="pn-001",
    )
    state = ProvisionedState(result=result, provisioned_at="2026-05-24T00:00:00+00:00")
    save_provisioned_state(state, store_path=str(tmp_path))

    loaded = load_provisioned_state(store_path=str(tmp_path))
    assert loaded is not None
    assert loaded.result.connection_id == "conn-456"
    assert loaded.result.from_number == "+15550009999"
    assert loaded.result.phone_number_id == "pn-001"
    assert loaded.provisioned_at == "2026-05-24T00:00:00+00:00"


def test_provisioning_state_missing_returns_none(tmp_path):
    assert load_provisioned_state(store_path=str(tmp_path)) is None


def test_provisioning_state_delete_is_idempotent(tmp_path):
    """Deleting state when none exists does not raise."""
    delete_provisioned_state(store_path=str(tmp_path))
    delete_provisioned_state(store_path=str(tmp_path))


@pytest.mark.asyncio
async def test_provision_skips_when_already_configured(monkeypatch):
    """provision() returns immediately when both connection_id and from_number are set."""
    result = await adapter.provision(
        "KEY_test",
        "https://example.com/webhook",
        connection_id="conn-existing",
        from_number="+15550000001",
    )
    assert result.connection_id == "conn-existing"
    assert result.from_number == "+15550000001"
    assert result.number_order_id == ""


@pytest.mark.asyncio
async def test_provision_loads_persisted_state(tmp_path, monkeypatch):
    """provision() returns persisted state if provisioned.json exists."""
    from provisioning import save_provisioned_state, ProvisionedState, ProvisioningResult

    existing = ProvisionedState(
        result=ProvisioningResult(
            application_id="app-persisted",
            connection_id="conn-persisted",
            from_number="+15550008888",
            number_order_id="order-persisted",
        ),
        provisioned_at="2026-01-01T00:00:00+00:00",
    )
    save_provisioned_state(existing, store_path=str(tmp_path))

    result = await adapter.provision(
        "KEY_test",
        "https://example.com/webhook",
        store_path=str(tmp_path),
    )
    assert result.connection_id == "conn-persisted"
    assert result.from_number == "+15550008888"
