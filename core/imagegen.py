"""Image generation for posts and carousel slides, with the provider as a plug-in.

No provider is chosen yet. To switch one on:
1. Write an adapter: a class with `name`, `price_per_image` and `generate(prompt, shape, n) -> list[bytes]`
   (PNG or JPEG bytes), using that provider's official SDK or API.
2. Register it in PROVIDERS below.
3. Set IMAGE_PROVIDER=<its key> and that provider's API key in server/.env, then restart the backend.

Until then everything reports "not connected" and nothing is sent anywhere.
Generated images are kept in uploads/generated/ and served only to people who can see the account.
"""

import os
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Literal, Protocol

from .appconf import UPLOAD_DIR
from .models import Account, AIRun
from .timeutil import now_utc

Shape = Literal["square", "portrait", "landscape"]
SHAPES: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),  # feed image
    "portrait": (1080, 1350),  # 4:5, LinkedIn carousel slides and tall feed images
    "landscape": (1200, 627),  # link-preview style
}
IMAGE_DAILY_LIMIT = int(os.getenv("IMAGE_DAILY_LIMIT", "40"))  # images per account per rolling 24 hours
GENERATED_DIR = "generated"


class ImageGenError(Exception):
    """Shown to the user as-is."""


class ImageProvider(Protocol):
    name: str
    price_per_image: Decimal  # USD, for the cost record

    def generate(self, prompt: str, shape: Shape, n: int) -> list[bytes]: ...


# Register adapters here, e.g. {"openai": OpenAIImages(), "gemini": GeminiImages()}
PROVIDERS: dict[str, ImageProvider] = {}


def provider() -> ImageProvider | None:
    return PROVIDERS.get(os.getenv("IMAGE_PROVIDER", "").strip().lower())


def configured() -> bool:
    return provider() is not None


def images_today(account: Account) -> int:
    runs = AIRun.objects.filter(account=account, kind="image_gen", status="ok", created_at__gte=now_utc() - timedelta(days=1))
    return sum(len((r.result or {}).get("images", [])) for r in runs)


# ---- prompts: one consistent style across a carousel, readable text, the brand's own words only

def slide_prompt(style: str, headline: str, body: str, index: int, total: int, account_name: str) -> str:
    role = "cover slide (the hook)" if index == 0 else "closing slide (call to action)" if index == total - 1 else f"slide {index + 1} of {total}"
    lines = [
        f"A LinkedIn carousel {role}, portrait 4:5, for {account_name}, a UAE software company.",
        f"Visual style (keep identical across all slides): {style}",
        f'Large, bold, perfectly spelled headline text: "{headline}"' if headline else "No headline text.",
    ]
    if body:
        lines.append(f'Smaller supporting text, spelled exactly: "{body}"')
    lines += [
        f'Small page marker "{index + 1}/{total}" in a corner.' if index else 'A small "Swipe" hint at the bottom right.',
        "Generous margins, high contrast, readable on a phone. No other text, no watermarks, no fake logos.",
    ]
    return "\n".join(lines)


def image_prompt(prompt: str) -> str:
    return prompt.strip() + "\nProfessional, clean, suitable for a B2B LinkedIn post. No watermarks, no fake logos, no misspelled text."


# ---- generating and storing

def generate(account: Account, prompt: str, shape: Shape, n: int, user_email: str | None, purpose: str) -> AIRun:
    p = provider()
    if p is None:
        raise ImageGenError("The image AI isn't connected yet. Your manager needs to choose a provider and add its API key.")
    if not prompt.strip():
        raise ImageGenError("Describe the image first.")
    if images_today(account) + n > IMAGE_DAILY_LIMIT:
        raise ImageGenError(f"The limit of {IMAGE_DAILY_LIMIT} generated images per account in 24 hours would be passed. This protects the budget.")
    run = AIRun.objects.create(account=account, kind="image_gen", focus=purpose[:300], status="running", model=p.name, created_by=user_email)
    try:
        blobs = p.generate(prompt, shape, n)
    except ImageGenError as e:
        run.status, run.error = "error", str(e)
        run.save(update_fields=["status", "error"])
        raise
    except Exception as e:  # provider SDK errors: keep the detail in the log, show a plain message
        run.status, run.error = "error", str(e)[:500]
        run.save(update_fields=["status", "error"])
        raise ImageGenError("The image AI couldn't make this image right now. Try again, or change the description.")
    folder = UPLOAD_DIR / GENERATED_DIR
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for b in blobs:
        ext = "png" if b[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
        name = f"{uuid.uuid4().hex}.{ext}"
        (folder / name).write_bytes(b)
        names.append(name)
    run.status = "ok"
    run.cost_usd = p.price_per_image * len(names)
    run.result = {"prompt": prompt, "shape": shape, "images": names}
    run.save()
    return run


def file_path(run: AIRun, index: int):
    names = (run.result or {}).get("images", [])
    if not 0 <= index < len(names):
        return None
    path = UPLOAD_DIR / GENERATED_DIR / names[index]
    return path if path.exists() else None
