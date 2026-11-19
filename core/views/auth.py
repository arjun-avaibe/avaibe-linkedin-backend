"""Microsoft sign-in endpoints: /api/auth/me, /login, /callback, /logout."""

import logging
import os
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from ..appconf import ADMIN_EMAILS, FRONTEND_URL, MS_REDIRECT_URI, auth_enabled
from ..auth import allowed_account_ids, current_from_request, default_account_id
from ..models import AuthFlow, User
from ..timeutil import as_utc, now_utc

log = logging.getLogger("auth")

FLOW_TTL = timedelta(minutes=10)
SCOPES = ["User.Read"]  # openid/profile/offline_access are added by MSAL automatically


def msal_app():
    """Separated so tests can replace it with a fake."""
    import msal

    return msal.ConfidentialClientApplication(
        os.getenv("MS_CLIENT_ID", ""),
        authority=f"https://login.microsoftonline.com/{os.getenv('MS_TENANT_ID', '')}",
        client_credential=os.getenv("MS_CLIENT_SECRET", ""),
    )


def _to_signin(**params: str) -> HttpResponseRedirect:
    return HttpResponseRedirect(f"{FRONTEND_URL}/signin?{urlencode(params)}")


def _safe_next(value: str | None) -> str:
    """Only allow returning to the Django admin after sign-in; everything else goes to the app."""
    if value and value.startswith("/admin") and "//" not in value:
        return value
    return ""


@require_GET
@ensure_csrf_cookie  # gives the React app the csrftoken cookie it echoes back on writes
def me(request):
    user = current_from_request(request)
    if user is None:
        return JsonResponse({"enabled": True, "user": None})
    return JsonResponse(
        {
            "enabled": auth_enabled(),
            "user": {
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "can_switch_accounts": user.switcher,
                "dev": user.dev,
            },
            "allowed_account_ids": allowed_account_ids(user),
            "default_account_id": default_account_id(),
        }
    )


@require_GET
def login(request):
    if not auth_enabled():
        return HttpResponseRedirect(FRONTEND_URL)
    AuthFlow.objects.filter(created_at__lt=now_utc() - FLOW_TTL).delete()
    flow = msal_app().initiate_auth_code_flow(SCOPES, redirect_uri=MS_REDIRECT_URI)
    flow["_next"] = _safe_next(request.GET.get("next"))
    AuthFlow.objects.create(state=flow["state"], flow=flow, created_at=now_utc())
    return HttpResponseRedirect(flow["auth_uri"])


@require_GET
def callback(request):
    if not auth_enabled():
        return HttpResponseRedirect(FRONTEND_URL)
    params = request.GET.dict()
    if params.get("error"):
        return _to_signin(error="microsoft", message=params.get("error_description", params["error"])[:300])

    row = AuthFlow.objects.filter(state=params.get("state", "")).first()
    if row is None or as_utc(row.created_at) < now_utc() - FLOW_TTL:
        return _to_signin(error="expired")
    flow = row.flow
    row.delete()  # single use
    next_url = flow.pop("_next", "")

    try:
        result = msal_app().acquire_token_by_auth_code_flow(flow, params)
    except ValueError:  # state/nonce mismatch: MSAL rejects tampered or replayed callbacks
        return _to_signin(error="invalid")
    if "error" in result:
        log.warning("Microsoft sign-in failed: %s", result.get("error"))
        return _to_signin(error="microsoft", message=str(result.get("error_description", result["error"]))[:300])

    claims = result.get("id_token_claims") or {}
    if claims.get("tid") != os.getenv("MS_TENANT_ID"):
        return _to_signin(error="tenant")
    email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if not email:
        return _to_signin(error="no_email")

    user = User.objects.filter(email__iexact=email).first()
    if user is None and email in ADMIN_EMAILS:
        user = User.objects.create_user(email, name=claims.get("name"), role="admin", can_switch_accounts=True)
    if user is None:
        return _to_signin(error="not_invited", email=email)
    if not user.is_active:
        return _to_signin(error="disabled", email=email)
    if claims.get("name"):
        user.name = claims["name"]
        user.save(update_fields=["name"])

    # Starts a fresh session (new session id) and records last_login
    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponseRedirect(next_url or FRONTEND_URL)


@require_POST
def logout(request):
    """CSRF-protected by Django's middleware (the React app sends the X-CSRFToken header)."""
    django_logout(request)
    return JsonResponse({"signed_out": True})


def health(request):
    return JsonResponse({"status": "ok"})
