from __future__ import annotations

from html import escape
from pathlib import Path

from .core import Contact, Skip, drafts


def write_preview(report: dict, config: dict, path: Path, demo: bool = False):
    cards = []
    for row in report["contacts"]:
        contact = Contact(**row["data"])
        try:
            messages = drafts(contact, config)
        except Skip as exc:
            messages = {"draft unavailable": str(exc)}
        cards.append(
            '<article><div class="meta">' + escape(row["state"]) + f' · Match score {row["score"]}</div>'
            + '<h2>' + escape(contact.name) + '</h2><p>' + escape(contact.headline) + '</p><p>'
            + escape(contact.company + ' · ' + contact.location) + '</p><p class="muted">'
            + escape(row["reason"]) + '</p>'
            + ''.join('<h3>' + escape(kind.title()) + '</h3><p class="note">' + escape(text) + '</p>' for kind, text in messages.items())
            + ('' if demo else '<a href="' + escape(contact.url, quote=True) + '" rel="noopener noreferrer">View profile</a>')
            + '</article>'
        )
    title = "Fictional outreach examples" if demo else "Outreach preview"
    explanation = ("All contacts below are fictional examples. No LinkedIn session was used and nothing was sent."
                   if demo else "Drafts and recorded state only. This page cannot send messages. Lower scores indicate unconfirmed HR/TA hiring remit.")
    html = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outreach preview</title><style>
body{font:16px/1.6 system-ui,sans-serif;background:#f4f6f8;color:#182b3a;margin:0}
main{max-width:900px;margin:40px auto;padding:0 24px}h1{line-height:1.2}h2{margin-bottom:0}
article{background:white;padding:28px;margin:24px 0;border:1px solid #d9e0e5;border-radius:14px}
.meta,.muted{color:#526575}.meta{font-size:13px}.note{background:#f0f5f8;padding:16px;border-left:3px solid #3173a3}
h3{font-size:14px;margin-bottom:8px}a{color:#155d94}header{padding:12px 0}p{overflow-wrap:anywhere}
</style><main><header><h1>""" + title + '</h1><p>' + explanation + '</p><p><strong>IT talent acquisition · Pune / Mumbai / Bengaluru / Jammu</strong></p></header>'
    html += ''.join(cards) or '<p>No contacts yet. Run discovery to populate this preview.</p>'
    html += '</main></html>'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return path
