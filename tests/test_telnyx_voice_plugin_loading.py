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


def test_adapter_imports_as_plugin_package_without_repo_on_sys_path():
    """Hermes loads directory plugins as a package and does not add the plugin dir to sys.path.

    Regression: a bare ``from provisioning import ...`` in adapter.py made
    ``hermes plugins validate`` fail with "No module named 'adapter'" even though
    the in-process tests passed (they put the repo root on sys.path).
    """
    import json
    import subprocess
    import tempfile
    import textwrap

    root = Path(__file__).resolve().parents[1]
    entries = []
    for entry in sys.path:
        resolved = Path(entry or ".").resolve()
        if resolved != root:
            entries.append(str(entry))
    script = textwrap.dedent(
        f"""
        import importlib.util, json, sys
        sys.path[:] = json.loads({json.dumps(json.dumps(entries))})
        spec = importlib.util.spec_from_file_location(
            "telnyx_pkg_under_test",
            {str(root / "__init__.py")!r},
            submodule_search_locations=[{str(root)!r}],
        )
        pkg = importlib.util.module_from_spec(spec)
        sys.modules["telnyx_pkg_under_test"] = pkg
        spec.loader.exec_module(pkg)
        from telnyx_pkg_under_test import adapter
        assert callable(adapter.provision)
        print("package-import-ok")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "package-import-ok" in proc.stdout
