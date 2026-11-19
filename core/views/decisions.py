"""The team's shared decisions on suggestions and Claude ideas: Do it / Later / Dismiss / Done."""

from datetime import timedelta

from pydantic import BaseModel, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..api import int_param, parse
from ..auth import check_account, require
from ..models import SuggestionDecision
from ..timeutil import as_utc, now_utc

LATER_DAYS = 7  # "Later" hides an item for a week, then it comes back


class DecisionIn(BaseModel):
    account_id: int
    key: str = Field(min_length=1, max_length=120, pattern=r"^(rule:[\w-]+|ai:\d+:\d+)$")
    # None clears the decision (Undo)
    status: str | None = Field(default=None, pattern=r"^(doing|later|dismissed|done)$")


def _out(d: SuggestionDecision) -> dict:
    return {
        "key": d.key,
        "status": d.status,
        "until": as_utc(d.until).isoformat() if d.until else None,
        "decided_by": d.decided_by,
        "updated_at": as_utc(d.updated_at).isoformat(),
    }


@api_view(["GET", "PUT"])
def decisions(request):
    user = request.user
    if request.method == "GET":
        account_id = int_param(request, "account_id", required=True)
        check_account(user, account_id)
        # A "Later" whose week is up counts as no decision again
        rows = [d for d in SuggestionDecision.objects.filter(account_id=account_id)
                if not (d.status == "later" and d.until and as_utc(d.until) <= now_utc())]
        return Response([_out(d) for d in rows])

    require(user, "editor")
    body = parse(DecisionIn, request.data)
    check_account(user, body.account_id)
    if body.status is None:
        SuggestionDecision.objects.filter(account_id=body.account_id, key=body.key).delete()
        return Response({"key": body.key, "status": None})
    d, _ = SuggestionDecision.objects.update_or_create(
        account_id=body.account_id,
        key=body.key,
        defaults={
            "status": body.status,
            "until": now_utc() + timedelta(days=LATER_DAYS) if body.status == "later" else None,
            "decided_by": None if user.dev else user.email,
        },
    )
    return Response(_out(d))
