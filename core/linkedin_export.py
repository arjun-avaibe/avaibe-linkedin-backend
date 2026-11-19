"""Read LinkedIn page-analytics exports (.xls or .xlsx) and load them into the app.

LinkedIn's company-page Analytics lets an admin export three files:
- Content:   sheet "Metrics" (page totals per day) and "All posts" (one row per post, lifetime totals)
- Followers: sheet "New followers" (per day) plus demographics (Location, Job function, ...)
- Visitors:  sheet "Visitor metrics" (page views per day) plus demographics

What the files do NOT contain, and how that is handled:
- A post's time of day: only the date. Posts are stored as time_known=False, at midday UAE on that
  date, and kept out of the hour-by-hour heatmap.
- Snapshots at fixed ages: each post has one total "as of the export". It is stored as the latest
  checkpoint the post had reached on the export date (7d if 7+ days old), with a note saying so.
- Topic and CTA: suggested from the caption (core.topics), flagged until someone confirms them.
- The format, except for articles and videos: other posts come in as "untagged".

Re-importing a newer export updates the same posts (matched by post link). It never overwrites a
snapshot someone typed in by hand, or a format/topic/time someone has set.
"""

import io
import re
from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Any

from django.db import transaction

from . import topics
from .models import PAGE_DAILY_FIELDS, Account, PageAudience, PageDaily, Post, Snapshot
from .timeutil import from_uae_local

EXPORT_NOTE = "From LinkedIn export"

# LinkedIn's "Content Type" -> our format. Blank means LinkedIn didn't say (images, text, documents...).
CONTENT_TYPES = {
    "article": "newsletter",
    "newsletter": "newsletter",
    "video": "video",
    "image": "image",
    "document": "carousel",
    "carousel": "carousel",
    "poll": "poll",
    "text": "text",
}

DIMENSION_SHEETS = {
    "Location": "location",
    "Job function": "job_function",
    "Seniority": "seniority",
    "Industry": "industry",
    "Company size": "company_size",
}


class ExportError(ValueError):
    pass


# ---------------------------------------------------------------- reading the workbook


def read_workbook(data: bytes) -> dict[str, list[list[Any]]]:
    """Sheet name -> rows (lists of cell values). Accepts the old .xls format LinkedIn uses and .xlsx."""
    if data[:4] == b"\xd0\xcf\x11\xe0":
        import xlrd

        try:
            book = xlrd.open_workbook(file_contents=data)
        except Exception as e:  # xlrd raises several unrelated types
            raise ExportError(f"Couldn't read this .xls file ({e})") from e
        out = {}
        for sh in book.sheets():
            rows = []
            for i in range(sh.nrows):
                row = []
                for c in sh.row(i):
                    if c.ctype == xlrd.XL_CELL_DATE:
                        row.append(xlrd.xldate.xldate_as_datetime(c.value, book.datemode))
                    else:
                        row.append(c.value)
                rows.append(row)
            out[sh.name] = rows
        return out
    if data[:2] == b"PK":
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:
            raise ExportError(f"Couldn't read this .xlsx file ({e})") from e
        return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
    raise ExportError("Not an Excel file. Upload the .xls or .xlsx file exactly as LinkedIn exported it.")


def detect_kind(sheets: dict[str, list[list[Any]]]) -> str:
    names = set(sheets)
    if "All posts" in names:
        return "content"
    if "New followers" in names:
        return "followers"
    if "Visitor metrics" in names:
        return "visitors"
    if "COMPETITORS" in names or any("competitor" in n.lower() for n in names):
        return "competitors"
    raise ExportError(f"Not a LinkedIn page export this app knows (sheets: {', '.join(sorted(names))})")


def _table(rows: list[list[Any]], first_col: str) -> list[dict[str, Any]]:
    """Rows as dicts, using the first row (within the top five) whose first cell is `first_col` as header.
    LinkedIn puts a one-line description above the header on some sheets."""
    for i, row in enumerate(rows[:5]):
        if row and str(row[0]).strip() == first_col:
            header = [str(h).strip() if h is not None else "" for h in row]
            out = []
            for r in rows[i + 1 :]:
                if not r or all(v in (None, "") for v in r):
                    continue
                out.append({header[j]: (r[j] if j < len(r) else None) for j in range(len(header)) if header[j]})
            return out
    raise ExportError(f"Couldn't find the header row (expected a column called '{first_col}')")


