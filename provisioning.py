"""Auto-provisioning for Telnyx Voice Call resources.

Creates a Call Control application, searches for an available US voice number,
orders it, and persists the provisioned state so subsequent starts are
idempotent.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TELNYX_API_BASE = "https://api.telnyx.com/v2"
PROVISIONED_STATE_FILE = "provisioned.json"


class ProvisioningError(Exception):
    """Raised when auto-provisioning fails."""


class ProvisioningResult:
    """Result of a successful auto-provision run."""

    __slots__ = ("application_id", "connection_id", "from_number", "number_order_id", "phone_number_id")

    def __init__(
        self,
        application_id: str,
        connection_id: str,
        from_number: str,
        number_order_id: str,
        phone_number_id: str = "",
    ) -> None:
        self.application_id = application_id
        self.connection_id = connection_id
        self.from_number = from_number
        self.number_order_id = number_order_id
        self.phone_number_id = phone_number_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "application_id": self.application_id,
            "connection_id": self.connection_id,
            "from_number": self.from_number,
            "number_order_id": self.number_order_id,
            "phone_number_id": self.phone_number_id,
        }


class ProvisionedState:
    """Persisted state from a previous provision, including timestamp."""

    __slots__ = ("result", "provisioned_at")

    def __init__(self, result: ProvisioningResult, provisioned_at: str) -> None:
        self.result = result
        self.provisioned_at = provisioned_at

    def to_dict(self) -> Dict[str, Any]:
        return {**self.result.to_dict(), "provisioned_at": self.provisioned_at}


def _state_path(store_path: Optional[str] = None) -> Path:
    base = Path(store_path) if store_path else Path.cwd()
    return base / PROVISIONED_STATE_FILE


def load_provisioned_state(store_path: Optional[str] = None) -> Optional[ProvisionedState]:
    """Load previously provisioned state from disk. Returns None if not found."""
    path = _state_path(store_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ProvisionedState(
            result=ProvisioningResult(
                application_id=raw["application_id"],
                connection_id=raw["connection_id"],
                from_number=raw["from_number"],
                number_order_id=raw.get("number_order_id", ""),
                phone_number_id=raw.get("phone_number_id", ""),
            ),
            provisioned_at=raw.get("provisioned_at", ""),
        )
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def save_provisioned_state(state: ProvisionedState, store_path: Optional[str] = None) -> None:
    """Persist provisioned state to disk."""
    path = _state_path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")


def delete_provisioned_state(store_path: Optional[str] = None) -> None:
    """Remove the persisted state file (best-effort)."""
    try:
        _state_path(store_path).unlink()
    except FileNotFoundError:
        pass


async def _api_post(
    session: Any,
    api_key: str,
    path: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """POST to the Telnyx API and return parsed JSON. Raises on failure."""
    import aiohttp

    url = f"{TELNYX_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with session.post(url, json=payload, headers=headers) as resp:
        body = await resp.json()
        if resp.status >= 400:
            errors = body.get("errors", [])
            detail = errors[0].get("detail", "") if errors else body.get("error", "")
            raise ProvisioningError(
                f"Telnyx API {path} failed (HTTP {resp.status}): {detail}"
            )
        return body


async def _api_delete(
    session: Any,
    api_key: str,
    path: str,
) -> Dict[str, Any]:
    """DELETE a Telnyx API resource. Raises on failure."""
    import aiohttp

    url = f"{TELNYX_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with session.delete(url, headers=headers) as resp:
        body = {}
        try:
            body = await resp.json()
        except Exception:
            pass
        if resp.status >= 400:
            errors = body.get("errors", [])
            detail = errors[0].get("detail", "") if errors else ""
            raise ProvisioningError(
                f"Telnyx DELETE {path} failed (HTTP {resp.status}): {detail}"
            )
        return body


async def _api_get(
    session: Any,
    api_key: str,
    path: str,
) -> Dict[str, Any]:
    """GET from the Telnyx API and return parsed JSON. Raises on failure."""
    import aiohttp

    url = f"{TELNYX_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with session.get(url, headers=headers) as resp:
        body = await resp.json()
        if resp.status >= 400:
            errors = body.get("errors", [])
            detail = errors[0].get("detail", "") if errors else body.get("error", "")
            raise ProvisioningError(
                f"Telnyx API {path} failed (HTTP {resp.status}): {detail}"
            )
        return body


async def _create_call_control_application(
    session: Any,
    api_key: str,
    webhook_url: str,
) -> tuple[str, str]:
    """Create a Telnyx Call Control application. Returns (application_id, connection_id)."""
    payload = {
        "application_name": "Hermes Voice AI Agent",
        "webhook_event_url": webhook_url,
        "webhook_event_failover_url": "",
        "dtmf_type": "RFC 2833",
        "first_command_timeout": True,
        "first_command_timeout_secs": 30,
    }
    body = await _api_post(session, api_key, "/call_control_applications", payload)
    data = body.get("data", body)
    application_id = str(data.get("id", ""))
    connection_id = str(data.get("connection_id", application_id))
    return application_id, connection_id


async def _search_available_number(session: Any, api_key: str) -> str:
    """Search for an available US voice number. Returns E.164 string."""
    path = (
        "/available_phone_numbers"
        "?filter[country_code]=US"
        "&filter[features][]=voice"
        "&filter[limit]=1"
    )
    body = await _api_get(session, api_key, path)
    numbers = body.get("data", [])
    if not numbers or not numbers[0].get("phone_number"):
        raise ProvisioningError("No available US voice phone numbers found")
    return str(numbers[0]["phone_number"])


async def _order_phone_number(
    session: Any,
    api_key: str,
    phone_number: str,
    connection_id: str,
) -> tuple[str, str, str]:
    """Order a phone number and assign it to a connection.

    Returns (order_id, phone_number, phone_number_id).
    """
    payload = {
        "phone_numbers": [{"phone_number": phone_number}],
        "connection_id": connection_id,
    }
    body = await _api_post(session, api_key, "/number_orders", payload)
    data = body.get("data", body)
    order_id = str(data.get("id", ""))
    ordered = data.get("phone_numbers", [])
    from_number = str(ordered[0]["phone_number"]) if ordered else phone_number
    phone_number_id = str(ordered[0].get("id", "")) if ordered else ""
    return order_id, from_number, phone_number_id


async def provision(
    api_key: str,
    webhook_url: str,
    *,
    connection_id: str = "",
    from_number: str = "",
    store_path: Optional[str] = None,
    session: Optional[Any] = None,
) -> ProvisioningResult:
    """Auto-provision a Telnyx Call Control Application + phone number.

    Idempotent: if provisioned.json already exists the persisted result is
    returned without making any API calls.  Skips provisioning if both
    connection_id and from_number are already supplied.
    """
    # 1. Already fully configured — nothing to do
    if connection_id and from_number:
        logger.info("[provisioning] connection_id and from_number already set — skipping")
        return ProvisioningResult(
            application_id=connection_id,
            connection_id=connection_id,
            from_number=from_number,
            number_order_id="",
        )

    # 2. Check persisted state from a previous run
    persisted = load_provisioned_state(store_path)
    if persisted:
        logger.info(
            "[provisioning] Loaded persisted state — from_number: %s, connection_id: %s",
            persisted.result.from_number,
            persisted.result.connection_id,
        )
        return persisted.result

    # Need an aiohttp session for API calls
    import aiohttp

    own_session = False
    if session is None:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        own_session = True

    try:
        logger.info("[provisioning] Starting auto-provisioning…")

        # 3. Create Call Control Application (unless connection_id already supplied)
        app_id = connection_id
        conn_id = connection_id
        if not conn_id:
            app_id, conn_id = await _create_call_control_application(session, api_key, webhook_url)
            logger.info("[provisioning] Created CC application: %s (connection_id: %s)", app_id, conn_id)

        # 4. Search + order phone number (unless from_number already supplied)
        num = from_number
        order_id = ""
        pn_id = ""
        if not num:
            available = await _search_available_number(session, api_key)
            logger.info("[provisioning] Found available number: %s", available)
            order_id, num, pn_id = await _order_phone_number(session, api_key, available, conn_id)
            logger.info("[provisioning] Ordered number: %s (orderId: %s)", num, order_id)

        result = ProvisioningResult(
            application_id=app_id,
            connection_id=conn_id,
            from_number=num,
            number_order_id=order_id,
            phone_number_id=pn_id,
        )

        # 5. Persist state
        state = ProvisionedState(
            result=result,
            provisioned_at=datetime.now(timezone.utc).isoformat(),
        )
        save_provisioned_state(state, store_path)
        logger.info("[provisioning] Provisioning complete — from_number: %s", num)

        return result
    except Exception:
        raise
    finally:
        if own_session:
            await session.close()


async def deprovision(
    api_key: str,
    *,
    store_path: Optional[str] = None,
    state: Optional[ProvisionedState] = None,
) -> None:
    """Clean up provisioned resources: release the phone number, delete the number
    order, and delete the CC application.

    Deleting a number_order does not release the phone number — we must DELETE
    /phone_numbers/{id} separately.  All errors are caught and logged but do not
    raise.
    """
    import aiohttp

    persisted = state or load_provisioned_state(store_path)
    if not persisted:
        logger.warning("[provisioning] No provisioned state found — nothing to deprovision")
        return

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        # Release the phone number
        if persisted.result.phone_number_id:
            try:
                await _api_delete(session, api_key, f"/phone_numbers/{persisted.result.phone_number_id}")
                logger.info("[provisioning] Released phone number: %s", persisted.result.phone_number_id)
            except Exception as exc:
                logger.warning("[provisioning] Could not release phone number %s: %s", persisted.result.phone_number_id, exc)

        # Delete number order
        if persisted.result.number_order_id:
            try:
                await _api_delete(session, api_key, f"/number_orders/{persisted.result.number_order_id}")
                logger.info("[provisioning] Deleted number order: %s", persisted.result.number_order_id)
            except Exception as exc:
                logger.warning("[provisioning] Could not delete number order %s: %s", persisted.result.number_order_id, exc)

        # Delete CC application
        if persisted.result.application_id:
            try:
                await _api_delete(session, api_key, f"/call_control_applications/{persisted.result.application_id}")
                logger.info("[provisioning] Deleted CC application: %s", persisted.result.application_id)
            except Exception as exc:
                logger.warning("[provisioning] Could not delete CC application %s: %s", persisted.result.application_id, exc)

    delete_provisioned_state(store_path)
    logger.info("[provisioning] Deprovisioned successfully")
