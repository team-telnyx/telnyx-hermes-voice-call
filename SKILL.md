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
cp __init__.py adapter.py provisioning.py plugin.yaml ~/.hermes/plugins/telnyx_voice_call/
hermes plugins list
hermes plugins enable telnyx-voice-call-platform
```

Restart the Hermes gateway after enabling the plugin.

## Configuration

### Auto-provision mode (recommended)

Set only the API key and enable auto-provisioning:

```bash
export TELNYX_API_KEY="KEY..."
export TELNYX_VOICE_AUTO_PROVISION=true
```

The adapter will automatically create a Call Control application and order a
phone number on connect. On disconnect, auto-provisioned resources are cleaned
up.

### Manual mode

Required:

```bash
export TELNYX_API_KEY="KEY..."
export TELNYX_VOICE_FROM_NUMBER="+15551234567"
export TELNYX_CALL_CONTROL_CONNECTION_ID="1234567890"
```

Find your connection ID at [Mission Control → Call Control](https://portal.telnyx.com/#/app/call-control/applications).

Recommended production hardening:

```bash
export TELNYX_PUBLIC_KEY="<Telnyx webhook signing public key>"
export TELNYX_VOICE_REQUIRE_SIGNATURE=true
export TELNYX_VOICE_ALLOWED_USERS="+15551230001,+15551230002"
export TELNYX_VOICE_ALLOW_ALL_USERS=false
```

Find your public key at [Mission Control → Public Key](https://portal.telnyx.com/#/app/account/public-key).

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
the plugin is enabled. With `TELNYX_VOICE_AUTO_PROVISION=true`, only the API
key is required.

## Call control actions

The adapter exposes these Call Control methods:

| Method | Telnyx API | Description |
|--------|-----------|-------------|
| `hangup(call_control_id)` | `/calls/{id}/actions/hangup` | Hang up an active call |
| `create_conference(call_control_id, name)` | `/calls/{id}/actions/conference` | Add a call leg to a conference bridge |
| `start_recording(call_control_id)` | `/calls/{id}/actions/record_start` | Start recording a call |
| `stop_recording(call_control_id)` | `/calls/{id}/actions/record_stop` | Stop recording a call |
| `transfer_call(call_control_id, to)` | `/calls/{id}/actions/transfer` | Transfer a call to another destination |

## Webhook

Configure the Telnyx Call Control application inbound webhook to:

```text
https://your-public-host.example/webhooks/telnyx/voice
```

The adapter handles these events:
- `call.initiated` — answers inbound calls, speaks greeting
- `call.answered` — drains queued speak for outbound calls
- `call.transcription` — forwards transcript to Hermes
- `call.dtmf.received` — forwards digit presses to Hermes
- `call.conference.created` — logs conference creation
- `call.recording.started` — logs recording start
- `call.transferred` — logs call transfer
- `call.hangup` — cleans up call state and pending speak
- `call.bridged`, `call.recording.saved`, `call.speak.started`, `call.speak.ended` — passive lifecycle events

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