def _date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ExportError(f"Unrecognised date '{s}'")


def _int(v: Any) -> int | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return int(round(float(str(v).replace(",", ""))))
    except ValueError:
        return None


def export_date_from_filename(name: str) -> date | None:
    """LinkedIn names files like 'avaibe_content_1794812594179.xls': a millisecond timestamp of the export."""
    m = re.search(r"(\d{13})", name or "")
    if not m:
        return None
    try:
        d = datetime.fromtimestamp(int(m.group(1)) / 1000).date()
    except (OverflowError, OSError, ValueError):
        return None
    return d if date(2015, 1, 1) <= d <= date(2100, 1, 1) else None


# ---------------------------------------------------------------- importing


def _checkpoint_for_age(days: int) -> str | None:
    if days >= 7:
        return "7d"
    if days >= 2:
        return "48h"
    if days >= 1:
        return "24h"
    return None


def _upsert_daily(account: Account, by_day: dict[date, dict[str, int | None]]) -> int:
    for d, values in by_day.items():
        values = {k: v for k, v in values.items() if k in PAGE_DAILY_FIELDS}
        PageDaily.objects.update_or_create(account=account, date=d, defaults=values)
    return len(by_day)


def _import_posts(account: Account, rows: Iterable[dict[str, Any]], exported_on: date) -> dict[str, Any]:
    created = updated = snapshots = kept_manual = too_new = 0
    errors: list[str] = []
    captured_at = from_uae_local(datetime.combine(exported_on, time(12, 0)))
    for i, r in enumerate(rows, start=1):
        link = str(r.get("Post link") or "").strip()
        if not link:
            errors.append(f"Post {i}: no link, skipped")
            continue
        try:
            posted = _date(r.get("Created date"))
        except ExportError as e:
            errors.append(f"Post {i}: {e}")
            continue
        caption = str(r.get("Post title") or "").strip() or None
        content_type = str(r.get("Content Type") or "").strip().lower()
        fmt = CONTENT_TYPES.get(content_type, "untagged")
        urn = re.search(r"urn:li:(?:activity|share|ugcPost):(\d+)", link)
        audience = str(r.get("Audience") or "").strip()

        post = Post.objects.filter(post_url=link).first()
        if post and post.account_id != account.id:
            errors.append(f"Post {i}: this link is already stored under another account, skipped")
            continue
        if post is None:
            s = topics.suggest(caption or "")
            post = Post.objects.create(
                account=account,
                post_url=link,
                linkedin_post_id=urn.group(1) if urn else None,
                author=str(r.get("Posted by") or "").strip() or None,
                # Midday UAE on the posting date: the export has no time, so time_known=False
                published_at=from_uae_local(datetime.combine(posted, time(12, 0))),
                time_known=False,
                format=fmt,
                topic=s["topic"],
                topic_suggested=s["topic"] is not None or s["cta"] is not None,
                industry=s["industry"],
                cta=s["cta"],
                hashtags=[h.lower() for h in dict.fromkeys(topics.hashtags(caption or ""))],
                caption=caption,
                notes=f"{EXPORT_NOTE}. LinkedIn audience setting: {audience}." if audience else f"{EXPORT_NOTE}.",
            )
            created += 1
        else:
            # Fill gaps only: never undo what someone corrected in the app
            changed = False
            if post.format == "untagged" and fmt != "untagged":
                post.format, changed = fmt, True
            if not post.caption and caption:
                post.caption, changed = caption, True
            if changed:
                post.save()
            updated += 1

        cp = _checkpoint_for_age((exported_on - posted).days)
        if cp is None:
            too_new += 1
            continue
        existing = Snapshot.objects.filter(post=post, checkpoint=cp).first()
        if existing and not (existing.notes or "").startswith(EXPORT_NOTE):
            kept_manual += 1
            continue
        views = _int(r.get("Views"))
        metrics = {
            "impressions": _int(r.get("Impressions")),
            "clicks": _int(r.get("Clicks")),
            "reactions": _int(r.get("Likes")),
            "comments": _int(r.get("Comments")),
            "reposts": _int(r.get("Reposts")),
            "followers_gained": _int(r.get("Follows")),
            "video_views": views if fmt == "video" else None,
            "newsletter_views": views if fmt == "newsletter" else None,
        }
        note = (
            f"{EXPORT_NOTE} of {exported_on:%d %b %Y}: totals since posting "
            f"({(exported_on - posted).days} days), stored as the {cp} figure."
        )
        Snapshot.objects.update_or_create(post=post, checkpoint=cp, defaults={**metrics, "captured_at": captured_at, "notes": note})
        snapshots += 1
    return {
        "posts_created": created,
        "posts_updated": updated,
        "snapshots_saved": snapshots,
        "snapshots_kept_manual": kept_manual,
        "posts_too_new": too_new,
        "errors": errors,
    }


