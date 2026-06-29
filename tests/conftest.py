"""Test bootstrap for the Telnyx Voice Call Hermes plugin."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _find_hermes_root() -> Path:
    """Locate a Hermes Agent checkout for tests that import gateway modules."""
    candidates = []
    env_root = os.getenv("HERMES_AGENT_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.extend([
        Path.cwd().parent / "hermes-agent",
        Path.home() / ".hermes" / "hermes-agent",
    ])
    for candidate in candidates:
        if (candidate / "gateway" / "platforms" / "base.py").exists():
            return candidate
    # Fallback: add the default path anyway so import errors are clear
    return Path.home() / ".hermes" / "hermes-agent"


HERMES_ROOT = _find_hermes_root()
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))


def _ensure_telnyx_voice_registered() -> None:
    """Make Platform('telnyx_voice_call') available in tests."""
    from gateway.platform_registry import PlatformEntry, platform_registry

    if platform_registry.is_registered("telnyx_voice_call"):
        return
    platform_registry.register(PlatformEntry(
        name="telnyx_voice_call",
        label="Telnyx Voice Call",
        adapter_factory=lambda cfg: None,
        check_fn=lambda: True,
        required_env=[],
    ))


_ensure_telnyx_voice_registered()
