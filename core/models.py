"""Data model.

Related lists used by the analytics code (post.snapshots, post.audience) are loaded with
Prefetch(..., to_attr=...) in core.analytics.posts_queryset(), so the analysis code can treat
them as plain lists. The reverse managers themselves are named *_items.
"""

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models

# "untagged": imported from a LinkedIn export that didn't say what kind of post it was (LinkedIn only
# labels articles and videos). Someone should open the post and pick the real format.
FORMATS = ["image", "carousel", "video", "poll", "newsletter", "text", "untagged"]
FORMAT_CHOICES = [(f, f) for f in FORMATS]

# Hours after publishing at which a performance snapshot is taken
CHECKPOINT_HOURS = {"1h": 1, "6h": 6, "24h": 24, "48h": 48, "7d": 168}
CHECKPOINT_CHOICES = [(c, c) for c in CHECKPOINT_HOURS]

ROLES = ["viewer", "editor", "approver", "admin"]  # each includes the rights of the ones before it
ROLE_CHOICES = [(r, r.capitalize()) for r in ROLES]

PLANNED_STATUSES = ["draft", "approved", "publishing", "published", "failed", "missed"]
# Formats in the publishing queue, and the tracking format each becomes once posted
PLANNED_FORMATS = {"text": "text", "image": "image", "carousel": "carousel", "video": "video", "article": "newsletter", "poll": "poll"}
# The app can publish these itself (personal profiles, "Share on LinkedIn"); the rest are posted by hand for now
AUTO_PUBLISH_FORMATS = {"text", "image"}


class Account(models.Model):
    """A LinkedIn asset: a company page, a founder profile or a personal profile."""

    KIND_CHOICES = [("company", "Company page"), ("founder", "Founder profile"), ("personal", "Personal profile")]

    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    linkedin_url = models.CharField(max_length=500, null=True, blank=True)
    # The API's id for this asset: urn:li:organization:123 (company page) or urn:li:person:AbC (profile)
    linkedin_urn = models.CharField(max_length=100, null=True, blank=True)
    # For founder / personal profiles: the company page they belong to (shown together on the Dashboard)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="profiles")
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.name}{' (test)' if self.is_test else ''}"


class Post(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="post_items")
    post_url = models.CharField(max_length=500, unique=True, null=True, blank=True)
    linkedin_post_id = models.CharField(max_length=100, null=True, blank=True)
    author = models.CharField(max_length=200, null=True, blank=True)
    published_at = models.DateTimeField(db_index=True)
    # False when only the date is known (LinkedIn exports give no time of day). Such posts count for
    # day-of-week analysis but are kept out of the posting-time heatmap.
    time_known = models.BooleanField(default=True)
    # Audience: True = targeted to a subset (location, seniority…), False = all followers, None = unknown (manual/export)
    targeted = models.BooleanField(null=True, blank=True)
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, db_index=True)
    topic = models.CharField(max_length=200, null=True, blank=True)
    # True when the topic was guessed from the caption on import and nobody has confirmed it yet
    topic_suggested = models.BooleanField(default=False)
    industry = models.CharField(max_length=200, null=True, blank=True)
    keywords = models.JSONField(default=list, blank=True)
    cta = models.CharField(max_length=300, null=True, blank=True)
    hashtags = models.JSONField(default=list, blank=True)
    caption = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "posts"
        indexes = [models.Index(fields=["account", "published_at"])]

    def __str__(self) -> str:
        return f"{self.topic or 'Post'} · {self.published_at:%Y-%m-%d}"


