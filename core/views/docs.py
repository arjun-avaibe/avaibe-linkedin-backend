"""Shared per-account documents for the Brand section: the LinkedIn page checklist and the competitor list."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..api import fail, parse
from ..auth import check_account, require
from ..models import Account, AccountDoc
from ..timeutil import as_utc

URL = r"^https?://\S+$"


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool = False
    note: str | None = Field(default=None, max_length=300)


class PageChecklist(BaseModel):
    """Ticks and notes, keyed by the item ids the page shows (the item wording lives in the app)."""

    model_config = ConfigDict(extra="forbid")
    items: dict[str, ChecklistItem] = Field(default_factory=dict, max_length=60)


class Competitor(BaseModel):
    """Public, observed facts kept apart from AVAIBE's own interpretation."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=120)
    segment: Literal["software", "erp", "edtech", "it_services", "agency", "other"] = "other"
    website: str | None = Field(default=None, max_length=300, pattern=URL)
    linkedin_url: str | None = Field(default=None, max_length=300, pattern=URL)
    # Observed (public)
    followers: int | None = Field(default=None, ge=0, le=100_000_000)
    posts_per_week: float | None = Field(default=None, ge=0, le=100)
    topics: str | None = Field(default=None, max_length=400)
    positioning: str | None = Field(default=None, max_length=600)
    # Interpretation (ours)
    strengths: str | None = Field(default=None, max_length=600)
    opportunity: str | None = Field(default=None, max_length=600)
    checked_on: date | None = None


class Competitors(BaseModel):
    model_config = ConfigDict(extra="forbid")
    competitors: list[Competitor] = Field(default_factory=list, max_length=40)


class MemberTotals(BaseModel):
    """A founder's totals across ALL their posts (LinkedIn's q=me analytics), written by the sync."""

    model_config = ConfigDict(extra="forbid")
    as_of: date | None = None
    followers: int | None = None
    totals: dict[str, int] = Field(default_factory=dict, max_length=20)


KINDS: dict[str, type[BaseModel]] = {"page_checklist": PageChecklist, "competitors": Competitors, "member_totals": MemberTotals}


def _out(kind: str, doc: AccountDoc | None) -> dict:
    return {
        "kind": kind,
        "data": doc.data if doc else KINDS[kind]().model_dump(mode="json"),
        "updated_by": doc.updated_by if doc else None,
        "updated_at": as_utc(doc.updated_at).isoformat() if doc else None,
    }


@api_view(["GET", "PUT"])
def account_doc(request, account_id: int, kind: str):
    user = request.user
    if kind not in KINDS:
        fail(404, "Unknown document")
    if not Account.objects.filter(id=account_id).exists():
        fail(404, "Account not found")
    check_account(user, account_id)
    if request.method == "GET":
        return Response(_out(kind, AccountDoc.objects.filter(account_id=account_id, kind=kind).first()))

    require(user, "editor")
    body = parse(KINDS[kind], request.data)
    doc, _ = AccountDoc.objects.update_or_create(
        account_id=account_id,
        kind=kind,
        defaults={"data": body.model_dump(mode="json"), "updated_by": None if user.dev else user.email},
    )
    return Response(_out(kind, doc))


def competitor_names(account_id: int) -> list[str]:
    """For Claude's context: the competitor names the team keeps on the Competitors page."""
    doc = AccountDoc.objects.filter(account_id=account_id, kind="competitors").first()
    return [c.get("name") for c in (doc.data.get("competitors", []) if doc else []) if c.get("name")]
