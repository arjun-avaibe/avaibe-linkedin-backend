from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .timeutil import from_uae_local

Format = Literal["image", "carousel", "video", "poll", "newsletter", "text", "untagged"]
Checkpoint = Literal["1h", "6h", "24h", "48h", "7d"]
AccountKind = Literal["company", "founder", "personal"]


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: AccountKind
    linkedin_url: str | None = Field(default=None, max_length=500)
    is_test: bool = False


class AccountOut(AccountIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


def normalise_hashtags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = t.strip().lstrip("#").strip().lower()
        if t and f"#{t}" not in out:
            out.append(f"#{t}")
    return out


class PostIn(BaseModel):
    account_id: int
    post_url: str | None = Field(default=None, max_length=500)
    linkedin_post_id: str | None = Field(default=None, max_length=100)
    author: str | None = Field(default=None, max_length=200)
    # A value without a time-zone offset is read as UAE local time.
    published_at: datetime
    # None = keep what is stored (a new post: True). False = only the date is known.
    time_known: bool | None = None
    format: Format
    topic: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    keywords: list[str] = []
    cta: str | None = Field(default=None, max_length=300)
    hashtags: list[str] = []
    caption: str | None = None
    notes: str | None = None

    @field_validator("published_at")
    @classmethod
    def _uae_default(cls, v: datetime) -> datetime:
        return from_uae_local(v)

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, v: list[str]) -> list[str]:
        return normalise_hashtags(v)

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, v: list[str]) -> list[str]:
        return [k.strip() for k in v if k.strip()]

    @field_validator("post_url", "linkedin_post_id", "author", "topic", "industry", "cta")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class SnapshotIn(BaseModel):
    # Defaults to "now" when omitted
    captured_at: datetime | None = None
    impressions: int | None = Field(default=None, ge=0)
    members_reached: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    reactions: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    video_views: int | None = Field(default=None, ge=0)
    poll_votes: int | None = Field(default=None, ge=0)
    newsletter_views: int | None = Field(default=None, ge=0)
    newsletter_subscribers: int | None = Field(default=None, ge=0)
    followers_gained: int | None = Field(default=None, ge=0)
    notes: str | None = None

    @field_validator("captured_at")
    @classmethod
    def _uae_default(cls, v: datetime | None) -> datetime | None:
        return from_uae_local(v) if v else None


Dimension = Literal["location", "industry", "seniority", "job_title", "job_function", "company_size", "company"]


class AudienceRowIn(BaseModel):
    dimension: Dimension
    value: str = Field(min_length=1, max_length=200)
    pct: float = Field(ge=0, le=100)

    @field_validator("value")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class PostAudienceIn(BaseModel):
    captured_at: datetime | None = None  # defaults to now; naive = UAE time
    rows: list[AudienceRowIn]

    @field_validator("captured_at")
    @classmethod
    def _uae_default(cls, v: datetime | None) -> datetime | None:
        return from_uae_local(v) if v else None


class PageAudienceRowIn(BaseModel):
    dimension: Dimension
    value: str = Field(min_length=1, max_length=200)
    count: int | None = Field(default=None, ge=0)
    pct: float | None = Field(default=None, ge=0, le=100)


class PageAudienceIn(BaseModel):
    as_of: date
    audience: Literal["followers", "visitors"]
    total: int | None = Field(default=None, ge=0)
    rows: list[PageAudienceRowIn]


PlannedFormat = Literal["text", "image", "carousel", "video", "article", "poll"]


class CarouselSlide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(default="", max_length=120)
    body: str = Field(default="", max_length=400)


class PlannedDetails(BaseModel):
    """Format-specific fields. Only the ones that belong to the post's format are kept."""

    model_config = ConfigDict(extra="forbid")

    # carousel
    document_title: str | None = Field(default=None, max_length=100)
    # carousel built in the app: the slides and their look (the PDF itself is made in the browser and uploaded as media)
    slides: list[CarouselSlide] | None = Field(default=None, max_length=20)
    carousel_theme: Literal["brand", "dark", "light"] | None = None
    carousel_accent: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    # video
    subtitles: bool | None = None
    # article / newsletter
    title: str | None = Field(default=None, max_length=150)
    link: str | None = Field(default=None, max_length=500, pattern=r"^https?://\S+$")
    # poll (LinkedIn: question up to 140 characters, 2-4 options of up to 30 characters)
    question: str | None = Field(default=None, max_length=140)
    options: list[str] | None = None
    duration: Literal["1d", "3d", "1w", "2w"] | None = None

    @field_validator("options")
    @classmethod
    def _options(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        v = [o.strip() for o in v if o.strip()]
        if not 2 <= len(v) <= 4:
            raise ValueError("A poll needs 2 to 4 options")
        if any(len(o) > 30 for o in v):
            raise ValueError("Each poll option can be at most 30 characters")
        return v


FORMAT_DETAIL_FIELDS = {
    "text": set(),
    "image": set(),
    "carousel": {"document_title", "slides", "carousel_theme", "carousel_accent"},
    "video": {"subtitles"},
    "article": {"title", "link"},
    "poll": {"question", "options", "duration"},
}


class PlannedPostIn(BaseModel):
    account_id: int
    format: PlannedFormat = "text"
    details: PlannedDetails = Field(default_factory=PlannedDetails)
    caption: str = Field(min_length=1, max_length=3000)  # LinkedIn's post text limit
    image_alt: str | None = Field(default=None, max_length=300)
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC"
    # Naive = UAE time. None = not scheduled (publish manually)
    scheduled_at: datetime | None = None
    topic: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    cta: str | None = Field(default=None, max_length=300)
    keywords: list[str] = []

    @field_validator("scheduled_at")
    @classmethod
    def _uae_default(cls, v: datetime | None) -> datetime | None:
        return from_uae_local(v) if v else None

    @field_validator("topic", "industry", "cta", "image_alt")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @model_validator(mode="after")
    def _details_for_format(self) -> "PlannedPostIn":
        if self.format == "poll" and (not self.details.question or not self.details.options):
            raise ValueError("A poll needs a question and 2 to 4 options")
        return self

    def stored_details(self) -> dict:
        """Only the fields that belong to this format, without empty values."""
        keep = FORMAT_DETAIL_FIELDS[self.format]
        return {k: v for k, v in self.details.model_dump().items() if k in keep and v not in (None, "", [])}


METRIC_FIELDS = [
    "impressions",
    "members_reached",
    "clicks",
    "reactions",
    "comments",
    "reposts",
    "video_views",
    "poll_votes",
    "newsletter_views",
    "newsletter_subscribers",
    "followers_gained",
]