class Snapshot(models.Model):
    """Performance of one post at one checkpoint. Metrics are nullable: "not recorded" is not zero."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="snapshot_items")
    checkpoint = models.CharField(max_length=4, choices=CHECKPOINT_CHOICES)
    captured_at = models.DateTimeField()

    impressions = models.IntegerField(null=True, blank=True)
    members_reached = models.IntegerField(null=True, blank=True)
    clicks = models.IntegerField(null=True, blank=True)
    reactions = models.IntegerField(null=True, blank=True)
    comments = models.IntegerField(null=True, blank=True)
    reposts = models.IntegerField(null=True, blank=True)
    video_views = models.IntegerField(null=True, blank=True)
    poll_votes = models.IntegerField(null=True, blank=True)
    newsletter_views = models.IntegerField(null=True, blank=True)
    newsletter_subscribers = models.IntegerField(null=True, blank=True)
    followers_gained = models.IntegerField(null=True, blank=True)
    # From LinkedIn's member (founder) post analytics; not available for company page posts
    saves = models.IntegerField(null=True, blank=True)
    sends = models.IntegerField(null=True, blank=True)
    profile_views = models.IntegerField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "snapshots"
        constraints = [models.UniqueConstraint(fields=["post", "checkpoint"], name="uniq_snapshot_post_checkpoint")]

    def __str__(self) -> str:
        return f"{self.post_id} @ {self.checkpoint}"


class PostAudience(models.Model):
    """One line of a post's audience breakdown, e.g. location = "Dubai, United Arab Emirates", 18 (%)."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="audience_items")
    dimension = models.CharField(max_length=30)
    value = models.CharField(max_length=200)
    pct = models.FloatField()  # 0-100, as shown by LinkedIn
    captured_at = models.DateTimeField()

    class Meta:
        db_table = "post_audience"
        constraints = [models.UniqueConstraint(fields=["post", "dimension", "value"], name="uniq_post_audience")]


class PageAudience(models.Model):
    """Page-level follower or visitor demographics on a given date."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="page_audience_items")
    as_of = models.DateField(db_index=True)
    audience = models.CharField(max_length=20)  # followers | visitors
    total = models.IntegerField(null=True, blank=True)
    dimension = models.CharField(max_length=30)
    value = models.CharField(max_length=200)
    count = models.IntegerField(null=True, blank=True)
    pct = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "page_audience"
        constraints = [
            models.UniqueConstraint(fields=["account", "as_of", "audience", "dimension", "value"], name="uniq_page_audience")
        ]


class PageDaily(models.Model):
    """One day of page-level activity, from LinkedIn page analytics exports (dates are UTC, as LinkedIn
    reports them). Every metric is nullable: a file that doesn't cover a metric leaves it untouched."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="page_daily_items")
    date = models.DateField()
    # Content: all posts combined, counted on the day members saw them (not the day they were posted)
    impressions = models.IntegerField(null=True, blank=True)
    unique_impressions = models.IntegerField(null=True, blank=True)
    clicks = models.IntegerField(null=True, blank=True)
    reactions = models.IntegerField(null=True, blank=True)
    comments = models.IntegerField(null=True, blank=True)
    reposts = models.IntegerField(null=True, blank=True)
    # Visitors
    page_views = models.IntegerField(null=True, blank=True)
    unique_visitors = models.IntegerField(null=True, blank=True)
    # Followers
    new_followers = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "page_daily"
        constraints = [models.UniqueConstraint(fields=["account", "date"], name="uniq_page_daily")]
        ordering = ["date"]


PAGE_DAILY_FIELDS = [
    "impressions",
    "unique_impressions",
    "clicks",
    "reactions",
    "comments",
    "reposts",
    "page_views",
    "unique_visitors",
    "new_followers",
]


# ------------------------------------------------------------------ people and access


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("An email is required")
        user = self.model(email=email.strip().lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # Microsoft sign-in only
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra):
        """For local admin access while Microsoft sign-in isn't configured."""
        extra.setdefault("role", "admin")
        extra.setdefault("can_switch_accounts", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser):
    """A team member (invite-only). In production, identity comes from Microsoft sign-in."""

    email = models.EmailField(max_length=320, unique=True)
    name = models.CharField(max_length=200, null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="viewer")
    # People without this (and not admins) only see the default account and get no switcher
    can_switch_accounts = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()
    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "users"
        ordering = ["email"]

    def __str__(self) -> str:
        return self.name or self.email

    # Django admin access is limited to the Admin role
    @property
    def is_staff(self) -> bool:
        return self.is_active and self.role == "admin"

    @property
    def is_superuser(self) -> bool:
        return self.is_staff

    def has_perm(self, perm, obj=None) -> bool:
        return self.is_staff

    def has_perms(self, perm_list, obj=None) -> bool:
        return self.is_staff

    def has_module_perms(self, app_label) -> bool:
        return self.is_staff


