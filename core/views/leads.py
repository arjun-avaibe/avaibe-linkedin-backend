"""Leads and the sales pipeline: enquiries the team records, and how they move to won or lost.
Business facts only (company, role, source, service, value): no personal names or contact details."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..api import NO_CONTENT, fail, int_param, parse
from ..auth import allowed_account_ids, check_account, require
from ..models import Account, Lead, Post
from ..timeutil import as_utc, now_utc, to_uae

Source = Literal["company_post", "founder_post", "company_page", "founder_profile", "website", "referral", "event", "other"]
Stage = Literal["new", "talking", "proposal", "won", "lost"]


class LeadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: int
    received_on: date
    source: Source
    post_id: int | None = None
    via_account_id: int | None = None
    company: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=120)
    in_uae: bool | None = None
    role: str | None = Field(default=None, max_length=120)
    decision_maker: bool | None = None
    service: str | None = Field(default=None, max_length=120)
    stage: Stage = "new"
    value_aed: Decimal | None = Field(default=None, ge=0, le=Decimal("999999999999"))
    closed_on: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    how_heard: str | None = Field(default=None, max_length=300)
    utm_source: str | None = Field(default=None, max_length=100)
    utm_medium: str | None = Field(default=None, max_length=100)
    utm_campaign: str | None = Field(default=None, max_length=100)
    replied_at: datetime | None = None
    qualified: bool | None = None  # None = use the default rule (UAE-based decision-maker)


def is_qualified(x: Lead) -> bool | None:
    """The team's override if set; otherwise UAE-based AND decision-maker (unknown if either is unknown)."""
    if x.qualified is not None:
        return x.qualified
    if x.in_uae is False or x.decision_maker is False:
        return False
    if x.in_uae and x.decision_maker:
        return True
    return None


def _out(x: Lead) -> dict:
    p = x.post
    return {
        "id": x.id,
        "account_id": x.account_id,
        "received_on": x.received_on.isoformat(),
        "source": x.source,
        "post_id": x.post_id,
        "post_title": (next((ln.strip() for ln in (p.caption or "").splitlines() if ln.strip()), "")[:120] or None) if p else None,
        "post_topic": p.topic if p else None,
        "post_format": p.format if p else None,
        "post_date": to_uae(as_utc(p.published_at)).date().isoformat() if p else None,
        "via_account_id": x.via_account_id,
        "via_account_name": x.via_account.name if x.via_account else None,
        "company": x.company,
        "industry": x.industry,
        "in_uae": x.in_uae,
        "role": x.role,
        "decision_maker": x.decision_maker,
        "service": x.service,
        "stage": x.stage,
        "value_aed": float(x.value_aed) if x.value_aed is not None else None,
        "closed_on": x.closed_on.isoformat() if x.closed_on else None,
        "notes": x.notes,
        "how_heard": x.how_heard,
        "utm_source": x.utm_source,
        "utm_medium": x.utm_medium,
        "utm_campaign": x.utm_campaign,
        "replied_at": as_utc(x.replied_at).isoformat() if x.replied_at else None,
        "qualified": x.qualified,
        "is_qualified": is_qualified(x),
        "updated_at": as_utc(x.updated_at).isoformat(),
    }


def _apply(x: Lead, body: LeadIn, user) -> None:
    allowed = set(allowed_account_ids(user))
    if body.post_id is not None:
        post = Post.objects.filter(id=body.post_id).first()
        if not post or post.account_id not in allowed:
            fail(422, "That post isn't one you can see")
    if body.via_account_id is not None and body.via_account_id not in allowed:
        fail(422, "That profile isn't one you can see")
    for k, v in body.model_dump(exclude={"account_id"}).items():
        setattr(x, k, v)
    if x.replied_at is not None:
        x.replied_at = as_utc(x.replied_at)  # times without a zone are taken as UTC
    # Closing date follows the stage: set when won/lost (today if not given), cleared when reopened
    if x.stage in ("won", "lost"):
        x.closed_on = x.closed_on or to_uae(now_utc()).date()
    else:
        x.closed_on = None


@api_view(["GET", "POST"])
def leads(request):
    user = request.user
    if request.method == "POST":
        require(user, "editor")
        body = parse(LeadIn, request.data)
        if not Account.objects.filter(id=body.account_id).exists():
            fail(404, "Account not found")
        check_account(user, body.account_id)
        x = Lead(account_id=body.account_id)
        _apply(x, body, user)
        x.save()
        return Response(_out(Lead.objects.select_related("post", "via_account").get(id=x.id)), status=201)

    account_id = int_param(request, "account_id", required=True)
    check_account(user, account_id)
    rows = Lead.objects.filter(account_id=account_id).select_related("post", "via_account")
    return Response([_out(x) for x in rows])


@api_view(["PUT", "DELETE"])
def lead_detail(request, lead_id: int):
    user = request.user
    require(user, "editor")
    x = Lead.objects.select_related("post", "via_account").filter(id=lead_id).first()
    if not x or x.account_id not in allowed_account_ids(user):
        fail(404, "Lead not found")
    if request.method == "DELETE":
        x.delete()
        return Response(status=NO_CONTENT)
    body = parse(LeadIn, request.data)
    if body.account_id != x.account_id:
        fail(422, "A lead can't be moved to another account")
    _apply(x, body, user)
    x.save()
    return Response(_out(Lead.objects.select_related("post", "via_account").get(id=x.id)))


@api_view(["POST"])
def lead_replied(request, lead_id: int):
    """One click: the team has replied to this lead now (only the first reply is recorded)."""
    user = request.user
    require(user, "editor")
    x = Lead.objects.select_related("post", "via_account").filter(id=lead_id).first()
    if not x or x.account_id not in allowed_account_ids(user):
        fail(404, "Lead not found")
    if x.replied_at is None:
        x.replied_at = now_utc()
        x.save(update_fields=["replied_at", "updated_at"])
    return Response(_out(x))
