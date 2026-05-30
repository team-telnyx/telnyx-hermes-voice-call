# Telnyx Voice Call — Hermes Agent Contribution

This repository contains the Telnyx Voice Call platform adapter for
[Hermes Agent](https://github.com/NousResearch/hermes-agent).

It is a **plugin-first platform adapter** that exposes Telnyx Call Control as a
Hermes platform channel named `telnyx_voice_call`. Hermes receives Telnyx Call
Control webhooks as platform messages, and Hermes replies are spoken back into
the call with Telnyx Call Control `speak` actions.

The adapter follows the existing Hermes platform-plugin pattern used by the
Telnyx SMS adapter (`telnyx-hermes-sms`), while aligning Call Control request
and webhook behavior with the finalized OpenClaw voice-call plugin.

## What's inside

| File | Purpose |
|------|---------|
| `__init__.py` | Hermes directory-plugin entry point that exposes `register(ctx)` |
| `adapter.py` | Hermes platform adapter implementation |
| `provisioning.py` | Auto-provisioning: creates CC app + orders phone number |
| `plugin.yaml` | Platform plugin metadata and setup env var definitions |
| `.env.example` | Copyable environment variable template |
| `tests/test_telnyx_voice_static.py` | Manifest/static/API shape checks |
| `tests/test_telnyx_voice_runtime.py` | Mocked Call Control send/webhook runtime tests |
| `tests/test_telnyx_voice_plugin_loading.py` | Directory plugin loading test |
| `tests/test_telnyx_voice_live.py` | Optional live outbound call test, gated by `TELNYX_VOICE_LIVE_TEST=1` |

## Capabilities

- Registers Hermes platform `telnyx_voice_call`
- Starts an `aiohttp` webhook server for Telnyx Call Control events
- Answers inbound calls and optionally speaks a greeting
- Sends Hermes replies into active calls via `speak`
- Creates outbound calls for E.164 targets
- Handles `call.initiated`, `call.answered`, `call.transcription`,
  `call.dtmf.received`, `call.conference.created`, `call.recording.started`,
  `call.transferred`, `call.hangup`, `streaming.started`, `streaming.stopped`
  events
- Supports Telnyx Ed25519 webhook signature verification with replay protection
- **Call control actions:** hangup, conference, recording (start/stop), transfer,
  streaming (start/stop)
- **Media streaming:** real-time bidirectional audio over WebSockets via Telnyx
  Call Control `streaming_start`/`streaming_stop`, with a built-in WebSocket
  endpoint for receiving media frames
- **Auto-provisioning:** optionally creates a Call Control app + orders a phone
  number when the adapter connects, so the agent becomes a phone number on enable
- Env-driven enablement, cron/home-channel delivery, standalone sender support,
  allowlist controls, PII-safe display, and voice-call-specific prompt hints

## Fresh clone setup

Requirements:

- Python 3.10+
- A local Hermes Agent checkout for tests that import `gateway.*` modules.
  Set `HERMES_AGENT_ROOT` if it is not at `~/.hermes/hermes-agent`.

```bash
git clone https://github.com/team-telnyx/telnyx-hermes-voice-call.git
cd telnyx-hermes-voice-call
export HERMES_AGENT_ROOT="$HOME/.hermes/hermes-agent"  # adjust if needed
uv run --extra test python -m pytest tests/test_telnyx_voice_static.py tests/test_telnyx_voice_runtime.py tests/test_telnyx_voice_plugin_loading.py -q
```

Without `uv`, use any Python 3.10+ virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
export HERMES_AGENT_ROOT="$HOME/.hermes/hermes-agent"
python -m pytest tests/test_telnyx_voice_static.py tests/test_telnyx_voice_runtime.py tests/test_telnyx_voice_plugin_loading.py -q
```

## Integration into hermes-agent

### User plugin install

Copy this repository's plugin files into a Hermes plugin directory:

```bash
mkdir -p ~/.hermes/plugins/telnyx_voice_call
cp __init__.py adapter.py provisioning.py plugin.yaml ~/.hermes/plugins/telnyx_voice_call/
```

Expected plugin tree:

```text
~/.hermes/plugins/telnyx_voice_call/
  plugin.yaml
  __init__.py
  adapter.py
  provisioning.py
```

Then enable the plugin and configure credentials:

```bash
hermes plugins list
hermes plugins enable telnyx-voice-call-platform
```

```bash
# Append Telnyx vars to your existing Hermes .env (do NOT overwrite):
cat .env.example >> ~/.hermes/.env
# Then edit ~/.hermes/.env — remove duplicates, fill in your Telnyx values.
```

```yaml
# ~/.hermes/config.yaml
platforms:
  telnyx_voice_call:
    enabled: true
```

Restart the Hermes gateway after installing/enabling the plugin so the platform
registry can discover `telnyx_voice_call`.

### Bundled upstream plugin path

For an upstream Hermes contribution, copy the plugin under:

```text
plugins/platforms/telnyx_voice_call/
  plugin.yaml
  __init__.py
  adapter.py
  provisioning.py
```

Hermes' platform registry handles adapter creation via
`ctx.register_platform(...)`, dynamic `Platform("telnyx_voice_call")` enum
support, env-driven enablement, allowed-user / allow-all auth checks,
cron/home-channel delivery, standalone out-of-process sends, and platform
prompt hints.

## Auto-provisioning

Set `TELNYX_VOICE_AUTO_PROVISION=true` and provide only `TELNYX_API_KEY`. On
connect, the adapter will:

1. Create a Telnyx Call Control application with the configured webhook URL.
2. Search for an available US voice phone number.
3. Order the number and assign it to the Call Control application.
4. Persist the provisioned state to `provisioned.json` (idempotent on restart).
5. Use the provisioned `connection_id` and `from_number` automatically.

On disconnect, auto-provisioned resources are cleaned up (number order and CC
application deleted).

If `TELNYX_VOICE_FROM_NUMBER` and `TELNYX_CALL_CONTROL_CONNECTION_ID` are
already set, auto-provisioning is skipped regardless of the flag — the adapter
uses the manually configured values.

## Required Telnyx setup (manual mode)

When auto-provisioning is not enabled, you need to set up Telnyx resources
manually:

1. Create or choose a Telnyx Call Control application at
   [Mission Control → Call Control](https://portal.telnyx.com/#/app/call-control/applications).
2. Point its webhook URL at this adapter's public webhook URL, for example:
   `https://example.ngrok.app/webhooks/telnyx/voice`.
3. Assign a Telnyx number to the Call Control application for inbound calls.
4. Configure the required env vars below.

## Call control actions

The adapter exposes call control methods that can be invoked programmatically:

| Method | Telnyx API | Description |
|--------|-----------|-------------|
| `hangup(call_control_id)` | `POST /calls/{id}/actions/hangup` | Hang up an active call |
| `create_conference(call_control_id, name)` | `POST /calls/{id}/actions/conference` | Add a call leg to a conference bridge |
| `start_recording(call_control_id)` | `POST /calls/{id}/actions/record_start` | Start recording a call |
| `stop_recording(call_control_id)` | `POST /calls/{id}/actions/record_stop` | Stop recording a call |
| `transfer_call(call_control_id, to)` | `POST /calls/{id}/actions/transfer` | Transfer a call to another destination |
| `streaming_start(call_control_id, **)` | `POST /calls/{id}/actions/streaming_start` | Start media streaming to a WebSocket |
| `streaming_stop(call_control_id, **)` | `POST /calls/{id}/actions/streaming_stop` | Stop media streaming |

All methods return `SendResult` with `success=True/False` and error details on
failure.

## Media streaming

The adapter supports Telnyx media streaming over WebSockets for real-time
voice AI pipelines. This enables bidirectional audio between the caller and
your ASR/TTS/LLM stack.

### Simple mode (default)

Without streaming configured, Hermes replies are delivered via Call Control
`speak` actions — text-to-speech rendered by Telnyx. This works well for
conversational agents that don't need real-time audio processing.

### Streaming mode

Set `TELNYX_VOICE_STREAM_URL` to enable media streaming. When configured:

1. On `call.answered`, the adapter automatically calls `streaming_start` with
   the configured WebSocket URL, track, and codec.
2. Telnyx opens a WebSocket connection to your server and delivers real-time
   audio frames as base64-encoded RTP payloads wrapped in JSON.
3. The adapter includes a built-in WebSocket endpoint (`/ws/telnyx/voice/stream`)
   that receives and logs Telnyx media frames. In v1, this validates/acks/records
   events conservatively. Future versions will integrate with ASR/TTS pipelines.
4. On `call.hangup`, `streaming_stop` is called automatically and the WebSocket
   connection is cleaned up.

### Streaming events

The adapter handles these streaming-related webhook events:

- `streaming.started` — records the `stream_id` for cleanup tracking
- `streaming.stopped` — cleans up the stream and closes the WebSocket

### WebSocket frame types

The built-in WebSocket handler processes these Telnyx frame types:

| Event | Description |
|-------|-------------|
| `connected` | WebSocket connection established |
| `start` | Stream begins; includes `stream_id`, `call_control_id`, `media_format` |
| `media` | Audio frame (base64 RTP payload). Logged at debug level (high volume) |
| `dtmf` | DTMF digit detected; emitted as Hermes message event |
| `mark` | Mark message (for media playback tracking) |
| `stop` | Stream ended |
| `error` | Stream error |

### Bidirectional streaming

For real-time voice AI, enable bidirectional streaming:

```python
result = await voice.streaming_start(
    call_control_id,
    stream_url="wss://your-server.com/ws",
    stream_track="both_tracks",
    bidirectional_mode="rtp",
    bidirectional_codec="G722",
    bidirectional_sampling_rate=16000,
    bidirectional_target_legs="both",
)
```

Bidirectional streaming lets you send audio back into the call via the
WebSocket, enabling low-latency speech-to-speech AI agent loops.

### Custom WebSocket server

You can point `TELNYX_VOICE_STREAM_URL` at any WebSocket server. The adapter's
built-in `/ws/telnyx/voice/stream` endpoint is available if you want the Hermes
host to receive the frames directly. Both options work — the adapter only needs
to call `streaming_start` with the correct URL.

## Environment variables

### Required

| Variable | Description |
|----------|-------------|
| `TELNYX_API_KEY` | Telnyx API key used for Call Control API requests. Create one at [Mission Control → API Keys](https://portal.telnyx.com/#/app/api-keys). |

### Required unless auto-provisioning is enabled

| Variable | Description |
|----------|-------------|
| `TELNYX_VOICE_FROM_NUMBER` | Telnyx-owned E.164 caller ID used for outbound calls |
| `TELNYX_CALL_CONTROL_CONNECTION_ID` | Telnyx Call Control application connection ID. Find it at [Mission Control → Call Control](https://portal.telnyx.com/#/app/call-control/applications). |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `TELNYX_VOICE_AUTO_PROVISION` | `false` | Auto-provision CC app + phone number on enable |
| `TELNYX_VOICE_WEBHOOK_URL` | unset | Public webhook URL sent when creating outbound calls |
| `TELNYX_VOICE_API_BASE` | `https://api.telnyx.com/v2` | Telnyx API base URL override |
| `TELNYX_VOICE_WEBHOOK_HOST` | `127.0.0.1` | Webhook bind host |
| `TELNYX_VOICE_WEBHOOK_PORT` | `8088` | Webhook listen port |
| `TELNYX_VOICE_WEBHOOK_PATH` | `/webhooks/telnyx/voice` | Webhook path |
| `TELNYX_PUBLIC_KEY` | unset | Telnyx account public key for webhook signature verification. Find it at [Mission Control → Public Key](https://portal.telnyx.com/#/app/account/public-key). |
| `TELNYX_VOICE_REQUIRE_SIGNATURE` | `false` | Require valid Telnyx webhook signatures |
| `TELNYX_VOICE_SIGNATURE_TOLERANCE` | `300` | Signature timestamp tolerance in seconds; `0` disables freshness check |
| `TELNYX_VOICE_ALLOWED_USERS` | unset | Comma-separated E.164 numbers allowed to call the bot |
| `TELNYX_VOICE_ALLOW_ALL_USERS` | `false` | Allow any caller phone number to talk to the bot; development only |
| `TELNYX_VOICE_HOME_CHANNEL` | unset | Default E.164 phone number for cron / notification delivery |
| `TELNYX_VOICE_GREETING` | unset | Optional greeting spoken after inbound calls are answered |
| `TELNYX_VOICE_DEFAULT_VOICE` | `Telnyx.NaturalHD.astra` | Telnyx TTS voice for Call Control speak actions |
| `TELNYX_VOICE_LANGUAGE` | `en-US` | Language for Call Control speak actions |
| `TELNYX_VOICE_STREAM_URL` | unset | WebSocket URL for media streaming (e.g. `wss://example.com/ws`). When set, streaming auto-starts on call.answered. |
| `TELNYX_VOICE_STREAM_TRACK` | `inbound_track` | Audio track to stream: `inbound_track`, `outbound_track`, `both_tracks` |
| `TELNYX_VOICE_STREAM_CODEC` | `PCMU` | Audio codec for streaming: `PCMU`, `PCMA`, `G722`, `OPUS`, `AMR-WB`, `L16`, `default` |
| `TELNYX_VOICE_WS_HOST` | same as webhook host | WebSocket server bind host |
| `TELNYX_VOICE_WS_PORT` | same as webhook port | WebSocket server listen port |
| `TELNYX_VOICE_WS_PATH` | `/ws/telnyx/voice/stream` | WebSocket endpoint path for Telnyx media frames |

## Runtime behavior

### Inbound calls

For inbound `call.initiated` events, the adapter:

1. Verifies the Telnyx webhook signature when configured.
2. Records the call session in memory.
3. Answers the call with Call Control.
4. Speaks `TELNYX_VOICE_GREETING` if configured.
5. Emits a Hermes `MessageEvent` from `call_control:<call_control_id>`.

Hermes replies to that session are sent back to Telnyx via:

```text
POST /v2/calls/{call_control_id}/actions/speak
```

### Outbound calls

Sending to an E.164 chat id creates a call:

```text
POST /v2/calls
```

The request includes:

- `connection_id`
- `to`
- `from`
- `webhook_url` when configured
- `webhook_url_method: POST`
- Base64 `client_state`
- `timeout_secs: 30`

If Telnyx returns a `call_control_id`, the adapter attempts an immediate
`speak`. If the call is not answered yet and Telnyx rejects the speak command,
the call creation still succeeds and answered webhooks can continue the session.

## Webhook security

Production deployments should configure `TELNYX_PUBLIC_KEY` and set:

```bash
TELNYX_VOICE_REQUIRE_SIGNATURE=true
```

The adapter verifies Telnyx's Ed25519 signature over `timestamp|payload`, checks
clock skew, and rejects replayed signed payloads. Raw Base64/Base64URL Ed25519
keys are supported. PEM and DER SPKI keys are also supported when the optional
`cryptography` dependency is installed.

## Running tests

```bash
# No credentials needed
uv run --extra test python -m pytest tests/test_telnyx_voice_static.py tests/test_telnyx_voice_runtime.py tests/test_telnyx_voice_plugin_loading.py -q
```

Live outbound call test requires an explicit safety flag and makes a real call:

```bash
export TELNYX_VOICE_LIVE_TEST=1
export TELNYX_API_KEY=***
export TELNYX_VOICE_FROM_NUMBER=+15550000001
export TELNYX_CALL_CONTROL_CONNECTION_ID=1234567890
export TELNYX_VOICE_LIVE_TO_NUMBER=+15550000002
uv run --extra test python -m pytest tests/test_telnyx_voice_live.py -q -m live
```

## References

- [Telnyx Call Control API](https://developers.telnyx.com/docs/api/v2/call-control)
- [Telnyx Call Control Commands](https://developers.telnyx.com/docs/api/v2/call-control/Call-Commands)
- [Telnyx webhook signing](https://developers.telnyx.com/docs/v2/development/webhooks/receiving-webhooks)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [Telnyx SMS adapter for Hermes](https://github.com/team-telnyx/telnyx-hermes-sms) (sister platform adapter)
