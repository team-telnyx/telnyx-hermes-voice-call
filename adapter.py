"""Telnyx Voice Call platform adapter for Hermes Agent.

Plugin-first Hermes platform adapter that exposes Telnyx Call Control as
``telnyx_voice_call`` without modifying Hermes core gateway code.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.platforms.helpers import redact_phone, strip_markdown
from gateway.session import SessionSource

from provisioning import ProvisioningResult, deprovision, provision

logger = logging.getLogger(__name__)

TELNYX_API_BASE = "https://api.telnyx.com/v2"
DEFAULT_WEBHOOK_HOST = "127.0.0.1"
DEFAULT_WEBHOOK_PORT = 8088
DEFAULT_WEBHOOK_PATH = "/webhooks/telnyx/voice"
DEFAULT_VOICE = "Telnyx.NaturalHD.astra"
DEFAULT_LANGUAGE = "en-US"
MAX_SPEAK_LENGTH = 5000
WEBHOOK_BODY_MAX_BYTES = 1_048_576
E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
CALL_CONTROL_PREFIX = "call_control:"
REPLAY_WINDOW_SECONDS = 10 * 60
REPLAY_CACHE_MAX_ENTRIES = 10_000
DEFAULT_AUTO_PROVISION = False


@dataclass
class CallSession:
    """In-memory state for a Telnyx Call Control leg."""

    call_control_id: str
    call_session_id: str = ""
    client_state: str = ""
    caller_number: str = ""
    dialed_number: str = ""
    direction: str = ""
    state: str = "initiated"
    started_at: float = 0.0


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _split_csv(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return [str(part).strip() for part in parts if str(part).strip()]


def _first_phone(value: Any) -> str:
    """Extract an E.164-ish number from Telnyx webhook values."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return _first_phone(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("phone_number", "number", "ani", "dnis"):
            raw = value.get(key)
            if raw:
                return str(raw).strip()
    return ""


def _call_control_chat_id(call_control_id: str) -> str:
    return f"{CALL_CONTROL_PREFIX}{call_control_id}"


def _strip_call_control_prefix(chat_id: str) -> str:
    raw = str(chat_id or "").strip()
    if raw.startswith(CALL_CONTROL_PREFIX):
        return raw[len(CALL_CONTROL_PREFIX):]
    return raw


def _decode_base64_or_urlsafe(value: str) -> bytes:
    normalized = value.strip().replace("-", "+").replace("_", "/")
    padded = normalized + "=" * ((4 - len(normalized) % 4) % 4)
    return base64.b64decode(padded)


