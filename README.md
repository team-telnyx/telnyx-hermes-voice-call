# Telnyx Voice Call — Hermes Agent Contribution

This repository contains the Telnyx Voice Call platform adapter for
[Hermes Agent](https://github.com/NousResearch/hermes-agent).

It is a **plugin-first platform adapter**. Hermes receives Telnyx Call Control
webhooks as platform messages, and Hermes replies are spoken back into the call
with Telnyx Call Control `speak` actions.

The adapter follows the existing Hermes platform-plugin pattern used by the
Telnyx SMS adapter, while aligning Call Control request and webhook behavior
with the finalized OpenClaw voice-call plugin.

## What's inside

| File | Purpose |
|------|---------|
| `__init__.py` | Hermes directory-plugin entry point that exposes `register(ctx)` |
| `adapter.py` | Hermes platform adapter implementation |
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
- Uses `client_state`, `webhook_url_method: POST`, `timeout_secs`, and
  `command_id` fields consistent with the OpenClaw voice-call implementation
- Handles `call.initiated`, `call.answered`, `call.transcription`,
  `call.dtmf.received`, and `call.hangup` events
- Supports Telnyx Ed25519 webhook signature verification with replay protection

## Fresh clone setup

Requirements:

- Python 3.10+; Python 3.12 is recommended because local Hermes checkouts may
  use modern typing syntax.
- A local Hermes Agent checkout for tests that import `gateway.*` modules.
  Set `HERMES_AGENT_ROOT` if it is not at `~/.hermes/hermes-agent`.
- `uv` is recommended for reproducible local test dependencies.

```bash
git clone https://github.com/team-telnyx/telnyx-hermes-voice-call.git
cd telnyx-hermes-voice-call
export HERMES_AGENT_ROOT="$HOME/.hermes/hermes-agent"  # adjust if needed
uv run --extra test python -m pytest tests/test_telnyx_voice_static.py tests/test_telnyx_voice_runtime.py tests/test_telnyx_voice_plugin_loading.py -q
```

Without `uv`, use any Python 3.10+ virtualenv:

```bash
python3.12 -m venv .venv
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
cp __init__.py adapter.py plugin.yaml ~/.hermes/plugins/telnyx_voice_call/
```

Expected plugin tree:

```text
~/.hermes/plugins/telnyx_voice_call/
  plugin.yaml
  __init__.py
  adapter.py
```

Enable the plugin in Hermes. Depending on the Hermes CLI version, use the
plugin manifest name/key shown by `hermes plugins list`; for this plugin that is
usually `telnyx-voice-call-platform` or the directory key `telnyx_voice_call`:

```bash
hermes plugins list
hermes plugins enable telnyx-voice-call-platform  # or: hermes plugins enable telnyx_voice_call
```

Then configure credentials and enable the platform:

```bash
# Append Telnyx vars to your existing Hermes .env (do NOT overwrite):
cat .env.example >> ~/.hermes/.env
# Then edit ~/.hermes/.env — remove duplicates, fill in your Telnyx values.
```

```yaml
# ~/.hermes/config.yaml
gateway:
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
```

No core Hermes code changes are required. Hermes' platform registry handles:

- adapter creation via `ctx.register_platform(...)`
- dynamic `Platform("telnyx_voice_call")` enum support
- env-driven enablement
- allowed-user / allow-all auth checks
- cron/home-channel delivery
- standalone out-of-process sends
- `hermes status` / setup UI display
- platform prompt hints

## Required Telnyx setup

1. Create or choose a Telnyx Call Control application.
2. Point its webhook URL at this adapter's public webhook URL, for example:
   `https://example.ngrok.app/webhooks/telnyx/voice`.
3. Assign a Telnyx number to the Call Control application for inbound calls.
4. Configure the required env vars below.

## Environment variables

### Required

| Variable | Description |
|----------|-------------|
| `TELNYX_API_KEY` | Telnyx API key used for Call Control API requests |
| `TELNYX_VOICE_FROM_NUMBER` | Telnyx-owned E.164 caller ID used for outbound calls |
| `TELNYX_CALL_CONTROL_CONNECTION_ID` | Telnyx Call Control application connection ID |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `TELNYX_VOICE_WEBHOOK_URL` | unset | Public webhook URL sent when creating outbound calls |
| `TELNYX_VOICE_API_BASE` | `https://api.telnyx.com/v2` | Telnyx API base URL override |
| `TELNYX_VOICE_WEBHOOK_HOST` | `127.0.0.1` | Webhook bind host |
| `TELNYX_VOICE_WEBHOOK_PORT` | `8088` | Webhook listen port |
| `TELNYX_VOICE_WEBHOOK_PATH` | `/webhooks/telnyx/voice` | Webhook path |
| `TELNYX_PUBLIC_KEY` | unset | Telnyx account public key for webhook signature verification |
| `TELNYX_VOICE_REQUIRE_SIGNATURE` | `false` | Require valid Telnyx webhook signatures |
| `TELNYX_VOICE_SIGNATURE_TOLERANCE` | `300` | Signature timestamp tolerance in seconds; `0` disables freshness check |
| `TELNYX_VOICE_ALLOWED_USERS` | unset | Comma-separated E.164 numbers allowed to call the bot |
| `TELNYX_VOICE_ALLOW_ALL_USERS` | `false` | Allow any caller phone number to talk to the bot; development only |
| `TELNYX_VOICE_HOME_CHANNEL` | unset | Default E.164 phone number for cron / notification delivery |
| `TELNYX_VOICE_GREETING` | unset | Optional greeting spoken after inbound calls are answered |
| `TELNYX_VOICE_DEFAULT_VOICE` | `Telnyx.NaturalHD.astra` | Telnyx TTS voice for Call Control speak actions |
| `TELNYX_VOICE_LANGUAGE` | `en-US` | Language for Call Control speak actions |

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
python -m pytest tests/test_telnyx_voice_static.py tests/test_telnyx_voice_runtime.py tests/test_telnyx_voice_plugin_loading.py -q

# Live test (requires Telnyx credentials and makes a real outbound call)
export TELNYX_VOICE_LIVE_TEST=1
export TELNYX_API_KEY=***
export TELNYX_VOICE_FROM_NUMBER=+15550000001
export TELNYX_CALL_CONTROL_CONNECTION_ID=1234567890
export TELNYX_VOICE_LIVE_TO_NUMBER=+15550000002
python -m pytest tests/test_telnyx_voice_live.py -q
```

## References

- [Telnyx Call Control API](https://developers.telnyx.com/docs/api/v2/call-control)
- [Telnyx webhook signing](https://developers.telnyx.com/docs/v2/development/webhooks/receiving-webhooks)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
