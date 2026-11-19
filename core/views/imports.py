"""CSV import. One row = one post + one snapshot. Rows for the same post_url update that post and
add/replace the snapshot for the given checkpoint. Each row is saved on its own, so one bad row
doesn't block the rest."""

import csv
import io
from datetime import date, datetime

from django.db import transaction
from django.http import HttpResponse
from pydantic import ValidationError
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .. import linkedin_export
from ..api import fail, int_param
from ..auth import check_account, require
from ..models import CHECKPOINT_HOURS, Account, Post, Snapshot
from ..schemas import METRIC_FIELDS, PostIn, SnapshotIn
from ..timeutil import now_utc

POST_COLUMNS = ["post_url", "published_at_uae", "format", "topic", "industry", "keywords", "cta", "hashtags", "author", "caption"]
SNAPSHOT_COLUMNS = ["checkpoint", "captured_at_uae"] + METRIC_FIELDS
TEMPLATE_COLUMNS = POST_COLUMNS + SNAPSHOT_COLUMNS


def _split(v: str, on_spaces: bool = False) -> list[str]:
    """Split on ';' or ','. Hashtags may also be space-separated; keywords may not
    (a keyword such as 'inventory management' contains a space)."""
    v = v.replace(",", ";")
    if on_spaces:
        v = v.replace(" ", ";")
    return [x.strip() for x in v.split(";") if x.strip()]


def _int(v: str | None) -> int | None:
    v = (v or "").strip().replace(",", "")
    return int(float(v)) if v else None


def _dt(v: str) -> datetime:
    v = v.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            pass
    raise ValueError(f"Unrecognised date '{v}'. Use YYYY-MM-DD HH:MM (UAE time).")


@api_view(["GET"])
def template(request):
    example = {
        "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:0000000000000000000/",
        "published_at_uae": "2026-11-10 10:30",
        "format": "carousel",
        "topic": "ERP challenges",
        "industry": "Trading",
        "keywords": "erp;inventory",
        "cta": "Comment ERP for the checklist",
        "hashtags": "#erp #uae",
        "author": "AVAIBE",
        "caption": "",
        "checkpoint": "24h",
        "captured_at_uae": "2026-11-11 10:30",
    }
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=TEMPLATE_COLUMNS)
    w.writeheader()
    w.writerow({c: example.get(c, "") for c in TEMPLATE_COLUMNS})
    resp = HttpResponse(buf.getvalue(), content_type="text/csv")
    resp["Content-Disposition"] = "attachment; filename=linkedin_posts_template.csv"
    return resp


def _import_row(row: dict, account_id: int) -> tuple[str, bool]:
    """Returns ('created'|'updated', snapshot_saved). Raises ValueError/ValidationError/KeyError on bad rows."""
    post_in = PostIn(
        account_id=account_id,
        post_url=row.get("post_url") or None,
        published_at=_dt(row["published_at_uae"]),
        format=row["format"].strip().lower(),
        topic=row.get("topic"),
        industry=row.get("industry"),
        keywords=_split(row.get("keywords", "")),
        cta=row.get("cta"),
        hashtags=_split(row.get("hashtags", ""), on_spaces=True),
        author=row.get("author"),
        caption=row.get("caption") or None,
    )
    post = Post.objects.filter(post_url=post_in.post_url).first() if post_in.post_url else None
    if post and post.account_id != account_id:
        raise ValueError("post_url belongs to a different account")
    data = post_in.model_dump()
    data["time_known"] = True  # the CSV template always carries a time
    if post is None:
        post = Post.objects.create(**data)
        outcome = "created"
    else:
        for k, v in data.items():
            setattr(post, k, v)
        post.save()
        outcome = "updated"

    cp = row.get("checkpoint", "").strip()
    if not cp:
        return outcome, False
    if cp not in CHECKPOINT_HOURS:
        raise ValueError(f"checkpoint must be one of {list(CHECKPOINT_HOURS)}")
    snap_in = SnapshotIn(
        captured_at=_dt(row["captured_at_uae"]) if row.get("captured_at_uae") else None,
        **{f: _int(row.get(f)) for f in METRIC_FIELDS},
    )
    data = snap_in.model_dump()
    data["captured_at"] = data["captured_at"] or now_utc()
    Snapshot.objects.update_or_create(post=post, checkpoint=cp, defaults=data)
    return outcome, True


@api_view(["POST"])
def import_linkedin_export(request):
    """One or more files exported from LinkedIn page analytics (content, followers, visitors).
    Optional exported_on (YYYY-MM-DD); otherwise read from LinkedIn's file name, else today."""
    user = request.user
    require(user, "editor")
    account_id = int_param(request, "account_id", required=True)
    account = Account.objects.filter(id=account_id).first()
    if account is None:
        fail(404, "Account not found")
    check_account(user, account_id)
    uploads = request.FILES.getlist("files")
    if not uploads:
        fail(422, "Attach at least one exported file")
    exported_on = None
    if request.data.get("exported_on"):
        try:
            exported_on = date.fromisoformat(str(request.data["exported_on"]))
        except ValueError:
            fail(422, "exported_on must be a date (YYYY-MM-DD)")
    results = []
    for f in uploads:
        if f.size > 20 * 1024 * 1024:
            results.append({"file": f.name, "kind": None, "errors": ["File is larger than 20 MB"]})
            continue
        try:
            results.append(linkedin_export.import_file(account, f.name, f.read(), exported_on))
        except linkedin_export.ExportError as e:
            results.append({"file": f.name, "kind": None, "errors": [str(e)]})
    return Response({"account": account.name, "account_kind": account.kind, "files": results})


@api_view(["POST"])
def import_csv(request):
    user = request.user
    require(user, "editor")
    account_id = int_param(request, "account_id", required=True)
    if not Account.objects.filter(id=account_id).exists():
        fail(404, "Account not found")
    check_account(user, account_id)
    upload = request.FILES.get("file")
    if upload is None:
        fail(422, "Attach a CSV file")
    raw = upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    missing = {"published_at_uae", "format"} - set(reader.fieldnames or [])
    if missing:
        fail(400, f"Missing required columns: {sorted(missing)}")

    created = updated = snapshots = 0
    errors: list[dict] = []
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        row = {k: (v or "") for k, v in row.items() if k}
        try:
            with transaction.atomic():  # a failing row leaves nothing half-saved
                outcome, snap_saved = _import_row(row, account_id)
        except (ValueError, ValidationError, KeyError) as e:
            errors.append({"row": i, "error": str(e)})
            continue
        created += outcome == "created"
        updated += outcome == "updated"
        snapshots += snap_saved
    return Response({"posts_created": created, "posts_updated": updated, "snapshots_saved": snapshots, "errors": errors})
