from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapter.py"
PROVISIONING = ROOT / "provisioning.py"
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


def test_plugin_manifest_is_platform():
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["name"] == "telnyx-voice-call-platform"
    assert manifest["label"] == "Telnyx Voice Call"
    assert manifest["kind"] == "platform"
    required = {item["name"] for item in manifest["requires_env"]}
    assert {"TELNYX_API_KEY", "TELNYX_VOICE_FROM_NUMBER", "TELNYX_CALL_CONTROL_CONNECTION_ID"} <= required
    optional = {item["name"] for item in manifest["optional_env"]}
    assert "TELNYX_VOICE_ALLOWED_USERS" in optional
    assert "TELNYX_VOICE_HOME_CHANNEL" in optional
    assert "TELNYX_VOICE_AUTO_PROVISION" in optional


def test_register_platform_shape():
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    register_calls = [
        call for call in calls
        if isinstance(call.func, ast.Attribute) and call.func.attr == "register_platform"
    ]
    assert register_calls, "adapter must call ctx.register_platform(...)"
    keywords = {kw.arg: kw.value for kw in register_calls[0].keywords}
    assert keywords["name"].value == "telnyx_voice_call"
    assert keywords["label"].value == "Telnyx Voice Call"
    assert keywords["allowed_users_env"].value == "TELNYX_VOICE_ALLOWED_USERS"
    assert keywords["allow_all_env"].value == "TELNYX_VOICE_ALLOW_ALL_USERS"
    assert keywords["cron_deliver_env_var"].value == "TELNYX_VOICE_HOME_CHANNEL"
    assert keywords["pii_safe"].value is True


def test_api_constants():
    import adapter

    assert adapter.TELNYX_API_BASE == "https://api.telnyx.com/v2"
    assert adapter.DEFAULT_WEBHOOK_PORT == 8088
    assert adapter.DEFAULT_WEBHOOK_PATH == "/webhooks/telnyx/voice"
    assert adapter.MAX_SPEAK_LENGTH == 5000


def test_plugin_package_entrypoint_exists():
    init_file = ROOT / "__init__.py"
    assert init_file.exists(), "Hermes directory plugins require __init__.py"
    text = init_file.read_text()
    assert "register" in text


def test_provisioning_module_exists():
    assert PROVISIONING.exists(), "provisioning.py must exist for auto-provisioning"
    text = PROVISIONING.read_text()
    assert "async def provision" in text
    assert "async def deprovision" in text
    assert "load_provisioned_state" in text
    assert "save_provisioned_state" in text


def test_manifest_documents_code_supported_env_vars():
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    env_names = {item["name"] for block in ("requires_env", "optional_env") for item in manifest.get(block, [])}
    assert "TELNYX_VOICE_API_BASE" in env_names
    assert "TELNYX_VOICE_SIGNATURE_TOLERANCE" in env_names
    assert "TELNYX_VOICE_AUTO_PROVISION" in env_names


def test_env_example_includes_live_test_guard():
    env_example = ENV_EXAMPLE.read_text()
    assert "TELNYX_VOICE_LIVE_TEST=0" in env_example
    assert "TELNYX_VOICE_LIVE_TO_NUMBER=" in env_example
    assert "TELNYX_VOICE_AUTO_PROVISION" in env_example


def test_env_example_includes_portal_links():
    env_example = ENV_EXAMPLE.read_text()
    assert "portal.telnyx.com" in env_example


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
    assert 'allowed_users_env="TELNYX_VOICE_ALLOWED_USERS"' in source
    assert 'allow_all_env="TELNYX_VOICE_ALLOW_ALL_USERS"' in source
    assert "max_message_length=MAX_SPEAK_LENGTH" in source


def test_adapter_has_call_control_actions():
    """Verify the adapter exposes conference, recording, transfer, and hangup."""
    source = ADAPTER.read_text(encoding="utf-8")
    assert "async def hangup(" in source
    assert "async def create_conference(" in source
    assert "async def start_recording(" in source
    assert "async def stop_recording(" in source
    assert "async def transfer_call(" in source