def _import_demographics(account: Account, sheets: dict[str, list[list[Any]]], audience: str, count_col: str, as_of: date) -> dict[str, Any]:
    found: dict[str, list[tuple[str, int]]] = {}
    for sheet, dim in DIMENSION_SHEETS.items():
        if sheet not in sheets:
            continue
        rows = _table(sheets[sheet], sheet)
        vals: dict[str, int] = {}
        for r in rows:
            name = str(r.get(sheet) or "").strip()[:200]
            n = _int(r.get(count_col))
            if name and n is not None:
                vals[name] = vals.get(name, 0) + n  # LinkedIn occasionally repeats a value
        found[dim] = list(vals.items())
    if not found:
        return {"demographic_rows": 0}
    # The export has no overall total. Use the largest dimension total: complete lists such as
    # seniority add up to (nearly) everyone, while location lists only the top 100 places.
    total = max(sum(n for _, n in vals) for vals in found.values())
    PageAudience.objects.filter(account=account, as_of=as_of, audience=audience).delete()
    PageAudience.objects.bulk_create(
        [
            PageAudience(account=account, as_of=as_of, audience=audience, total=total, dimension=dim, value=v, count=n)
            for dim, vals in found.items()
            for v, n in vals
        ]
    )
    return {"demographic_rows": sum(len(v) for v in found.values()), "demographic_total": total, "as_of": as_of.isoformat()}


def import_file(account: Account, filename: str, data: bytes, exported_on: date | None) -> dict[str, Any]:
    """Import one exported file. Everything in the file is saved together, or nothing is."""
    sheets = read_workbook(data)
    kind = detect_kind(sheets)
    as_of = exported_on or export_date_from_filename(filename) or date.today()
    result: dict[str, Any] = {"file": filename, "kind": kind, "exported_on": as_of.isoformat(), "errors": []}
    if kind == "competitors":
        has_rows = any(len(rows) > 1 for rows in sheets.values())
        result["skipped"] = (
            "Competitor analytics aren't imported yet."
            if has_rows
            else "This competitor file is empty (it only has a date). Export it again from LinkedIn with competitors added."
        )
        return result

    with transaction.atomic():
        if kind == "content":
            if "Metrics" in sheets:
                by_day = {}
                for r in _table(sheets["Metrics"], "Date"):
                    by_day[_date(r["Date"])] = {
                        "impressions": _int(r.get("Impressions (total)")),
                        "unique_impressions": _int(r.get("Unique impressions (organic)")),
                        "clicks": _int(r.get("Clicks (total)")),
                        "reactions": _int(r.get("Reactions (total)")),
                        "comments": _int(r.get("Comments (total)")),
                        "reposts": _int(r.get("Reposts (total)")),
                    }
                result["days_saved"] = _upsert_daily(account, by_day)
            result.update(_import_posts(account, _table(sheets["All posts"], "Post title"), as_of))
        elif kind == "followers":
            by_day = {_date(r["Date"]): {"new_followers": _int(r.get("Total followers"))} for r in _table(sheets["New followers"], "Date")}
            result["days_saved"] = _upsert_daily(account, by_day)
            result.update(_import_demographics(account, sheets, "followers", "Total followers", as_of))
        elif kind == "visitors":
            by_day = {
                _date(r["Date"]): {
                    "page_views": _int(r.get("Total page views (total)")),
                    "unique_visitors": _int(r.get("Total unique visitors (total)")),
                }
                for r in _table(sheets["Visitor metrics"], "Date")
            }
            result["days_saved"] = _upsert_daily(account, by_day)
            # Visitor demographics count page VIEWS over the export period, not people
            result.update(_import_demographics(account, sheets, "visitors", "Total views", as_of))
    return result
