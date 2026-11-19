"""Content calendar: posts (ours and the linked founders'), planned posts, holidays, periods and events
for one month-sized window, plus the posting-time model's advice for each date."""

from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from rest_framework.decorators import api_view
from rest_framework.response import Response

from .. import analytics as a
from .. import calendar_data, timing
from ..api import fail, int_param
from ..auth import allowed_account_ids, check_account
from ..models import CHECKPOINT_HOURS, Account, PlannedPost
from ..timeutil import as_utc, slot_label, to_uae

MIN_TYPICAL = 5  # posts needed at the same age before "vs typical" is shown (same rule as the Posts page)
MAX_DAYS = 62


def _title(caption: str | None) -> str:
    line = next((ln.strip() for ln in (caption or "").splitlines() if ln.strip()), "")
    return line[:120] or "(no caption)"


def _related(account: Account, allowed: set[int]) -> list[Account]:
    """The account itself, plus the founders linked to a company page (or the page and fellow founders of a profile)."""
    root = account.parent or account
    group = [root, *root.profiles.all()]
    seen, out = set(), []
    for acc in [account, *group]:
        if acc.id not in seen and acc.id in allowed:
            seen.add(acc.id)
            out.append(acc)
    return out


def _latest(snaps):
    return max(snaps, key=lambda s: CHECKPOINT_HOURS[s.checkpoint], default=None)


def _posts_for(acc: Account, start: date, end: date) -> list[dict[str, Any]]:
    posts = a.load_posts(acc.id)
    # The typical post at each age, from ALL of this account's posts (not only the visible month)
    by_cp: dict[str, list[float]] = defaultdict(list)
    for p in posts:
        s = _latest(p.snapshots)
        if s and s.impressions is not None:
            by_cp[s.checkpoint].append(s.impressions)
    out = []
    for p in posts:
        local = to_uae(as_utc(p.published_at))
        if not start <= local.date() <= end:
            continue
        s = _latest(p.snapshots)
        peers = by_cp.get(s.checkpoint, []) if s else []
        typical = median(peers) if len(peers) >= MIN_TYPICAL else None
        imp = s.impressions if s else None
        out.append(
            {
                "id": p.id,
                "account_id": acc.id,
                "account_name": acc.name,
                "account_kind": acc.kind,
                "date": local.date().isoformat(),
                "time": local.strftime("%H:%M") if p.time_known else None,
                "format": p.format,
                "title": _title(p.caption),
                "impressions": imp,
                "checkpoint": s.checkpoint if s else None,
                "vs_typical": round(imp / typical, 2) if imp is not None and typical else None,
            }
        )
    return out


def _timing(posts, start: date, end: date) -> dict[str, Any]:
    """Per date, from the posting-time model (timing.py): best slot, how the date compares with a normal day,
    a slot worth testing, and whether the model has proven itself on newer posts (backtest)."""
    m, n = timing.model_for(posts)
    if m is None:
        return {"ready": False, "posts_used": n,
                "reason": f"The time model needs at least {timing.MIN_POSTS} past posts with a known posting time (has {n})."}
    need = a.min_slot_posts()
    bt = timing.backtest_verdict(posts)
    special = {k: timing._effect(m, k, m.special.get(k, 0.0), m.n_special.get(k, 0), label)
               for k, label in timing.SPECIAL.items()}
    days = {}
    d = start
    while d <= end:
        days[d.isoformat()] = timing.recommend(m, d, need)
        d += timedelta(days=1)
    return {"ready": True, "posts_used": n, "backtest": bt, "special": special, "days": days}


@api_view(["GET"])
def calendar(request):
    account_id = int_param(request, "account_id", required=True)
    check_account(request.user, account_id)
    try:
        start = date.fromisoformat(request.query_params["from"])
        end = date.fromisoformat(request.query_params["to"])
    except (KeyError, ValueError):
        fail(422, "'from' and 'to' are required, as dates like 2026-11-01")
    if start > end or (end - start).days > MAX_DAYS:
        fail(422, f"'from' must be before 'to', at most {MAX_DAYS} days apart")

    account = Account.objects.get(id=account_id)
    allowed = set(allowed_account_ids(request.user))
    accounts = _related(account, allowed)

    posts: list[dict[str, Any]] = []
    for acc in accounts:
        posts += _posts_for(acc, start, end)
    posts.sort(key=lambda p: (p["date"], p["time"] or ""))

    planned = []
    for p in PlannedPost.objects.filter(account_id=account_id, scheduled_at__isnull=False).exclude(status="published"):
        local = to_uae(as_utc(p.scheduled_at))
        if start <= local.date() <= end:
            planned.append(
                {
                    "id": p.id,
                    "date": local.date().isoformat(),
                    "time": local.strftime("%H:%M"),
                    "slot": slot_label(local),
                    "format": p.format,
                    "status": p.status,
                    "title": _title(p.caption),
                }
            )
    planned.sort(key=lambda p: (p["date"], p["time"]))

    first, last = calendar_data.covered_years()
    return Response(
        {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "accounts": [{"id": x.id, "name": x.name, "kind": x.kind} for x in accounts],
            "posts": posts,
            "planned": planned,
            "items": calendar_data.items_between(start, end),
            "timing": _timing(a.load_posts(account_id), start, end),
            "dates_cover": [first, last],
        }
    )


MAX_ITEM_DAYS = 400


@api_view(["GET"])
def calendar_items(request):
    """Only the holidays, periods and events between two dates (no posts or advice), for chart markers."""
    try:
        start = date.fromisoformat(request.query_params["from"])
        end = date.fromisoformat(request.query_params["to"])
    except (KeyError, ValueError):
        fail(422, "'from' and 'to' are required, as dates like 2026-11-01")
    if start > end or (end - start).days > MAX_ITEM_DAYS:
        fail(422, f"'from' must be before 'to', at most {MAX_ITEM_DAYS} days apart")
    return Response({"from": start.isoformat(), "to": end.isoformat(), "items": calendar_data.items_between(start, end)})