class AuthFlow(models.Model):
    """In-progress Microsoft sign-in (state, nonce, PKCE verifier), consumed once by the callback."""

    state = models.CharField(max_length=128, primary_key=True)
    flow = models.JSONField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "auth_flows"


class AppSetting(models.Model):
    """Editable settings section (e.g. 'segments', 'performance', 'access') stored as JSON."""

    key = models.CharField(max_length=50, primary_key=True)
    value = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "app_settings"

    def __str__(self) -> str:
        return self.key


# ------------------------------------------------------------------ AI (Claude)


class AIRun(models.Model):
    """One Claude request (e.g. 'strategy ideas'), kept so results can be re-read without paying again,
    and so usage and cost are visible."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="ai_runs")
    kind = models.CharField(max_length=30, default="ideas")
    focus = models.CharField(max_length=300, null=True, blank=True)
    status = models.CharField(max_length=20, default="ok")  # ok | error
    model = models.CharField(max_length=60)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    created_by = models.CharField(max_length=320, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ai_runs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind} · {self.account_id} · {self.created_at:%Y-%m-%d %H:%M}"


class AccountDoc(models.Model):
    """A small shared document per account, e.g. the brand page checklist or the competitor list.
    One row per (account, kind); the JSON is validated per kind in views/docs.py."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="docs")
    kind = models.CharField(max_length=40)
    data = models.JSONField(default=dict, blank=True)
    updated_by = models.CharField(max_length=320, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "account_docs"
        constraints = [models.UniqueConstraint(fields=["account", "kind"], name="uniq_doc_account_kind")]

    def __str__(self) -> str:
        return f"{self.account_id} · {self.kind}"


class SuggestionDecision(models.Model):
    """The team's decision on one suggestion or Claude idea, shared by everyone on the account.

    key: "rule:<suggestion id>" for data-driven suggestions, "ai:<run id>:<index>" for Claude ideas.
    status: doing (Do it) | later (snoozed until `until`) | dismissed | done.
    """

    STATUSES = ("doing", "later", "dismissed", "done")

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="suggestion_decisions")
    key = models.CharField(max_length=120)
    status = models.CharField(max_length=20)
    until = models.DateTimeField(null=True, blank=True)
    decided_by = models.CharField(max_length=320, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "suggestion_decisions"
        constraints = [models.UniqueConstraint(fields=["account", "key"], name="uniq_decision_account_key")]

    def __str__(self) -> str:
        return f"{self.account_id} · {self.key} · {self.status}"


# ------------------------------------------------------------------ LinkedIn publishing


class LinkedInConnection(models.Model):
    """A LinkedIn member who authorised the app to post on their behalf (w_member_social)."""

    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="linkedin_connection")
    member_urn = models.CharField(max_length=100)  # urn:li:person:<sub>
    name = models.CharField(max_length=200, null=True, blank=True)
    email = models.CharField(max_length=200, null=True, blank=True)
    picture_url = models.CharField(max_length=1000, null=True, blank=True)
    access_token_enc = models.TextField()  # Fernet-encrypted
    scopes = models.CharField(max_length=300, null=True, blank=True)
    expires_at = models.DateTimeField()
    connected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "linkedin_connections"


