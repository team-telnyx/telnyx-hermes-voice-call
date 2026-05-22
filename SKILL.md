---
name: telnyx-hermes-voice-call
description: Telnyx Voice Call platform adapter for Hermes Agent using Telnyx Call Control.
author: Telnyx AI FDE
version: 0.1.0
---

# Telnyx Hermes Voice Call

Use this repository when adding or validating Telnyx Voice Call support for
Hermes Agent.

## What this contributes

- Hermes platform plugin: `telnyx_voice_call`
- Telnyx Call Control webhook server
- Inbound call answer + optional greeting
- Hermes reply delivery via Call Control `speak`
- Outbound E.164 call creation
- Telnyx webhook signature verification

## Local validation

```bash
uv run --extra test python -m pytest tests/test_telnyx_voice_static.py tests/test_telnyx_voice_runtime.py tests/test_telnyx_voice_plugin_loading.py -q
```

If validating against a local Hermes checkout with its own venv:

```bash
~/.hermes/hermes-agent/.venv/bin/python -m pytest tests/test_telnyx_voice_static.py tests/test_telnyx_voice_runtime.py tests/test_telnyx_voice_plugin_loading.py -q
```

## Live test

Only run intentionally; it places a real outbound call.

```bash
export TELNYX_VOICE_LIVE_TEST=1
export TELNYX_API_KEY=***
export TELNYX_VOICE_FROM_NUMBER=+15550000001
export TELNYX_CALL_CONTROL_CONNECTION_ID=1234567890
export TELNYX_VOICE_LIVE_TO_NUMBER=+15550000002
python -m pytest tests/test_telnyx_voice_live.py -q
```

## PR rules

- Never push implementation directly to `main`.
- Use feature branch + PR.
- Keep Hermes voice-call separate from `/ai` README changes.
- Do not include secrets in tests, docs, logs, or commits.
