"""Claude endpoints: generate and read strategy ideas, turn a post idea into a draft."""

from pydantic import BaseModel, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .. import ai
from ..api import fail, int_param, parse
from ..auth import check_account, require
from ..models import Account, AIRun, PlannedPost
from ..timeutil import as_utc


class GenerateIn(BaseModel):
    account_id: int
    focus: str | None = Field(default=None, max_length=300)


def _run_out(r: AIRun) -> dict:
    return {
        "id": r.id,
        "account_id": r.account_id,
        "focus": r.focus,
        "status": r.status,
        "model": r.model,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost_usd": float(r.cost_usd),
        "result": r.result,
        "error": r.error,
        "created_by": r.created_by,
        "created_at": as_utc(r.created_at).isoformat(),
    }


@api_view(["GET", "POST"])
def ideas(request):
    user = request.user
    if request.method == "GET":
        account_id = int_param(request, "account_id", required=True)
        check_account(user, account_id)
        latest = AIRun.objects.filter(account_id=account_id, kind="ideas", status="ok").first()
        return Response({
            "configured": ai.configured(),
            "latest": _run_out(latest) if latest else None,
            "runs_today": ai.runs_today(Account(id=account_id)),
            "running": ai.running(Account(id=account_id)) is not None,  # someone else is generating right now
            "daily_limit": ai.DAILY_RUN_LIMIT,
        })

    require(user, "editor")  # generating costs money: editors and above
    body = parse(GenerateIn, request.data)
    account = Account.objects.filter(id=body.account_id).first()
    if not account:
        fail(404, "Account not found")
    check_account(user, account.id)
    focus = (body.focus or "").strip() or None
    try:
        run = ai.generate_ideas(account, focus, None if user.dev else user.email)
    except ai.AIError as e:
        fail(400, str(e))
    return Response(_run_out(run), status=201)


@api_view(["POST"])
def idea_to_draft(request, run_id: int, index: int):
    """Create a Publishing draft from a post idea's caption (it still needs approval)."""
    user = request.user
    require(user, "editor")
    run = AIRun.objects.filter(id=run_id, status="ok").first()
    if not run:
        fail(404, "Ideas not found")
    check_account(user, run.account_id)
    items = (run.result or {}).get("ideas", [])
    if not 0 <= index < len(items):
        fail(404, "Idea not found")
    idea = items[index]
    caption = (idea.get("draft_caption") or "").strip()
    if not caption:
        fail(400, "This idea has no draft caption")
    p = PlannedPost.objects.create(
        account_id=run.account_id,
        caption=caption[:3000],
        topic=(idea.get("title") or "")[:200] or None,
        status="draft",
    )
    return Response({"planned_id": p.id}, status=201)
