"""
Async Zammad REST API client.

Authentication: HTTP Token (Authorization: Token token=<value>).
Retries:        Exponential back-off via tenacity on transient HTTP errors.
Timeouts:       Configurable per-request via settings.
"""
from __future__ import annotations

import base64
import re
from pathlib import PurePosixPath
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import Settings, get_settings
from src.zammad.schemas import (
    ZammadArticleSchema,
    ZammadAttachmentSchema,
    ZammadTicketSchema,
    ZammadUserSchema,
)

logger = structlog.get_logger(__name__)

# Status codes that are safe to retry
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_SAFE_FILENAME_RE = re.compile(r"[^\w.\-]")


def _sanitize_filename(name: str) -> str:
    """Remove characters that could cause path traversal or injection."""
    safe = _SAFE_FILENAME_RE.sub("_", PurePosixPath(name).name)
    return safe[:200] or "file"


class ZammadAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Zammad API {status_code}: {detail}")


class ZammadClient:
    """Thin async wrapper around the Zammad REST API."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self._base_url = cfg.zammad_url + "/api/v1"
        self._token = cfg.zammad_http_token.get_secret_value()
        self._timeout = cfg.zammad_request_timeout
        self._max_retries = cfg.zammad_max_retries
        self._retry_wait = cfg.zammad_retry_wait_seconds
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ZammadClient":
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Token token={self._token}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("ZammadClient must be used as an async context manager")
        return self._http

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _make_retry(self):  # type: ignore[return]
        return retry(
            retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=self._retry_wait, max=30),
            reraise=True,
        )

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        @self._make_retry()
        async def _do() -> httpx.Response:
            resp = await self._client.request(method, path, **kwargs)
            if resp.status_code in _RETRYABLE_STATUS:
                raise httpx.HTTPStatusError(
                    f"Retryable status {resp.status_code}", request=resp.request, response=resp
                )
            return resp

        resp = await _do()
        if resp.is_error:
            body = resp.text[:500]
            logger.warning("zammad_api_error", status=resp.status_code, body=body, path=path)
            raise ZammadAPIError(resp.status_code, body)
        return resp

    # ── Users ─────────────────────────────────────────────────────────────────

    async def search_user_by_login(self, login: str) -> ZammadUserSchema | None:
        resp = await self._request("GET", "/users/search", params={"query": login, "limit": 1})
        users = resp.json()
        return ZammadUserSchema(**users[0]) if users else None

    async def search_user_by_phone(self, phone: str) -> ZammadUserSchema | None:
        resp = await self._request(
            "GET", "/users/search", params={"query": phone, "limit": 5}
        )
        users = resp.json()
        for u in users:
            if u.get("phone") == phone:
                return ZammadUserSchema(**u)
        return None

    async def create_user(
        self,
        *,
        login: str,
        email: str,
        firstname: str,
        lastname: str = "",
        phone: str | None = None,
    ) -> ZammadUserSchema:
        payload: dict[str, Any] = {
            "login": login,
            "email": email,
            "firstname": firstname,
            "lastname": lastname,
            "roles": ["Customer"],
        }
        if phone:
            payload["phone"] = phone
        resp = await self._request("POST", "/users", json=payload)
        return ZammadUserSchema(**resp.json())

    async def update_user(self, user_id: int, *, phone: str | None = None) -> ZammadUserSchema:
        payload: dict[str, Any] = {}
        if phone is not None:
            payload["phone"] = phone
        resp = await self._request("PUT", f"/users/{user_id}", json=payload)
        return ZammadUserSchema(**resp.json())

    # ── Tickets ───────────────────────────────────────────────────────────────

    async def create_ticket(
        self,
        *,
        title: str,
        group: str,
        customer_id: int,
        body: str,
        article_type: str = "web",
    ) -> ZammadTicketSchema:
        payload = {
            "title": title,
            "group": group,
            "customer_id": customer_id,
            "article": {
                "subject": title,
                "body": body,
                "type": article_type,
                "internal": False,
            },
        }
        resp = await self._request("POST", "/tickets", json=payload)
        return ZammadTicketSchema(**resp.json())

    async def get_ticket(self, ticket_id: int) -> ZammadTicketSchema:
        # expand=true forces Zammad to return state as a full object
        # {"id": 4, "name": "closed"} instead of just state_id: 4
        resp = await self._request(
            "GET", f"/tickets/{ticket_id}", params={"expand": "true"}
        )
        return ZammadTicketSchema(**resp.json())

    # ── Articles ──────────────────────────────────────────────────────────────

    async def add_article(
        self,
        *,
        ticket_id: int,
        body: str,
        article_type: str = "web",
        internal: bool = False,
        attachments: list[dict[str, str]] | None = None,
    ) -> ZammadArticleSchema:
        payload: dict[str, Any] = {
            "ticket_id": ticket_id,
            "subject": "Telegram message",
            "body": body,
            "type": article_type,
            "internal": internal,
            "sender": "Customer",
        }
        if attachments:
            payload["attachments"] = attachments
        resp = await self._request("POST", "/ticket_articles", json=payload)
        return ZammadArticleSchema(**resp.json())

    async def add_article_with_attachment(
        self,
        *,
        ticket_id: int,
        body: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> ZammadArticleSchema:
        safe_name = _sanitize_filename(filename)
        encoded = base64.b64encode(content).decode()
        return await self.add_article(
            ticket_id=ticket_id,
            body=body,
            attachments=[
                {
                    "filename": safe_name,
                    "data": encoded,
                    "mime-type": content_type,
                }
            ],
        )

    # ── Attachments (download) ────────────────────────────────────────────────

    async def download_attachment(
        self,
        ticket_id: int,
        article_id: int,
        attachment_id: int,
    ) -> bytes:
        resp = await self._request(
            "GET",
            f"/ticket_attachment/{ticket_id}/{article_id}/{attachment_id}",
        )
        return resp.content
