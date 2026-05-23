from __future__ import annotations

import os

import pytest

import adapter
from gateway.config import PlatformConfig


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_outbound_call_smoke(monkeypatch):
    """Optional live call test, never runs unless explicitly enabled.

    Required env:
    - TELNYX_VOICE_LIVE_TEST=1
    - TELNYX_API_KEY
    - TELNYX_VOICE_FROM_NUMBER
    - TELNYX_CALL_CONTROL_CONNECTION_ID
    - TELNYX_VOICE_LIVE_TO_NUMBER
    """
    if os.getenv("TELNYX_VOICE_LIVE_TEST") != "1":
        pytest.skip("set TELNYX_VOICE_LIVE_TEST=1 to run live Telnyx voice test")

    to_number = os.getenv("TELNYX_VOICE_LIVE_TO_NUMBER", "").strip()
    if not to_number:
        pytest.skip("TELNYX_VOICE_LIVE_TO_NUMBER is required for live call test")

    voice = adapter.TelnyxVoiceCallAdapter(PlatformConfig(enabled=True, extra={}))
    result = await voice.send(to_number, "Hermes Telnyx voice-call live smoke test.")
    assert result.success is True
    assert result.message_id
