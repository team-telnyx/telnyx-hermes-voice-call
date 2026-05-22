from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapter.py"
MANIFEST = ROOT / "plugin.yaml"
ENV_EXAMPLE = ROOT / ".env.example"


def _module_ast() -> ast.Module:
    return ast.parse(ADAPTER.read_text(encoding="utf-8"))


def _assigned_constant(name: str):
    for node in _module_ast().body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"missing assignment for {name}")


def test_manifest_declares_platform_plugin():
    text = MANIFEST.read_text(encoding="utf-8")
    assert "kind: platform" in text
    assert "name: telnyx-voice-call-platform" in text
    assert "TELNYX_API_KEY" in text
    assert "TELNYX_CALL_CONTROL_CONNECTION_ID" in text


def test_env_example_contains_required_values():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "TELNYX_API_KEY=" in text
    assert "TELNYX_VOICE_FROM_NUMBER=" in text
    assert "TELNYX_CALL_CONTROL_CONNECTION_ID=" in text
    assert "TELNYX_VOICE_WEBHOOK_PORT=8088" in text


def test_constants_are_expected():
    assert _assigned_constant("TELNYX_API_BASE") == "https://api.telnyx.com/v2"
    assert _assigned_constant("DEFAULT_WEBHOOK_PORT") == 8088
    assert _assigned_constant("DEFAULT_WEBHOOK_PATH") == "/webhooks/telnyx/voice"
    assert _assigned_constant("MAX_SPEAK_LENGTH") == 5000


def test_register_shape_matches_hermes_platform_contract():
    source = ADAPTER.read_text(encoding="utf-8")
    assert 'name="telnyx_voice_call"' in source
    assert 'label="Telnyx Voice Call"' in source
    assert "adapter_factory=lambda cfg: TelnyxVoiceCallAdapter(cfg)" in source
    assert "standalone_sender_fn=_standalone_send" in source
    assert "allowed_users_env=\"TELNYX_VOICE_ALLOWED_USERS\"" in source
    assert "allow_all_env=\"TELNYX_VOICE_ALLOW_ALL_USERS\"" in source
    assert "max_message_length=MAX_SPEAK_LENGTH" in source
