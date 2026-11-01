from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Halt(RuntimeError):
    """Stop the account workflow instead of guessing or retrying a send."""


class Skip(RuntimeError):
    """This contact is not eligible for the proposed action."""


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.hostname not in {"linkedin.com", "www.linkedin.com", "in.linkedin.com"}:
        raise ValueError("Expected an HTTPS LinkedIn profile URL")
    if parts.username or parts.password or parts.port:
        raise ValueError("Unexpected credentials or port in profile URL")
    path = unquote(parts.path).rstrip("/")
    if not re.fullmatch(r"/in/[\w%-]+", path, flags=re.UNICODE):
        raise ValueError("Expected /in/<profile> URL")
    return "https://www.linkedin.com" + path + "/"


def compact(value: str) -> str:
    return " ".join(value.split())


def contains(text: str, term: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I))


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(
            f"Configuration file not found: {path}. Copy settings.example.json "
            "to settings.json and replace the fictional candidate details."
        )
    config = json.loads(path.read_text())
    for key in ("name", "experience", "specialty", "evidence"):
        value = config["candidate"][key]
        if not isinstance(value, str) or not compact(value):
            raise ValueError(f"candidate.{key} must be nonempty text")
    for key in ("locations", "companies", "search_roles"):
        if not isinstance(config[key], list) or not config[key]:
            raise ValueError(f"{key} must be a nonempty list")
    for key in ("locations", "search_roles"):
        if any(not isinstance(item, str) or not compact(item) for item in config[key]):
            raise ValueError(f"{key} must contain nonempty strings")
    for company in config["companies"]:
        if not isinstance(company.get("name"), str) or not company["name"].strip():
            raise ValueError("Every company needs a name")
        if not company.get("aliases") or any(not isinstance(a, str) or not a.strip() for a in company["aliases"]):
            raise ValueError("Every company needs nonempty aliases")
    for key in ("max_searches_per_cycle", "max_profiles_per_cycle", "max_sends_per_cycle", "max_sends_per_24h", "invitation_character_limit"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("seconds_between_actions", "cycle_interval_seconds"):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 1:
            raise ValueError(f"{key} must be a finite number >= 1")
    if config.get("browser_channel") not in ("chrome", "chromium"):
        raise ValueError("browser_channel must be chrome or chromium")
    return config


@dataclass
class Contact:
    url: str
    name: str
    headline: str
    company: str
    location: str
    evidence: str
    relationship: str = "unknown"

    def __post_init__(self):
        self.url = canonical_url(self.url)
        for field in ("name", "headline", "company", "location", "evidence"):
            setattr(self, field, compact(getattr(self, field)))


def qualify(contact: Contact, config: dict) -> tuple[int, str]:
    if not contact.name or contact.name.casefold() == "linkedin member":
        return 0, "No usable name"
    if not any(contact.company.casefold() == c["name"].casefold() for c in config["companies"]):
        return 0, "Current company not verified against target employers"
    aliases = {"Bengaluru": ["Bengaluru", "Bangalore"]}
    if not any(contains(contact.location, term) for city in config["locations"] for term in aliases.get(city, [city])):
        return 0, "No verified target-city location or recruiting remit"
    title = contact.headline
    if not re.search(r"\btalent acquisition\b|\brecruit(?:er|ment|ing)\b|\bHR\b|\bhuman resources\b", title, re.I):
        return 0, "No relevant recruiting or HR role"
    score, reason = 40, "Recruiting/HR contact at target employer; HR/TA hiring remit unconfirmed"
    if re.search(r"\b(?:manager|head|director|lead|leader)\b", title, re.I):
        score, reason = 70, "Potential HR/TA team hiring decision-maker"
    if re.search(r"(?:hiring|recruiting)\s+(?:for\s+)?(?:HR|recruiters|talent acquisition)\b", contact.evidence, re.I):
        score, reason = 100, "Profile explicitly mentions recruiting HR/TA professionals"
    return score, reason


def drafts(contact: Contact, config: dict) -> dict[str, str]:
    person = config["candidate"]
    first = contact.name.split()[0]
    invitation = (
        f"Hi {first}, I'm {person['name']}, with {person['experience']} in {person['specialty']}. "
        f"I'm exploring TA roles at {contact.company} and would like to connect."
    )
    if len(invitation) > config["invitation_character_limit"]:
        invitation = (
            f"Hi {first}, I have {person['experience']} in {person['specialty']} "
            f"and am exploring TA roles at {contact.company}. I'd like to connect."
        )
    if len(invitation) > config["invitation_character_limit"]:
        raise Skip("Personalized invitation exceeds configured character limit")
    introduction = (
        f"Hi {first}, thanks for connecting. I'm exploring IT talent acquisition opportunities "
        f"at {contact.company}. I have {person['experience']} of TA/HR experience, including "
        f"{person['evidence']}. My preferred locations are {', '.join(config['locations'])}. "
        "Do you handle hiring for TA positions, or could you point me to the relevant colleague? "
        f"Thanks, {person['name']}."
    )
    return {"invitation": invitation, "introduction": introduction}


def queries(config: dict) -> list[str]:
    # Interleave cities before revisiting the same company; a stored cursor resumes later.
    locations = [alias for city in config["locations"] for alias in ([city, "Bangalore"] if city == "Bengaluru" else [city])]
    return [f'"{role}" "{company["name"]}" "{city}"'
            for role in config["search_roles"]
            for company in config["companies"]
            for city in locations]


def introduction_allowed(events: list[tuple[str, str]], candidate_name: str, invitation: str | None) -> bool:
    """Only the exact recorded invitation, attributed to the candidate, may precede an introduction."""
    if not events:
        return True
    return bool(invitation) and len(events) == 1 and (
        compact(events[0][0]).casefold() == compact(candidate_name).casefold()
        and compact(events[0][1]) == compact(invitation)
    )


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=10)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS contacts (
                url TEXT PRIMARY KEY, data TEXT NOT NULL, score INTEGER NOT NULL,
                reason TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'qualified',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY, url TEXT NOT NULL, kind TEXT NOT NULL,
                text TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(url, kind), CHECK(kind IN ('invitation', 'introduction'))
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def upsert(self, contact: Contact, score: int, reason: str):
        with self.db:
            self.db.execute("""INSERT INTO contacts(url,data,score,reason) VALUES(?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET data=excluded.data, score=excluded.score,
                reason=excluded.reason, updated_at=CURRENT_TIMESTAMP""",
                (contact.url, json.dumps(asdict(contact), ensure_ascii=False), score, reason))

    def contacts(self):
        return self.db.execute("SELECT * FROM contacts ORDER BY score DESC, updated_at, url").fetchall()

    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def state(self, url: str, state: str):
        with self.db:
            self.db.execute("UPDATE contacts SET state=?, updated_at=CURRENT_TIMESTAMP WHERE url=?", (state, url))

    def action(self, url: str, kind: str):
        return self.db.execute("SELECT * FROM actions WHERE url=? AND kind=?", (url, kind)).fetchone()

    def begin(self, url: str, kind: str, text: str) -> int | None:
        # Persist before clicking. A crash must never make the send eligible again.
        try:
            with self.db:
                cur = self.db.execute("INSERT INTO actions(url,kind,text,status) VALUES(?,?,?,'attempting')", (url, kind, text))
                self.db.execute("UPDATE contacts SET state='needs_review' WHERE url=?", (url,))
                return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def finish(self, action_id: int, status: str, detail: str = ""):
        with self.db:
            self.db.execute("UPDATE actions SET status=?,detail=? WHERE id=?", (status, detail, action_id))
            action = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            state = ("invitation_pending" if action["kind"] == "invitation" else "introduction_sent") if status == "sent" else "needs_review"
            self.db.execute("UPDATE contacts SET state=? WHERE url=?", (state, action["url"]))

    def unresolved(self) -> bool:
        return bool(self.db.execute("SELECT 1 FROM actions WHERE status IN ('attempting','uncertain') LIMIT 1").fetchone())

    def recent_attempts(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM actions WHERE created_at >= datetime('now', '-24 hours')").fetchone()[0]

    def resolve(self, action_id: int, outcome: str):
        if outcome not in {"sent", "abandoned"}:
            raise ValueError("Resolution must be sent or abandoned")
        action = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not action or action["status"] not in {"attempting", "uncertain"}:
            raise ValueError("Action is not awaiting review")
        self.finish(action_id, outcome, "Manually reconciled by user")
        if outcome == "abandoned":
            self.state(action["url"], "stopped")

    def report(self) -> dict:
        return {
            "paused": self.get("paused") or None,
            "contacts": [{**dict(row), "data": json.loads(row["data"])} for row in self.contacts()],
            "actions": [dict(row) for row in self.db.execute("SELECT * FROM actions ORDER BY id")],
        }


def execute_action(store: Store, browser, contact: Contact, kind: str, text: str, check_stop):
    # Preparation may open a dialog but must not send. Persist intent immediately
    # before the final click, and never automatically retry any attempted action.
    check_stop()
    browser.prepare(contact, kind, text)
    check_stop()
    action_id = store.begin(contact.url, kind, text)
    if action_id is None:
        raise Skip("Action already recorded; duplicate suppressed")
    try:
        check_stop()
        browser.send(contact, kind, text)
    except BaseException as exc:
        store.finish(action_id, "uncertain", type(exc).__name__)
        store.set("paused", "Uncertain send result; reconcile the action before resuming")
        raise
    store.finish(action_id, "sent")