def _decode_client_state(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _decode_base64_or_urlsafe(value).decode("utf-8")
    except Exception:
        return str(value)


def _encode_client_state(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def check_requirements() -> bool:
    """Return True when runtime deps and minimum Telnyx credentials exist."""
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    # API key is always required; connection_id + from_number are optional
    # when auto-provision is enabled.
    if _truthy(_env("TELNYX_VOICE_AUTO_PROVISION")):
        return bool(_env("TELNYX_API_KEY"))
    return bool(
        _env("TELNYX_API_KEY")
        and _env("TELNYX_VOICE_FROM_NUMBER")
        and _env("TELNYX_CALL_CONTROL_CONNECTION_ID")
    )


def validate_config(config: PlatformConfig) -> bool:
    extra = getattr(config, "extra", {}) or {}
    api_key = _env("TELNYX_API_KEY") or str(extra.get("api_key", "")).strip()
    if not api_key:
        return False
    # When auto-provision is enabled, connection_id and from_number can be omitted.
    if _truthy(_env("TELNYX_VOICE_AUTO_PROVISION")) or _truthy(str(extra.get("auto_provision", ""))):
        return True
    from_number = _env("TELNYX_VOICE_FROM_NUMBER") or str(extra.get("from_number", "")).strip()
    connection_id = _env("TELNYX_CALL_CONTROL_CONNECTION_ID") or str(extra.get("connection_id", "")).strip()
    return bool(api_key and from_number and connection_id)


def is_connected(config: PlatformConfig) -> bool:
    return validate_config(config)


def _env_enablement() -> dict | None:
    api_key = _env("TELNYX_API_KEY")
    if not api_key:
        return None

    # With auto-provision, connection_id and from_number are optional.
    from_number = _env("TELNYX_VOICE_FROM_NUMBER")
    connection_id = _env("TELNYX_CALL_CONTROL_CONNECTION_ID")
    auto_provision = _truthy(_env("TELNYX_VOICE_AUTO_PROVISION"))
    if not (from_number and connection_id) and not auto_provision:
        return None

    seed: dict[str, Any] = {
        "from_number": from_number,
        "connection_id": connection_id,
        "webhook_host": _env("TELNYX_VOICE_WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST),
        "webhook_port": _env("TELNYX_VOICE_WEBHOOK_PORT", str(DEFAULT_WEBHOOK_PORT)),
        "webhook_path": _env("TELNYX_VOICE_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH),
    }
    if auto_provision:
        seed["auto_provision"] = True
    webhook_url = _env("TELNYX_VOICE_WEBHOOK_URL")
    if webhook_url:
        seed["webhook_url"] = webhook_url
    home = _env("TELNYX_VOICE_HOME_CHANNEL")
    if home:
        seed["home_channel"] = {"chat_id": home, "name": home}
    return seed


class TelnyxVoiceCallAdapter(BasePlatformAdapter):
    """Telnyx Call Control <-> Hermes gateway adapter."""

    MAX_MESSAGE_LENGTH = MAX_SPEAK_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("telnyx_voice_call"))
        extra = getattr(config, "extra", {}) or {}
        self._api_key = _env("TELNYX_API_KEY") or str(extra.get("api_key", "")).strip()
        self._from_number = _env("TELNYX_VOICE_FROM_NUMBER") or str(extra.get("from_number", "")).strip()
        self._connection_id = _env("TELNYX_CALL_CONTROL_CONNECTION_ID") or str(extra.get("connection_id", "")).strip()
        self._api_base = (_env("TELNYX_VOICE_API_BASE") or str(extra.get("api_base", TELNYX_API_BASE))).rstrip("/")
        self._webhook_url = _env("TELNYX_VOICE_WEBHOOK_URL") or str(extra.get("webhook_url", "")).strip()
        self._webhook_host = _env("TELNYX_VOICE_WEBHOOK_HOST") or str(extra.get("webhook_host", DEFAULT_WEBHOOK_HOST))
        try:
            self._webhook_port = int(_env("TELNYX_VOICE_WEBHOOK_PORT") or str(extra.get("webhook_port", DEFAULT_WEBHOOK_PORT)))
        except ValueError:
            raw_port = _env("TELNYX_VOICE_WEBHOOK_PORT") or str(extra.get("webhook_port", ""))
            logger.error("[telnyx_voice_call] TELNYX_VOICE_WEBHOOK_PORT is invalid: %r", raw_port)
            self._webhook_port = DEFAULT_WEBHOOK_PORT
        self._webhook_path = _env("TELNYX_VOICE_WEBHOOK_PATH") or str(extra.get("webhook_path", DEFAULT_WEBHOOK_PATH))
        self._public_key = _env("TELNYX_PUBLIC_KEY") or str(extra.get("public_key", "")).strip()
        self._require_signature = _truthy(_env("TELNYX_VOICE_REQUIRE_SIGNATURE") or str(extra.get("require_signature", "")))
        self._voice = _env("TELNYX_VOICE_DEFAULT_VOICE") or str(extra.get("voice", DEFAULT_VOICE))
        self._language = _env("TELNYX_VOICE_LANGUAGE") or str(extra.get("language", DEFAULT_LANGUAGE))
        self._greeting = _env("TELNYX_VOICE_GREETING") or str(extra.get("greeting", "")).strip()
        try:
            self._signature_tolerance = int(_env("TELNYX_VOICE_SIGNATURE_TOLERANCE") or str(extra.get("signature_tolerance", "300")))
        except ValueError:
            self._signature_tolerance = 300
        self._auto_provision = _truthy(_env("TELNYX_VOICE_AUTO_PROVISION") or str(extra.get("auto_provision", ""))) or DEFAULT_AUTO_PROVISION
        self._active_calls: dict[str, CallSession] = {}
        self._pending_speak: dict[str, str] = {}  # call_control_id → queued text
        self._replay_cache: dict[str, float] = {}
        self._provisioned: Optional[ProvisioningResult] = None
        self._runner = None
        self._http_session: Optional["aiohttp.ClientSession"] = None

    async def connect(self) -> bool:
        import aiohttp
        from aiohttp import web

        if not self._api_key:
            msg = "[telnyx_voice_call] TELNYX_API_KEY not set"
            logger.error(msg)
            self._set_fatal_error("telnyx_voice_missing_api_key", msg, retryable=False)
            return False

        # Auto-provision: if enabled and connection_id/from_number are missing,
        # create a Call Control app + order a number automatically.
        if self._auto_provision and (not self._connection_id or not self._from_number):
            try:
                webhook_url = self._webhook_url or f"http://{self._webhook_host}:{self._webhook_port}{self._webhook_path}"
                result = await provision(
                    self._api_key,
                    webhook_url,
                    connection_id=self._connection_id,
                    from_number=self._from_number,
                )
                self._connection_id = result.connection_id
                self._from_number = result.from_number
                self._provisioned = result
                logger.info(
                    "[telnyx_voice_call] auto-provisioned: from=%s, connection_id=%s",
                    redact_phone(self._from_number),
                    self._connection_id,
                )
            except Exception as exc:
                msg = f"[telnyx_voice_call] auto-provisioning failed: {exc}"
                logger.error(msg)
                self._set_fatal_error("telnyx_voice_provision_failed", msg, retryable=True)
                return False

        if not self._from_number:
            msg = "[telnyx_voice_call] TELNYX_VOICE_FROM_NUMBER not set"
            logger.error(msg)
            self._set_fatal_error("telnyx_voice_missing_from_number", msg, retryable=False)
            return False
        if not self._connection_id:
            msg = "[telnyx_voice_call] TELNYX_CALL_CONTROL_CONNECTION_ID not set"
            logger.error(msg)
            self._set_fatal_error("telnyx_voice_missing_connection_id", msg, retryable=False)
            return False
        if self._require_signature and not self._public_key:
            msg = "[telnyx_voice_call] signature verification required but TELNYX_PUBLIC_KEY is not set"
            logger.error(msg)
            self._set_fatal_error("telnyx_voice_missing_public_key", msg, retryable=False)
            return False
        if self._public_key or self._require_signature:
            try:
                from nacl.signing import VerifyKey  # noqa: F401
            except ImportError:
                msg = "[telnyx_voice_call] PyNaCl is required for webhook signature validation"
                logger.error(msg)
                self._set_fatal_error("telnyx_voice_missing_pynacl", msg, retryable=False)
                return False

        app = web.Application(client_max_size=WEBHOOK_BODY_MAX_BYTES)
        app.router.add_post(self._webhook_path, self._handle_webhook)
        app.router.add_get("/health", lambda _: web.Response(text="ok"))

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._webhook_host, self._webhook_port)
        await site.start()
        self._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self._mark_connected()
        logger.info(
            "[telnyx_voice_call] webhook server listening on %s:%d%s, from: %s",
            self._webhook_host,
            self._webhook_port,
            self._webhook_path,
            redact_phone(self._from_number),
        )
        return True

    async def disconnect(self) -> None:
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        # Deprovision auto-created resources on graceful shutdown.
        if self._provisioned and self._api_key:
            try:
                await deprovision(self._api_key)
            except Exception as exc:
                logger.warning("[telnyx_voice_call] deprovision failed: %s", exc)
        self._mark_disconnected()
        logger.info("[telnyx_voice_call] disconnected")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        target = str(chat_id or "").strip()
        if target.startswith(CALL_CONTROL_PREFIX):
            call_control_id = _strip_call_control_prefix(target)
            session = self._active_calls.get(call_control_id)
            name = session.caller_number if session and session.caller_number else target
        else:
            name = target
        return {"name": name, "type": "dm"}

    def format_message(self, content: str) -> str:
        return strip_markdown(content or "")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        target = str(chat_id or "").strip()
        text = self.format_message(content).strip()
        if not text:
            return SendResult(success=False, error="Cannot speak an empty message")

        if target.startswith(CALL_CONTROL_PREFIX):
            call_control_id = _strip_call_control_prefix(target)
            # If the call hasn't been answered yet, queue the speak instead
            # of sending it into a dead call (Telnyx returns 422).
            session = self._active_calls.get(call_control_id)
            if session and session.state not in ("answered", "active"):
                logger.info("[telnyx_voice_call] call not answered yet; queuing speak for %s", call_control_id)
                self._pending_speak[call_control_id] = text
                return SendResult(success=True, message_id=call_control_id)
            return await self._speak(call_control_id, text)

        if E164_RE.match(target):
            return await self._create_outbound_call(target, text)

        return SendResult(success=False, error="Expected E.164 phone number or call_control:<id>")

    async def _create_outbound_call(self, to_number: str, text: str) -> SendResult:
        client_state = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "connection_id": self._connection_id,
            "to": to_number,
            "from": self._from_number,
            "webhook_url_method": "POST",
            "client_state": _encode_client_state(client_state),
            "timeout_secs": 30,
        }
        if self._webhook_url:
            payload["webhook_url"] = self._webhook_url

        response = await self._post_json("/calls", payload)
        if response.get("error"):
            return SendResult(success=False, error=response["error"], raw_response=response, retryable=response.get("retryable", False))

        data = response.get("data") or response
        call_control_id = str(data.get("call_control_id") or data.get("id") or "")
        if call_control_id:
            self._active_calls[call_control_id] = CallSession(
                call_control_id=call_control_id,
                call_session_id=str(data.get("call_session_id") or ""),
                client_state=client_state,
                caller_number=self._from_number,
                dialed_number=to_number,
                direction="outbound",
                state="initiated",
                started_at=time.time(),
            )
            # Queue the speak for delivery on call.answered — the call
            # is never answered yet at this point so an immediate speak would
            # always 422 and waste a roundtrip.
            self._pending_speak[call_control_id] = text
            logger.info("[telnyx_voice_call] outbound call created; speak queued for call.answered")
            return SendResult(success=True, message_id=call_control_id, raw_response=response)
        return SendResult(success=True, raw_response=response)

    async def _speak(self, call_control_id: str, text: str) -> SendResult:
        if not call_control_id:
            return SendResult(success=False, error="Missing call_control_id")
        if len(text) > MAX_SPEAK_LENGTH:
            logger.warning("[telnyx_voice_call] speak text truncated from %d to %d chars for %s", len(text), MAX_SPEAK_LENGTH, call_control_id)
            text = text[:MAX_SPEAK_LENGTH]
        payload = {
            "command_id": f"hermes-speak-{uuid.uuid4()}",
            "payload": text,
            "voice": self._voice,
            "language": self._language,
        }
        response = await self._post_json(f"/calls/{call_control_id}/actions/speak", payload)
        if response.get("error"):
            return SendResult(success=False, error=response["error"], raw_response=response, retryable=response.get("retryable", False))
        return SendResult(success=True, message_id=call_control_id, raw_response=response)

    # ------------------------------------------------------------------
    # Call Control actions
    # ------------------------------------------------------------------

    async def hangup(self, call_control_id: str) -> SendResult:
        """Hang up an active call."""
        response = await self._post_json(
            f"/calls/{call_control_id}/actions/hangup",
            {"command_id": f"hermes-hangup-{uuid.uuid4()}"},
        )
        if response.get("error"):
            return SendResult(success=False, error=response["error"], raw_response=response, retryable=response.get("retryable", False))
        return SendResult(success=True, message_id=call_control_id, raw_response=response)

    async def create_conference(self, call_control_id: str, conference_name: str) -> SendResult:
        """Add a call leg to a conference bridge."""
        payload = {
            "command_id": f"hermes-conf-{uuid.uuid4()}",
            "name": conference_name,
            "beep_enabled": "never",
            "start_conference_on_create": True,
        }
        response = await self._post_json(f"/calls/{call_control_id}/actions/conference", payload)
        if response.get("error"):
            return SendResult(success=False, error=response["error"], raw_response=response, retryable=response.get("retryable", False))
        return SendResult(success=True, message_id=call_control_id, raw_response=response)

    async def start_recording(self, call_control_id: str, *, format: str = "mp3", channels: str = "single") -> SendResult:
        """Start recording a call."""
        payload = {
            "command_id": f"hermes-rec-start-{uuid.uuid4()}",
            "format": format,
            "channels": channels,
        }
        response = await self._post_json(f"/calls/{call_control_id}/actions/record_start", payload)
        if response.get("error"):
            return SendResult(success=False, error=response["error"], raw_response=response, retryable=response.get("retryable", False))
        return SendResult(success=True, message_id=call_control_id, raw_response=response)

    async def stop_recording(self, call_control_id: str) -> SendResult:
        """Stop recording a call."""
        payload = {
            "command_id": f"hermes-rec-stop-{uuid.uuid4()}",
        }
        response = await self._post_json(f"/calls/{call_control_id}/actions/record_stop", payload)
        if response.get("error"):
            return SendResult(success=False, error=response["error"], raw_response=response, retryable=response.get("retryable", False))
        return SendResult(success=True, message_id=call_control_id, raw_response=response)

    async def transfer_call(self, call_control_id: str, to: str) -> SendResult:
        """Transfer a call to another destination."""
        payload = {
            "command_id": f"hermes-transfer-{uuid.uuid4()}",
            "to": to,
        }
        response = await self._post_json(f"/calls/{call_control_id}/actions/transfer", payload)
        if response.get("error"):
            return SendResult(success=False, error=response["error"], raw_response=response, retryable=response.get("retryable", False))
        return SendResult(success=True, message_id=call_control_id, raw_response=response)

    async def _answer(self, call_control_id: str) -> SendResult:
        response = await self._post_json(
            f"/calls/{call_control_id}/actions/answer",
            {"command_id": f"hermes-answer-{call_control_id}"},
        )
        if response.get("error"):
            return SendResult(success=False, error=response["error"], raw_response=response, retryable=response.get("retryable", False))
        return SendResult(success=True, message_id=call_control_id, raw_response=response)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import aiohttp

        if not self._http_session:
            return {"error": "HTTP session not initialised — adapter not connected", "retryable": True}
        session = self._http_session
        url = f"{self._api_base}{path}"
        try:
            async with session.post(url, json=payload, headers={"Authorization": f"Bearer {self._api_key}"}) as resp:
                try:
                    body = await resp.json()
                except Exception:
                    body = {"text": await resp.text()}
                if 200 <= resp.status < 300:
                    return body if isinstance(body, dict) else {"data": body}
                message = "Telnyx API request failed"
                if isinstance(body, dict):
                    errors = body.get("errors")
                    if isinstance(errors, list) and errors:
                        message = str(errors[0].get("detail") or errors[0].get("title") or message)
                    else:
                        message = str(body.get("error") or body.get("message") or message)
                return {"error": f"{message} (HTTP {resp.status})", "status": resp.status, "body": body, "retryable": resp.status >= 500}
        except aiohttp.ClientError as exc:
            return {"error": str(exc), "retryable": True}

    async def _handle_webhook(self, request):
        from aiohttp import web

        body = await request.read()
        if not self._validate_telnyx_signature(body, request.headers):
            return web.json_response({"ok": False, "error": "invalid signature"}, status=401)

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)

        data = payload.get("data") if isinstance(payload, dict) else None
        event_type = str((data or {}).get("event_type") or "")
        event_payload = (data or {}).get("payload") or {}
        call_control_id = str(event_payload.get("call_control_id") or event_payload.get("id") or "")
        if not event_type or not call_control_id:
            return web.json_response({"ok": True, "ignored": True})

        session = self._record_call_session(event_payload, call_control_id, event_type)

        if event_type == "call.initiated" and session.direction == "inbound":
            # Answer + greet in the background so the webhook ACK returns
            # immediately.  Telnyx retries webhooks that don't respond fast
            # enough, which would cause double-answer.
            async def _handle_initiated():
                await self._answer(call_control_id)
                if self._greeting:
                    await self._speak(call_control_id, self._greeting)
                await self._emit_call_event(payload, session, call_control_id, "Incoming Telnyx voice call")

            task = asyncio.ensure_future(_handle_initiated())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        elif event_type == "call.answered":
            session.state = "answered"
            # Process answered in the background too (speak + emit can be
            # slow) and ack the webhook immediately.
            async def _handle_answered():
                queued_text = self._pending_speak.pop(call_control_id, None)
                if queued_text:
                    logger.info("[telnyx_voice_call] delivering queued speak for %s", call_control_id)
                    await self._speak(call_control_id, queued_text)
                await self._emit_call_event(payload, session, call_control_id, "Telnyx voice call answered")

            task = asyncio.ensure_future(_handle_answered())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        elif event_type == "call.transcription":
            transcript = self._extract_transcript(event_payload)
            if transcript:
                await self._emit_call_event(payload, session, call_control_id, transcript)
        elif event_type == "call.dtmf.received":
            digit = str(event_payload.get("digit") or "").strip()
            if digit:
                await self._emit_call_event(payload, session, call_control_id, f"Caller pressed {digit}")
        elif event_type == "call.hangup":
            session.state = "ended"
            self._pending_speak.pop(call_control_id, None)
            await self._emit_call_event(payload, session, call_control_id, "Call ended")
            self._active_calls.pop(call_control_id, None)
            logger.info("[telnyx_voice_call] call ended for %s", call_control_id)
        elif event_type == "call.conference.created":
            logger.info("[telnyx_voice_call] conference created for %s", call_control_id)
        elif event_type == "call.recording.started":
            logger.info("[telnyx_voice_call] recording started for %s", call_control_id)
        elif event_type == "call.transferred":
            logger.info("[telnyx_voice_call] call transferred for %s", call_control_id)
        elif event_type in {"call.bridged", "call.recording.saved", "call.speak.started", "call.speak.ended"}:
            logger.info("[telnyx_voice_call] received lifecycle event %s for %s", event_type, call_control_id)
        else:
            logger.debug("[telnyx_voice_call] ignored event %s", event_type)

        return web.json_response({"ok": True})

    def _record_call_session(self, event_payload: dict[str, Any], call_control_id: str, event_type: str) -> CallSession:
        direction_raw = str(event_payload.get("direction") or "").lower()
        if direction_raw in {"incoming", "inbound"}:
            direction = "inbound"
        elif direction_raw in {"outgoing", "outbound"}:
            direction = "outbound"
        else:
            direction = ""
        from_number = _first_phone(event_payload.get("from") or event_payload.get("from_number") or event_payload.get("ani"))
        to_number = _first_phone(event_payload.get("to") or event_payload.get("to_number") or event_payload.get("dnis"))
        session = self._active_calls.get(call_control_id) or CallSession(
            call_control_id=call_control_id,
            started_at=time.time(),
        )
        session.call_session_id = str(event_payload.get("call_session_id") or session.call_session_id or "")
        session.client_state = _decode_client_state(str(event_payload.get("client_state") or "")) or session.client_state
        session.caller_number = from_number or session.caller_number
        session.dialed_number = to_number or session.dialed_number
        session.direction = direction or session.direction or "inbound"
        if event_type == "call.bridged":
            session.state = "active"
        elif event_type == "call.ringing":
            session.state = "ringing"
        elif event_type == "call.initiated":
            session.state = "initiated"
        self._active_calls[call_control_id] = session
        return session

    def _extract_transcript(self, event_payload: dict[str, Any]) -> str:
        data = event_payload.get("transcription_data")
        if isinstance(data, dict):
            # Only surface final transcripts to avoid firing on every
            # interim partial result during real-time transcription.
            is_final = data.get("is_final")
            if is_final is False:
                return ""
            transcript = data.get("transcript")
            if transcript:
                return str(transcript).strip()
        return str(event_payload.get("transcription") or "").strip()

    async def _emit_call_event(self, raw: dict[str, Any], session: CallSession, call_control_id: str, prefix: str) -> None:
        caller = session.caller_number or "unknown caller"
        dialed = session.dialed_number or self._from_number or "unknown destination"
        text = f"{prefix} from {caller} to {dialed}. Reply with the message to speak to the caller."
        source = SessionSource(
            platform=Platform("telnyx_voice_call"),
            chat_id=_call_control_chat_id(call_control_id),
            chat_name=f"Telnyx voice call {redact_phone(caller)}",
            chat_type="dm",
            user_id=session.caller_number or call_control_id,
            user_name=caller,
            message_id=call_control_id,
        )
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=raw,
            message_id=call_control_id,
        )
        task = asyncio.ensure_future(self._safe_handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _safe_handle_message(self, event: MessageEvent) -> None:
        try:
            await self.handle_message(event)
        except Exception:
            logger.exception("[telnyx_voice_call] unhandled error in handle_message for %s", event.message_id)

    def _mark_replay(self, replay_key: str) -> bool:
        now = time.time()
        expired = [key for key, expires_at in self._replay_cache.items() if expires_at <= now]
        for key in expired:
            self._replay_cache.pop(key, None)
        is_replay = replay_key in self._replay_cache
        self._replay_cache[replay_key] = now + REPLAY_WINDOW_SECONDS
        while len(self._replay_cache) > REPLAY_CACHE_MAX_ENTRIES:
            oldest = next(iter(self._replay_cache))
            self._replay_cache.pop(oldest, None)
        return is_replay

    def _verify_ed25519(self, public_key: str, signed_payload: bytes, signature: bytes) -> bool:
        public_key = public_key.strip()
        try:
            if public_key.startswith("-----BEGIN"):
                from cryptography.hazmat.primitives import serialization
                from cryptography.exceptions import InvalidSignature

                key = serialization.load_pem_public_key(public_key.encode("utf-8"))
                try:
                    key.verify(signature, signed_payload)
                    return True
                except InvalidSignature:
                    return False

            key_bytes = _decode_base64_or_urlsafe(public_key)
            if len(key_bytes) == 32:
                from nacl.signing import VerifyKey

                VerifyKey(key_bytes).verify(signed_payload, signature)
                return True

            from cryptography.hazmat.primitives import serialization
            from cryptography.exceptions import InvalidSignature

            key = serialization.load_der_public_key(key_bytes)
            try:
                key.verify(signature, signed_payload)
                return True
            except InvalidSignature:
                return False
        except Exception:
            return False

    def _validate_telnyx_signature(self, body: bytes, headers: Dict[str, str]) -> bool:
        if not (self._public_key or self._require_signature):
            return True

        signature_raw = headers.get("Telnyx-Signature-Ed25519") or headers.get("telnyx-signature-ed25519")
        timestamp = headers.get("Telnyx-Timestamp") or headers.get("telnyx-timestamp")
        if not signature_raw or not timestamp or not self._public_key:
            return False

        try:
            tolerance = self._signature_tolerance
        except AttributeError:
            tolerance = 300
        if tolerance > 0:
            try:
                if abs(time.time() - int(timestamp)) > tolerance:
                    return False
            except ValueError:
                return False

        try:
            signature = _decode_base64_or_urlsafe(signature_raw)
        except Exception:
            return False
        signed_payload = f"{timestamp}|".encode() + body
        if not self._verify_ed25519(self._public_key, signed_payload, signature):
            return False

        replay_key = "telnyx:" + _sha256_hex(timestamp.encode() + b"\n" + signature + b"\n" + body)
        return not self._mark_replay(replay_key)


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id=None,
    media_files=None,
    force_document: bool = False,
) -> dict:
    """Out-of-process cron delivery support."""
    adapter = TelnyxVoiceCallAdapter(pconfig)
    result = await adapter.send(chat_id, message)
    if result.success:
        return {"success": True, "message_id": result.message_id}
    return {"error": result.error or "Telnyx Voice Call send failed"}


def register(ctx) -> None:
    """Plugin entry point: called by the Hermes plugin system."""
    ctx.register_platform(
        name="telnyx_voice_call",
        label="Telnyx Voice Call",
        adapter_factory=lambda cfg: TelnyxVoiceCallAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["TELNYX_API_KEY", "TELNYX_VOICE_FROM_NUMBER", "TELNYX_CALL_CONTROL_CONNECTION_ID"],
        install_hint="pip install aiohttp pynacl (PyNaCl only required for signature validation)",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="TELNYX_VOICE_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="TELNYX_VOICE_ALLOWED_USERS",
        allow_all_env="TELNYX_VOICE_ALLOW_ALL_USERS",
        max_message_length=MAX_SPEAK_LENGTH,
        pii_safe=True,
        emoji="☎️",
        allow_update_command=True,
        platform_hint=(
            "You are speaking with a user over a Telnyx voice call. Replies are converted "
            "to speech with Telnyx Call Control. Keep responses concise, conversational, "
            "and easy to understand when heard aloud."
        ),
    )
