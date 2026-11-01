from __future__ import annotations

import fcntl
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .core import Contact, Halt, Skip, Store, drafts, execute_action, qualify, queries


@contextmanager
def single_runner(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Halt("Another browser workflow is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Runner:
    def __init__(self, store: Store, browser, config: dict, log=print, wait=None):
        self.store, self.browser, self.config, self.log = store, browser, config, log
        self.wait = wait or self.sleep

    def check_stop(self):
        if self.store.get("paused"):
            raise Halt(self.store.get("paused"))
        if self.store.unresolved():
            raise Halt("An earlier send is unresolved. Check status and reconcile it before continuing.")

    def check_pause(self):
        # An active attempt is necessarily unresolved during its final click.
        if self.store.get("paused"):
            raise Halt(self.store.get("paused"))

    def sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check_pause()
            time.sleep(min(0.5, max(0, end - time.monotonic())))

    def discover(self):
        self.check_stop()
        search_terms = queries(self.config)
        cursor = int(self.store.get("query_cursor", "0")) % len(search_terms)
        visited = set()
        for offset in range(min(self.config["max_searches_per_cycle"], len(search_terms))):
            self.check_stop()
            if len(visited) >= self.config["max_profiles_per_cycle"]:
                break
            index = (cursor + offset) % len(search_terms)
            self.log(f"Search: {search_terms[index]}")
            urls = self.browser.search(search_terms[index], self.config["max_profiles_per_cycle"] - len(visited))
            self.wait(self.config["seconds_between_actions"])
            for url in urls:
                self.check_stop()
                if url in visited:
                    continue
                visited.add(url)
                try:
                    contact = self.browser.read_profile(url)
                    score, reason = qualify(contact, self.config)
                    self.store.upsert(contact, score, reason)
                    self.log(f"{'Qualified' if score else 'Skipped'}: {contact.name} — {reason}")
                except Skip as exc:
                    self.log(f"Skipped profile: {exc}")
                self.wait(self.config["seconds_between_actions"])
            self.store.set("query_cursor", str((index + 1) % len(search_terms)))
        return len(visited)

    def process(self, live: bool = False):
        self.check_stop()
        sent = 0
        contacts = [row for row in self.store.contacts() if row["score"] > 0 and row["state"] not in
                    {"stopped", "replied", "opted_out", "introduction_sent", "needs_review"}]
        if not contacts:
            return 0
        # Rotate a durable cursor so unanswered invitations cannot indefinitely
        # consume every check in a bounded cycle. Higher scores order each pass.
        cursor = int(self.store.get("process_cursor", "0")) % len(contacts)
        contacts = contacts[cursor:] + contacts[:cursor]
        inspected = 0
        for index, row in enumerate(contacts):
            self.check_stop()
            if live and (sent >= self.config["max_sends_per_cycle"] or self.store.recent_attempts() >= self.config["max_sends_per_24h"]):
                self.log("Configured send budget reached; ending this cycle.")
                break
            if inspected >= self.config["max_profiles_per_cycle"]:
                break
            inspected += 1
            self.store.set("process_cursor", str((cursor + index + 1) % len(contacts)))
            try:
                contact = self.browser.read_profile(row["url"])
                score, reason = qualify(contact, self.config)
                self.store.upsert(contact, score, reason)
                if not score:
                    continue
                if contact.relationship == "pending":
                    self.store.state(contact.url, "invitation_pending")
                    continue
                if contact.relationship == "connected":
                    # Only message people accepted after an invitation from this app.
                    # Existing connections are not silently enrolled in outreach.
                    invitation = self.store.action(contact.url, "invitation")
                    if not invitation or invitation["status"] != "sent":
                        self.store.state(contact.url, "stopped")
                        self.log(f"Existing connection left untouched: {contact.name}")
                        continue
                    self.browser.prior_invitation = invitation["text"]
                    kind = "introduction"
                elif contact.relationship == "not_connected":
                    kind = "invitation"
                else:
                    self.log(f"Skipped {contact.name}: connection status could not be verified")
                    continue
                if self.store.action(contact.url, kind):
                    continue
                text = drafts(contact, self.config)[kind]
                if not live:
                    self.log(f"PREVIEW {kind} to {contact.name}: {text}")
                    continue
                execute_action(self.store, self.browser, contact, kind, text, self.check_pause)
                sent += 1
                self.log(f"Sent {kind} to {contact.name}")
            except Skip as exc:
                self.log(f"Stopped contact: {exc}")
                self.store.state(row["url"], "stopped")
            finally:
                # Do not delay a halt/interrupt or mask its original exception.
                if sys.exc_info()[0] is None:
                    self.wait(self.config["seconds_between_actions"])
        return sent
