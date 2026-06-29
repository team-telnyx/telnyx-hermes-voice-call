from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_directory_plugin_entrypoint_registers_platform():
    """Mirror Hermes loading __init__.py from a directory plugin."""
    root = Path(__file__).resolve().parents[1]
    module_name = "telnyx_voice_call_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)

    calls = []

    class Ctx:
        def register_platform(self, **kwargs):
            calls.append(kwargs)

    module.register(Ctx())
    assert calls
    registered = calls[0]
    assert registered["name"] == "telnyx_voice_call"
    assert registered["label"] == "Telnyx Voice Call"
    assert registered["required_env"] == [
        "TELNYX_API_KEY",
        "TELNYX_VOICE_FROM_NUMBER",
        "TELNYX_CALL_CONTROL_CONNECTION_ID",
    ]
    assert registered["cron_deliver_env_var"] == "TELNYX_VOICE_HOME_CHANNEL"
