"""Claude endpoints: generate and read strategy ideas, turn a post idea into a draft."""

import json
from datetime import datetime
from typing import Any, Literal

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
    goal = {"leads": "leads", "sales": "sales", "brand": "brand"}.get(idea.get("type") or "", "engagement")
    p = PlannedPost.objects.create(
        account_id=run.account_id,
        caption=caption[:3000],
        topic=(idea.get("title") or "")[:200] or None,
        goal=goal,
        status="draft",
    )
    return Response({"planned_id": p.id}, status=201)


class PostAssistIn(BaseModel):
    account_id: int
    format: str = Field(max_length=20)
    caption: str = Field(max_length=3500)
    topic: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    cta: str | None = Field(default=None, max_length=300)
    goal: Literal["brand", "leads", "sales", "engagement"] | None = None
    instructions: str | None = Field(default=None, max_length=500)
    # The parts of the post to review, picked after clicking Analyse
    focus: list[Literal["text", "visual", "timing", "poll"]] = Field(default_factory=lambda: ["text"], min_length=1, max_length=4)
    scheduled_at: datetime | None = None  # the chosen posting time, for the timing review
    details: dict[str, Any] = Field(default_factory=dict)  # poll question and options, titles (format details)
    media_attached: bool = False  # an image, PDF or video is attached to the post (videos aren't sent: the AI can't watch them)


@api_view(["GET", "POST"])
def post_assist(request):
    """GET: is Claude connected, and how many analyses are left today. POST: analyse a draft (only on click)."""
    user = request.user
    if request.method == "GET":
        account_id = int_param(request, "account_id", required=True)
        check_account(user, account_id)
        return Response({
            "configured": ai.configured(),
            "runs_today": ai.post_assist_runs_today(Account(id=account_id)),
            "daily_limit": ai.POST_ASSIST_DAILY_LIMIT,
        })
    require(user, "editor")  # costs money: editors and above
    body = parse(PostAssistIn, _form_data(request, ("focus", "details")))
    account = Account.objects.filter(id=body.account_id).first()
    if not account:
        fail(404, "Account not found")
    check_account(user, account.id)
    draft = body.model_dump(include={"format", "caption", "topic", "industry", "cta", "goal", "scheduled_at", "details", "media_attached"})
    try:
        attachment = _attachment(request)
        run = ai.post_assist(account, draft, (body.instructions or "").strip() or None, None if user.dev else user.email, attachment, list(dict.fromkeys(body.focus)))
    except ai.AIError as e:
        fail(400, str(e))
    return Response(_run_out(run), status=201)


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class PostChatIn(BaseModel):
    account_id: int
    format: str = Field(max_length=20)
    caption: str = Field(default="", max_length=3500)
    topic: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    cta: str | None = Field(default=None, max_length=300)
    goal: Literal["brand", "leads", "sales", "engagement"] | None = None
    messages: list[ChatMessageIn] = Field(default_factory=list, max_length=ai.POST_CHAT_MAX_MESSAGES)
    web_search: bool = False  # the team's per-message switch: look things up on the web first (costs a little more)


@api_view(["GET", "POST"])
def post_chat(request):
    """GET: is Claude connected, messages left today. POST: one chat turn (only when someone clicks Start or sends)."""
    user = request.user
    if request.method == "GET":
        account_id = int_param(request, "account_id", required=True)
        check_account(user, account_id)
        return Response({
            "configured": ai.configured(),
            "messages_today": ai.post_chat_messages_today(Account(id=account_id)),
            "daily_limit": ai.POST_CHAT_DAILY_LIMIT,
            "session_limit": ai.POST_CHAT_MAX_MESSAGES,
        })
    require(user, "editor")  # costs money: editors and above
    body = parse(PostChatIn, _form_data(request, json_fields=("messages",)))
    account = Account.objects.filter(id=body.account_id).first()
    if not account:
        fail(404, "Account not found")
    check_account(user, account.id)
    draft = body.model_dump(include={"format", "caption", "topic", "industry", "cta", "goal"})
    history = [m.model_dump() for m in body.messages]
    try:
        attachment = _attachment(request)
        run = ai.post_chat(account, draft, history, None if user.dev else user.email, attachment, body.web_search)
    except ai.AIError as e:
        fail(400, str(e))
    return Response(_run_out(run), status=201)


def _form_data(request, json_fields: tuple[str, ...] = ()) -> dict:
    """JSON body as is; a multipart body (sent when a file is attached) as plain fields, with JSON-encoded lists decoded."""
    if not request.content_type.startswith("multipart/"):
        return request.data
    data = {k: request.data.get(k) for k in request.data if k != "attachment"}
    for k in json_fields:
        if data.get(k):
            try:
                data[k] = json.loads(data[k])
            except ValueError:
                fail(422, f"{k} must be JSON")
    return {k: v for k, v in data.items() if v not in (None, "")}


def _attachment(request) -> dict | None:
    """The post's image or carousel PDF, if the page sent one (raises AIError for unreadable files)."""
    upload = request.FILES.get("attachment")
    return ai.read_attachment(upload) if upload else None
