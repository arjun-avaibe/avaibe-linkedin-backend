"""Who is using the app, and what they may do.

- Sign-in: Microsoft Entra ID (single tenant) via MSAL; Django's session framework holds the login
  (httpOnly cookie, server-side session row).
- Invite-only: a Microsoft account can sign in only if an admin added that email on the Team page
  (or it is listed in ADMIN_EMAILS, which bootstraps the first admins).
- Roles: viewer < editor < admin < superadmin. Editors write, schedule and publish. Admins manage viewers and editors;
  only super admins manage admins and super admins. Nobody changes their own role.
- Account access: people without "can switch accounts" (and not admins) only see the default account.
  Enforced on every API call, not just hidden in the UI.
- Local mode: until Microsoft sign-in is configured, every request acts as a super admin so the app stays
  usable on a laptop. The UI shows that sign-in is off. With READ_ONLY_WITHOUT_SIGNIN=true (the hosted demo),
  local mode can only read: every change is refused, so nobody can alter data on an open server.
"""

from dataclasses import dataclass, field

from rest_framework.authentication import BaseAuthentication, SessionAuthentication
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import BasePermission

from . import settings_store
from .appconf import auth_enabled, read_only_without_signin
from .models import ROLE_LABELS, ROLES, Account


@dataclass
class CurrentUser:
    id: int | None
    email: str
    name: str | None
    role: str
    can_switch_accounts: bool
    dev: bool = False  # True in local mode (sign-in not configured)
    _accounts: list[int] | None = field(default=None, repr=False)

    is_authenticated = True  # for DRF

    def at_least(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)

    @property
    def switcher(self) -> bool:
        return self.at_least("admin") or self.can_switch_accounts

    @classmethod
    def from_user(cls, u) -> "CurrentUser":
        return cls(id=u.id, email=u.email, name=u.name, role=u.role, can_switch_accounts=u.can_switch_accounts)


def dev_user() -> CurrentUser:
    # A fresh object per request: the allowed-accounts cache must not outlive the request
    return CurrentUser(id=None, email="local-dev", name="Local dev (sign-in off)", role="superadmin", can_switch_accounts=True, dev=True)


def current_from_request(request) -> CurrentUser | None:
    """For plain Django views (not DRF)."""
    if not auth_enabled():
        return dev_user()
    u = getattr(request, "user", None)
    if u is None or not u.is_authenticated or not u.is_active:
        return None
    return CurrentUser.from_user(u)


class AppAuthentication(BaseAuthentication):
    """DRF authentication: local mode acts as admin; otherwise the Django session user, with CSRF enforced."""

    def authenticate(self, request):
        if not auth_enabled():
            return (dev_user(), None)
        u = getattr(request._request, "user", None)
        if u is None or not u.is_authenticated or not u.is_active:
            return None
        SessionAuthentication().enforce_csrf(request)  # blocks forged cross-site requests
        return (CurrentUser.from_user(u), None)

    def authenticate_header(self, request):
        return "Session"  # makes DRF answer 401 (not 403) when nobody is signed in


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class IsSignedIn(BasePermission):
    message = "Please sign in"

    def has_permission(self, request, view) -> bool:
        if not isinstance(request.user, CurrentUser):
            return False
        # An open (no sign-in) hosted server is read-only: no changes without a signed-in person
        if request.user.dev and read_only_without_signin() and request.method not in SAFE_METHODS:
            self.message = "This demo is read-only: sign-in is off, so changes aren't allowed"
            return False
        return True


def require(user: CurrentUser, role: str) -> None:
    if not user.at_least(role):
        raise PermissionDenied(f"This needs the {ROLE_LABELS[role]} role or higher")


# ------------------------------------------------------------------ account access


def default_account_id() -> int | None:
    configured = settings_store.get("access").get("default_account_id")
    if configured and Account.objects.filter(id=configured).exists():
        return configured
    first_company = Account.objects.filter(kind="company").order_by("id").values_list("id", flat=True).first()
    return first_company or Account.objects.order_by("id").values_list("id", flat=True).first()


def allowed_account_ids(user: CurrentUser) -> list[int]:
    if user._accounts is None:
        if user.switcher:
            user._accounts = list(Account.objects.order_by("id").values_list("id", flat=True))
        else:
            d = default_account_id()
            user._accounts = [d] if d else []
    return user._accounts


def check_account(user: CurrentUser, account_id: int) -> None:
    if account_id not in allowed_account_ids(user):
        # 404 rather than 403, so people can't probe which accounts exist
        raise NotFound("Account not found")
