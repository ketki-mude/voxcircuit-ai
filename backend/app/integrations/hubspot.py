"""HubSpot CRM through a private app token.

Contacts are upserted with email as the id property, so a retry lands on the same
contact with the same values: the upsert is idempotent by nature, and HubSpot has no
idempotency keys to send. Only standard contact properties are written (email, first
and last name, ZIP); the rest of the lead stays in VoxCircuit's database.
"""

from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.base import CrmContact, IntegrationError

BASE_URL = "https://api.hubapi.com"


class HubSpotCrm:
    def __init__(self, token: str, http: httpx.AsyncClient, base_url: str = BASE_URL) -> None:
        self._token = token
        self._http = http
        self._base_url = base_url

    async def upsert_contact(
        self, *, email: str, properties: dict[str, str], idempotency_key: str
    ) -> CrmContact:
        body = {
            "inputs": [
                {"idProperty": "email", "id": email, "properties": _standard(email, properties)}
            ]
        }
        data = await self._call("POST", "/crm/v3/objects/contacts/batch/upsert", json=body)
        results = (data or {}).get("results") or []
        if not results:
            raise IntegrationError(
                f"HubSpot upsert returned no contact: {(data or {}).get('errors')}"
            )
        return CrmContact(id=str(results[0]["id"]), email=email, properties=properties)

    async def delete_contact(self, *, email: str) -> bool:
        """Permanently delete the contact (GDPR delete). It is looked up first, because a
        GDPR delete of an email HubSpot doesn't know would blocklist that email."""
        found = await self._call(
            "GET",
            f"/crm/v3/objects/contacts/{quote(email, safe='')}",
            params={"idProperty": "email"},
            missing_ok=True,
        )
        if found is None:
            return False
        await self._call(
            "POST", "/crm/v3/objects/contacts/gdpr-delete", json={"objectId": str(found["id"])}
        )
        return True

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
        missing_ok: bool = False,
    ) -> dict[str, Any] | None:
        try:
            response = await self._http.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except httpx.HTTPError as exc:
            raise IntegrationError(f"HubSpot {method} {path} failed: {type(exc).__name__}") from exc
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code >= 400:
            raise IntegrationError(
                f"HubSpot {method} {path} returned {response.status_code}: {_message(response)}"
            )
        if not response.content:
            return {}
        data: dict[str, Any] = response.json()
        return data


def _standard(email: str, properties: dict[str, str]) -> dict[str, str]:
    first, _, last = properties.get("full_name", "").strip().partition(" ")
    mapped = {"email": email}
    if first:
        mapped["firstname"] = first
    if last:
        mapped["lastname"] = last
    if properties.get("zip_code"):
        mapped["zip"] = properties["zip_code"]
    return mapped


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message", ""))[:200]
    except ValueError:
        return response.text[:200]
