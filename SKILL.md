---
name: telnyx-hermes-voice-call
description: Telnyx Voice Call platform adapter for Hermes Agent using Telnyx Call Control.
author: Telnyx AI FDE
version: 0.1.0
tags: [hermes, telnyx, voice, call-control, platform]
---

# Telnyx Hermes Voice Call

Use Telnyx Call Control as a first-class Hermes platform adapter for voice calls.

## Install into Hermes

```bash
mkdir -p ~/.hermes/plugins/telnyx_voice_call
cp __init__.py adapter.py plugin.yaml ~/.hermes/plugins/telnyx_voice_call/
hermes plugins list
hermes plugins enable telnyx-voice-call-platform
```

Restart the Hermes gateway after enabling the plugin.

## Configuration

Required:

```bash
export TELNYX_API_KEY="KEY..."
export TELNYX_VOICE_FROM_NUMBER="+15551234567"
export TELNYX_CALL_CONTROL_CONNECTION_ID="1234567890"
```

Recommended production hardening:

```bash
export TELNYX_PUBLIC_KEY="<Telnyx webhook signing public key>"
export TELNYX_VOICE_REQUIRE_SIGNATURE=true
export TELNYX_VOICE_ALLOWED_USERS="+15551230001,+15551230002"
export TELNYX_VOICE_ALLOW_ALL_USERS=false
```

Optional:

```bash
export TELNYX_VOICE_WEBHOOK_URL="https://example.ngrok.app/webhooks/telnyx/voice"
export TELNYX_VOICE_API_BASE="https://api.telnyx.com/v2"
export TELNYX_VOICE_WEBHOOK_HOST="0.0.0.0"
export TELNYX_VOICE_WEBHOOK_PORT=8088
export TELNYX_VOICE_WEBHOOK_PATH="/webhooks/telnyx/voice"
export TELNYX_VOICE_HOME_CHANNEL="+15551230001"
export TELNYX_VOICE_SIGNATURE_TOLERANCE=300
export TELNYX_VOICE_GREETING="Hello, you are connected to Hermes."
export TELNYX_VOICE_DEFAULT_VOICE="Telnyx.NaturalHD.astra"
export TELNYX_VOICE_LANGUAGE="en-US"
```

## Hermes config

```yaml
gateway:
  platforms:
    telnyx_voice_call:
      enabled: true
```

Or rely on env-driven enablement when `TELNYX_API_KEY`,
`TELNYX_VOICE_FROM_NUMBER`, and `TELNYX_CALL_CONTROL_CONNECTION_ID` are set and
the plugin is enabled.

## Webhook

Configure the Telnyx Call Control application inbound webhook to:

```text
https://your-public-host.example/webhooks/telnyx/voice
```

The adapter accepts inbound call events (`call.initiated`, `call.answered`,
`call.transcription`, `call.dtmf.received`, `call.hangup`) and ignores
lifecycle events.

## Tests

```bash
uv run --extra test python -m pytest tests/test_telnyx_voice_static.py tests/test_telnyx_voice_runtime.py tests/test_telnyx_voice_plugin_loading.py -q
```

Live outbound call test requires an explicit safety flag and makes a real call:

```bash
export TELNYX_VOICE_LIVE_TEST=1
export TELNYX_API_KEY="KEY..."
export TELNYX_VOICE_FROM_NUMBER="+15551234567"
export TELNYX_CALL_CONTROL_CONNECTION_ID="1234567890"
export TELNYX_VOICE_LIVE_TO_NUMBER="+15557654321"
uv run --extra test python -m pytest tests/test_telnyx_voice_live.py -q -m live
```

## PR rules

- Never push implementation directly to `main`.
- Use feature branch + PR.
- Keep Hermes voice-call separate from `/ai` README changes.
- Do not include secrets in tests, docs, logs, or commits.
