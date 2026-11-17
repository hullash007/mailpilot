"""Basic Phase 1 inbox dashboard, served by FastAPI.

Deliberately dependency-free (no frontend build step) for Phase 1. The
React + Vite dashboard from the project plan arrives with Phase 2+.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import get_db
from .services.access import require_access
from .models import Email, Thread

router = APIRouter(dependencies=[Depends(require_access)])

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MailPilot - Inbox</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #f4f5f7; color: #1b1f24; }
  header { background: #0b3d91; color: #fff; padding: 14px 24px;
           display: flex; align-items: baseline; gap: 12px; }
  header h1 { font-size: 18px; margin: 0; }
  header span { font-size: 13px; opacity: .8; }
  .stats { display: flex; gap: 12px; padding: 16px 24px 0; flex-wrap: wrap; }
  .stat { background: #fff; border: 1px solid #e2e5ea; border-radius: 10px;
          padding: 12px 18px; min-width: 130px; }
  .stat b { display: block; font-size: 22px; }
  .stat span { font-size: 12px; color: #5b6470; }
  main { padding: 16px 24px 40px; }
  table { width: 100%; border-collapse: collapse; background: #fff;
          border: 1px solid #e2e5ea; border-radius: 10px; overflow: hidden; }
  th, td { text-align: left; padding: 10px 14px; font-size: 13px;
           border-bottom: 1px solid #eef0f3; vertical-align: top; }
  th { background: #fafbfc; font-size: 12px; text-transform: uppercase;
       letter-spacing: .04em; color: #5b6470; }
  tr:last-child td { border-bottom: none; }
  .sender { font-weight: 600; }
  .snippet { color: #5b6470; }
  .labels { margin-top: 4px; }
  .label { display: inline-block; background: #e8eefc; color: #0b3d91;
           border-radius: 999px; padding: 1px 8px; font-size: 11px;
           margin-right: 4px; }
  .empty { background: #fff; border: 1px dashed #c8cdd4; border-radius: 10px;
           padding: 40px; text-align: center; color: #5b6470; }
  .empty code { background: #eef0f3; padding: 2px 6px; border-radius: 4px; }
  .banner { background: #fff7e0; border: 1px solid #f0d98c; color: #6b5300;
            border-radius: 10px; padding: 10px 16px; margin: 16px 24px 0;
            font-size: 13px; }
  .label.sample { background: #fde8e8; color: #8f1d1d; font-weight: 700; }
  td, th { overflow-wrap: anywhere; }
  @media (max-width: 640px) {
    header { padding: 12px 16px; flex-wrap: wrap; }
    .stats { padding: 12px 16px 0; }
    .stat { min-width: 0; flex: 1 1 40%; padding: 10px 12px; }
    main { padding: 12px 12px 32px; }
    .banner { margin: 12px 12px 0; }
    table, thead, tbody, tr, td { display: block; width: 100%; }
    thead { position: absolute; left: -9999px; }
    table { border: none; background: transparent; }
    tr { background: #fff; border: 1px solid #e2e5ea; border-radius: 10px;
         margin-bottom: 10px; padding: 4px 0; }
    td { border-bottom: none; padding: 6px 14px; font-size: 14px; }
    td.date { color: #5b6470; font-size: 12px; padding-top: 0; }
  }
</style>
</head>
<body>
<header><h1>MailPilot</h1><span>Phase 1 - Gmail Foundation (read-only)</span></header>
<section class="stats">
  <div class="stat"><b>__EMAIL_COUNT__</b><span>emails stored</span></div>
  <div class="stat"><b>__THREAD_COUNT__</b><span>threads</span></div>
</section>
__BANNER__
<main>
__BODY__
</main>
</body>
</html>
"""

_ROW = """<tr>
  <td class="sender">{sender}{sample_badge}</td>
  <td><div>{subject}</div><div class="snippet">{snippet}</div>
      <div class="labels">{labels}</div></td>
  <td class="date">{date}</td>
</tr>"""

_SAMPLE_BADGE = ' <span class="label sample">SAMPLE</span>'
_BANNER = ('<div class="banner">Showing sample data - this is not connected to '
           'a real inbox.</div>')

_EMPTY = """<div class="empty">
<p>No emails stored yet.</p>
<p>Connect a Gmail account via <code>/auth/google/login</code>, then run
<code>POST /ingest/&lt;account_id&gt;</code> to pull mail into the database.</p>
</div>"""


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(db: Session = Depends(get_db)) -> HTMLResponse:
    email_count = db.scalar(select(func.count(Email.id))) or 0
    thread_count = db.scalar(select(func.count(Thread.id))) or 0
    rows = db.scalars(
        select(Email).order_by(Email.internal_date.desc().nulls_last()).limit(100)
    ).all()
    if rows:
        body_rows = "".join(
            _ROW.format(
                sample_badge=_SAMPLE_BADGE if getattr(e, "is_sample", False) else "",
                sender=_esc(e.sender),
                subject=_esc(e.subject) or "(no subject)",
                snippet=_esc(e.snippet),
                labels="".join(
                    f'<span class="label">{_esc(str(l))}</span>' for l in (e.label_ids or [])
                ),
                date=e.internal_date.strftime("%Y-%m-%d %H:%M") if e.internal_date else "",
            )
            for e in rows
        )
        body = (
            "<table><thead><tr><th>From</th><th>Subject</th><th>Date</th></tr></thead>"
            f"<tbody>{body_rows}</tbody></table>"
        )
    else:
        body = _EMPTY
    banner = _BANNER if any(getattr(e, "is_sample", False) for e in rows) else ""
    html = (
        _PAGE.replace("__EMAIL_COUNT__", str(email_count))
        .replace("__THREAD_COUNT__", str(thread_count))
        .replace("__BANNER__", banner)
        .replace("__BODY__", body)
    )
    return HTMLResponse(html)
