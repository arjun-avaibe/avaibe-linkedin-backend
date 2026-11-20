"""Image generation endpoints (Create post → Generate with AI). Nothing is generated without a click."""

from django.http import FileResponse
from pydantic import BaseModel, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .. import imagegen
from ..api import fail, int_param, parse
from ..auth import check_account, require
from ..models import Account, AIRun


class GenerateIn(BaseModel):
    account_id: int
    prompt: str = Field(min_length=1, max_length=2000)
    shape: imagegen.Shape = "square"
    count: int = Field(default=1, ge=1, le=4)


class SlideIn(BaseModel):
    account_id: int
    style: str = Field(min_length=1, max_length=600)
    headline: str = Field(default="", max_length=120)
    body: str = Field(default="", max_length=400)
    index: int = Field(ge=0, le=19)
    total: int = Field(ge=2, le=20)


def _out(run: AIRun) -> dict:
    names = (run.result or {}).get("images", [])
    return {
        "run_id": run.id,
        "provider": run.model,
        "cost_usd": float(run.cost_usd),
        "images": [{"url": f"/api/images/{run.id}/{i}"} for i in range(len(names))],
    }


def _account(request, account_id: int) -> Account:
    account = Account.objects.filter(id=account_id).first()
    if not account:
        fail(404, "Account not found")
    check_account(request.user, account.id)
    return account


@api_view(["GET"])
def status(request):
    account_id = int_param(request, "account_id", required=True)
    check_account(request.user, account_id)
    p = imagegen.provider()
    return Response({
        "configured": p is not None,
        "provider": p.name if p else None,
        "price_per_image": float(p.price_per_image) if p else None,
        "images_today": imagegen.images_today(Account(id=account_id)),
        "daily_limit": imagegen.IMAGE_DAILY_LIMIT,
    })


@api_view(["POST"])
def generate(request):
    """1-4 options for a post image."""
    require(request.user, "editor")  # costs money
    body = parse(GenerateIn, request.data)
    account = _account(request, body.account_id)
    try:
        run = imagegen.generate(account, imagegen.image_prompt(body.prompt), body.shape, body.count, None if request.user.dev else request.user.email, "post image")
    except imagegen.ImageGenError as e:
        fail(400, str(e))
    return Response(_out(run), status=201)


@api_view(["POST"])
def slide(request):
    """One carousel slide (the page asks for them one at a time, so progress shows and one can be redone)."""
    require(request.user, "editor")
    body = parse(SlideIn, request.data)
    account = _account(request, body.account_id)
    prompt = imagegen.slide_prompt(body.style, body.headline, body.body, body.index, body.total, account.name)
    try:
        run = imagegen.generate(account, prompt, "portrait", 1, None if request.user.dev else request.user.email, f"carousel slide {body.index + 1}")
    except imagegen.ImageGenError as e:
        fail(400, str(e))
    return Response(_out(run), status=201)


@api_view(["GET"])
def image(request, run_id: int, index: int):
    run = AIRun.objects.filter(id=run_id, kind="image_gen", status="ok").first()
    if not run:
        fail(404, "Image not found")
    check_account(request.user, run.account_id)
    path = imagegen.file_path(run, index)
    if not path:
        fail(404, "Image not found")
    return FileResponse(open(path, "rb"))