class OAuthState(models.Model):
    """One-time value tying a LinkedIn login callback to the request that started it (CSRF protection)."""

    state = models.CharField(max_length=64, primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "oauth_states"


class PlannedPost(models.Model):
    """A post waiting to be published. Only 'approved' posts are ever published."""

    STATUS_CHOICES = [(s, s) for s in PLANNED_STATUSES]

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="planned_items")
    # text | image | carousel | video | article | poll. Only text and image can be published by the app today;
    # the others are drafted and approved here, posted by hand, then marked as posted.
    format = models.CharField(max_length=20, default="text", db_index=True)
    caption = models.TextField()
    image_file = models.CharField(max_length=300, null=True, blank=True)  # file name inside UPLOAD_DIR
    # Carousel PDF or video file for review before posting by hand (file name inside UPLOAD_DIR)
    media_file = models.CharField(max_length=300, null=True, blank=True)
    media_name = models.CharField(max_length=300, null=True, blank=True)  # original file name, for display
    # Format-specific fields, e.g. poll question/options, article title/link, carousel document title
    details = models.JSONField(default=dict, blank=True)
    image_alt = models.CharField(max_length=300, null=True, blank=True)
    visibility = models.CharField(max_length=20, default="PUBLIC")  # PUBLIC | CONNECTIONS
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True)
    topic = models.CharField(max_length=200, null=True, blank=True)
    industry = models.CharField(max_length=200, null=True, blank=True)
    cta = models.CharField(max_length=300, null=True, blank=True)
    keywords = models.JSONField(default=list, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    linkedin_urn = models.CharField(max_length=200, null=True, blank=True)
    post = models.ForeignKey(Post, on_delete=models.SET_NULL, null=True, blank=True, related_name="planned_items")
    error = models.TextField(null=True, blank=True)
    # LinkedIn busy: when the next automatic try (approved) or a manual retry (failed) is sensible
    retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)  # busy retries so far
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "planned_posts"

    def __str__(self) -> str:
        return f"{self.status}: {self.caption[:40]}"


class Lead(models.Model):
    """An enquiry or business conversation, recorded by the team (LinkedIn's API doesn't provide these).
    Business facts only: no personal names or contact details are stored."""

    SOURCES = [
        ("company_post", "Company page post"),
        ("founder_post", "Founder's post"),
        ("company_page", "Company page (visit / message)"),
        ("founder_profile", "Founder's profile / message"),
        ("website", "Website"),
        ("referral", "Referral"),
        ("event", "Event"),
        ("other", "Other"),
    ]
    STAGES = [("new", "New"), ("talking", "In talks"), ("proposal", "Proposal sent"), ("won", "Won"), ("lost", "Lost")]

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="leads")
    received_on = models.DateField()
    source = models.CharField(max_length=30, choices=SOURCES)
    # The post or profile it came from, when known
    post = models.ForeignKey(Post, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads")
    via_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads_via")
    company = models.CharField(max_length=200)
    industry = models.CharField(max_length=120, null=True, blank=True)
    in_uae = models.BooleanField(null=True, blank=True)
    role = models.CharField(max_length=120, null=True, blank=True)  # e.g. "Principal", "CTO" (no names)
    decision_maker = models.BooleanField(null=True, blank=True)
    service = models.CharField(max_length=120, null=True, blank=True)
    stage = models.CharField(max_length=20, choices=STAGES, default="new", db_index=True)
    value_aed = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    closed_on = models.DateField(null=True, blank=True)  # set when won or lost
    notes = models.TextField(null=True, blank=True)
    # Attribution: what they say ("Saw a founder's post on LMS") and the tracked link, when it came via the website
    how_heard = models.CharField(max_length=300, null=True, blank=True)
    utm_source = models.CharField(max_length=100, null=True, blank=True)
    utm_medium = models.CharField(max_length=100, null=True, blank=True)
    utm_campaign = models.CharField(max_length=100, null=True, blank=True)
    # When the team first replied (slow replies lose leads)
    replied_at = models.DateTimeField(null=True, blank=True)
    # Qualified = UAE-based decision-maker by default; set it to override
    qualified = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "leads"
        ordering = ["-received_on", "-id"]

    def __str__(self) -> str:
        return f"{self.company} ({self.stage})"
