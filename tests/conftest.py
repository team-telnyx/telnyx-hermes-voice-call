"""Test bootstrap for the Telnyx Voice Call Hermes plugin."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = Path.home() / ".hermes" / "hermes-agent"

for path in (ROOT, HERMES_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


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
