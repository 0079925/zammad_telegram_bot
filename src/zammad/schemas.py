"""
Pydantic schemas for the Zammad REST API.

Only the fields actually used by this integration are declared.
Unknown fields from Zammad responses are silently ignored (extra="ignore").
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ZammadUserSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    login: str
    email: str
    firstname: str = ""
    lastname: str = ""
    phone: str | None = None


class ZammadTicketSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    number: str
    title: str
    state_id: int | None = None
    state: dict | None = None  # {"name": "open", ...}
    group: dict | None = None  # {"name": "Support L1", ...}
    customer_id: int | None = None

    @field_validator("state", mode="before")
    @classmethod
    def _normalize_state(cls, value):
        if isinstance(value, str):
            return {"name": value}
        return value

    @field_validator("group", mode="before")
    @classmethod
    def _normalize_group(cls, value):
        if isinstance(value, str):
            return {"name": value}
        return value


class ZammadAttachmentSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    filename: str
    size: int | None = None
    preferences: dict = Field(default_factory=dict)

    @property
    def content_type(self) -> str:
        return self.preferences.get("Content-Type", "application/octet-stream")


class ZammadArticleSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    ticket_id: int
    body: str = ""
    internal: bool = False
    created_by_id: int | None = None
    attachments: list[ZammadAttachmentSchema] = Field(default_factory=list)
    content_type: str = "text/plain"

    @property
    def body_text(self) -> str:
        """Return plain-text body regardless of content_type."""
        if "html" in self.content_type.lower():
            # Very basic HTML stripping — keeps it dependency-free
            import re

            text = re.sub(r"<br\s*/?>", "\n", self.body, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", "", text)
            return text.strip()
        return self.body.strip()


# ── Webhook payload ───────────────────────────────────────────────────────────

class WebhookTicket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    number: str
    title: str
    state: dict | None = None

    @field_validator("state", mode="before")
    @classmethod
    def _normalize_state(cls, value):
        if isinstance(value, str):
            return {"name": value}
        return value


class WebhookArticle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    ticket_id: int
    body: str = ""
    internal: bool = False
    created_by_id: int | None = None
    content_type: str = "text/plain"
    attachments: list[ZammadAttachmentSchema] = Field(default_factory=list)

    @property
    def body_text(self) -> str:
        if "html" in self.content_type.lower():
            import re

            text = re.sub(r"<br\s*/?>", "\n", self.body, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", "", text)
            return text.strip()
        return self.body.strip()


class ZammadWebhookPayload(BaseModel):
    """Shape of the JSON body Zammad POSTs to our webhook endpoint."""

    model_config = ConfigDict(extra="ignore")

    ticket: WebhookTicket
    article: WebhookArticle | None = None
