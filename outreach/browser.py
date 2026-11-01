"""English-language LinkedIn UI adapter. UI changes stop the run.

These selectors must be checked against the signed-in account before relying on
live operation. No private API, stealth patch, or authentication bypass is used.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .core import Contact, Halt, Skip, canonical_url, compact, contains, introduction_allowed


WARNING = re.compile(
    r"account (?:has been |is )?(?:temporarily |permanently )?restricted|"
    r"reached (?:the |your )?weekly invitation limit|"
    r"you(?:'ve| have) reached (?:the |your )?(?:invitation|commercial use) limit|"
    r"unusual activity|automated activity|verify your identity|security verification|"
    r"too many requests|you(?:'ve| have) reached (?:your )?(?:monthly )?personalized invitation limit",
    re.I,
)


class LinkedInBrowser:
    def __init__(self, directory: Path, config: dict, check_stop=lambda: None):
        self.directory = directory
        self.config = config
        self.check_stop = check_stop
        self.pw = self.context = self.page = None
        self.top = self.composer = self.send_button = None
        self.prepared = None
        self.prior_invitation = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise Halt("Playwright is missing. Run: .venv/bin/python -m pip install -r requirements.txt") from exc
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.pw = sync_playwright().start()
        try:
            kwargs = {"channel": "chrome"} if self.config["browser_channel"] == "chrome" else {}
            self.context = self.pw.chromium.launch_persistent_context(
                str(self.directory.resolve()), headless=False, viewport={"width": 1280, "height": 900}, **kwargs
            )
            self.context.set_default_timeout(12000)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            return self
        except BaseException:
            self.pw.stop()
            raise

    def __exit__(self, *args):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.pw:
                self.pw.stop()

    def guard(self, allow_sign_in: bool = False):
        self.check_stop()
        path = urlsplit(self.page.url).path.lower()
        if any(part in path for part in ("/checkpoint", "/challenge")):
            raise Halt("LinkedIn requires account verification; no automatic login retry")
        if not allow_sign_in and any(part in path for part in ("/login", "/authwall")):
            raise Halt("LinkedIn requires login or account verification")
        if self.page.locator('iframe[src*="captcha"], iframe[src*="challenge"]').count():
            raise Halt("Verification challenge detected")
        text = self.page.locator("body").inner_text(timeout=12000)
        if WARNING.search(text):
            raise Halt("LinkedIn displayed an account, invitation, or verification warning")

    def go(self, url: str):
        self.check_stop()
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self.guard()

    @staticmethod
    def visible(locator):
        return locator.filter(visible=True)

    def one(self, locator, description: str):
        locator = self.visible(locator)
        if locator.count() != 1:
            raise Halt(f"Expected exactly one visible {description}; UI requires review")
        return locator

    def signed_in_navigation(self):
        return self.visible(self.page.locator('nav a[href*="/mynetwork/"], header a[href*="/mynetwork/"]'))

    @staticmethod
    def is_linkedin_page(page):
        return urlsplit(page.url).hostname in {"www.linkedin.com", "linkedin.com", "in.linkedin.com"}

    def select_signed_in_tab(self):
        # Manual sign-in can complete in another tab while the original tab
        # remains on /login. Inspect page state, never cookies or session files.
        original = self.page
        pages = self.context.pages if self.context else [self.page]
        for page in reversed(pages):
            if page.is_closed() or not self.is_linkedin_page(page):
                continue
            if urlsplit(page.url).path.rstrip("/") != "/feed":
                continue
            self.page = page
            self.guard()
            if self.signed_in_navigation().count():
                return True
        self.page = original
        return False

    def check_session(self):
        # Do not navigate away from an outstanding challenge to obtain a feed.
        pages = self.context.pages if self.context else [self.page]
        original = self.page
        for page in pages:
            if not page.is_closed() and self.is_linkedin_page(page):
                self.page = page
                self.guard(allow_sign_in=True)
        self.page = original
        if self.select_signed_in_tab():
            return True
        if self.page.is_closed():
            if not self.context:
                raise Halt("The app's browser tab was closed; session could not be checked")
            self.page = self.context.new_page()
        # One feed navigation rechecks the shared saved session, including cookies
        # established by a separate login tab. This does not submit credentials.
        self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
        self.guard(allow_sign_in=True)
        signed_in = self.signed_in_navigation()
        sign_in_form = self.visible(self.page.locator('input[name="session_key"], input[autocomplete="username"]'))
        try:
            signed_in.or_(sign_in_form).first.wait_for(state="visible", timeout=12000)
        except Exception as exc:
            self.guard(allow_sign_in=True)
            raise Halt("Cannot determine saved-session status; stopped without requesting another login") from exc
        self.guard(allow_sign_in=True)
        if self.select_signed_in_tab():
            return True
        if not sign_in_form.count():
            raise Halt("Session status is ambiguous; stopped without requesting another login")
        return False

    def login(self):
        if self.check_session():
            return "reused"
        input("No active saved session. Sign in once in the opened browser, then press Enter here: ")
        if not self.check_session():
            raise Halt("The app's saved session is still signed out. No further login was attempted. "
                       "Sign-in must complete in the browser opened by this app.")
        return "signed_in"

    def search(self, query: str, limit: int) -> list[str]:
        self.go("https://www.linkedin.com/search/results/people/?" + urlencode({"keywords": query}))
        main = self.page.get_by_role("main")
        main.wait_for(state="visible")
        # Scope links to result cards, excluding recommendations and navigation.
        cards = main.locator(".reusable-search__result-container")
        try:
            cards.first.wait_for(state="visible", timeout=12000)
        except Exception:
            self.guard()
            if re.search(r"no results|no matching results", main.inner_text(), re.I):
                return []
            raise Halt("People-search result layout is unrecognized; inspect the UI adapter")
        urls = []
        for card in cards.all():
            links = card.locator('a[href*="/in/"]').all()
            for link in links:
                href = link.get_attribute("href") or ""
                try:
                    url = canonical_url(href)
                except ValueError:
                    continue
                if url not in urls:
                    urls.append(url)
                break
            if len(urls) >= limit:
                break
        return urls

    def read_profile(self, url: str) -> Contact:
        self.go(canonical_url(url))
        try:
            if canonical_url(self.page.url) != canonical_url(url):
                raise Halt("Profile redirected to a different identity")
        except ValueError as exc:
            raise Halt("Profile navigation did not reach the requested identity") from exc
        main = self.page.get_by_role("main")
        main.get_by_role("heading", level=1).wait_for(state="visible")
        h1 = self.one(main.get_by_role("heading", level=1), "profile name")
        self.top = self.one(h1.locator("xpath=ancestor::section[1]"), "profile header")
        headline = self.one(self.top.locator(".text-body-medium.break-words"), "headline").inner_text()
        location = self.one(self.top.locator(".text-body-small.inline.t-black--light.break-words"), "profile location").inner_text()
        company = ""
        # Current employer evidence must come from the current-company control or
        # an explicit 'at/@ employer' headline, never from historical experience.
        employer_controls = self.top.locator('[aria-label^="Current company:"]')
        employer_evidence = " ".join((n.get_attribute("aria-label") or "") for n in employer_controls.all())
        for target in self.config["companies"]:
            matched = any(contains(employer_evidence, alias) for alias in target["aliases"])
            if not employer_evidence:
                matched = any(re.search(r"(?:\bat\s+|@\s*)" + re.escape(alias) + r"(?:\b|$)", headline, re.I) for alias in target["aliases"])
                if re.search(r"\b(?:former|formerly|previously|ex)\b", headline, re.I):
                    matched = False
            if matched:
                if company:
                    raise Skip("Ambiguous current employer")
                company = target["name"]
        top_text = compact(self.top.inner_text())
        relation = "unknown"
        degrees = " ".join(self.top.locator(".dist-value").all_inner_texts())
        if re.search(r"\b1st\b", degrees):
            relation = "connected"
        elif self.visible(self.top.get_by_role("button", name=re.compile(r"^Pending$|^Invitation pending", re.I))).count():
            relation = "pending"
        elif re.search(r"\b(?:2nd|3rd)\b", degrees):
            relation = "not_connected"
        return Contact(url, h1.inner_text(), headline, company, location,
                       compact(headline + " " + employer_evidence + " " + top_text), relation)

    def connect_control(self):
        return self.visible(self.top.get_by_role("button", name=re.compile(r"^Connect$|^Invite .+ to connect$", re.I)))

    def history_allows_introduction(self, history) -> bool:
        events = []
        for event in history.locator(".msg-s-event-listitem").all():
            body = event.locator(".msg-s-event-listitem__body")
            group = event.locator("xpath=ancestor::li[1]")
            sender = group.locator(".msg-s-message-group__name")
            # Missing sender/content is ambiguous, never proof of an empty chat.
            if body.count() != 1 or sender.count() != 1:
                return False
            events.append((sender.inner_text(), body.inner_text()))
        return introduction_allowed(events, self.config["candidate"]["name"], self.prior_invitation)

    def prepare(self, contact: Contact, kind: str, text: str):
        self.guard()
        if canonical_url(self.page.url) != contact.url:
            raise Halt("Prepared action is not on the expected profile")
        self.prepared = (contact.url, kind, text)
        if kind == "invitation":
            if contact.relationship != "not_connected":
                raise Skip("Cannot verify that this contact is eligible for an invitation")
            # No click until durable intent is saved; Connect could change behavior.
            if self.connect_control().count() != 1:
                more = self.visible(self.top.get_by_role("button", name=re.compile(r"^More$|^More actions$", re.I)))
                if more.count() != 1:
                    raise Skip("No unambiguous Connect or More control")
            return
        if kind != "introduction" or contact.relationship != "connected":
            raise Skip("A first-degree connection is required for the introductory message")
        # Close existing chats first, to avoid addressing the wrong conversation.
        for button in self.page.get_by_role("button", name=re.compile(r"^Close (?:your )?conversation", re.I)).all():
            if button.is_visible():
                button.click()
        button = self.one(self.top.get_by_role("button", name=re.compile(r"^Message$|^Message .+", re.I)), "profile Message button")
        button.click()
        editors = self.page.locator('.msg-form__contenteditable[contenteditable="true"]')
        editors.first.wait_for(state="visible")
        editor = self.one(editors, "message editor")
        self.composer = self.one(editor.locator("xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' msg-overlay-conversation-bubble ')][1]"), "conversation")
        # Require this exact profile's link in the conversation header.
        header_links = self.composer.locator('.msg-overlay-bubble-header a[href*="/in/"]').all()
        identities = set()
        for link in header_links:
            try:
                identities.add(canonical_url(link.get_attribute("href") or ""))
            except ValueError:
                pass
        if identities != {contact.url}:
            raise Halt("Cannot verify the conversation recipient")
        history = self.one(self.composer.locator(".msg-s-message-list-content"), "conversation history")
        if self.visible(self.composer.locator(".artdeco-loader, [role='progressbar']")).count():
            raise Halt("Conversation history is still loading")
        if not self.history_allows_introduction(history):
            raise Skip("Existing conversation found; automated outreach stopped for this person")
        editor.fill(text)
        self.send_button = self.one(self.composer.get_by_role("button", name="Send", exact=True), "message Send button")
        self.guard()

    def send(self, contact: Contact, kind: str, text: str):
        if self.prepared != (contact.url, kind, text):
            raise Halt("Send does not match the prepared action")
        self.guard()
        if kind == "invitation":
            connect = self.connect_control()
            if connect.count() != 1:
                self.one(self.top.get_by_role("button", name=re.compile(r"^More$|^More actions$", re.I)), "More button").click()
                connect = self.page.get_by_role("menuitem", name=re.compile(r"^Connect$|^Invite .+ to connect$", re.I))
            self.one(connect, "Connect action").click()
            dialog = self.page.get_by_role("dialog")
            dialog.first.wait_for(state="visible")
            dialog = self.one(dialog, "invitation dialog")
            self.guard()
            if not contains(dialog.inner_text(), contact.name):
                raise Halt("Invitation dialog does not identify the expected recipient")
            note = self.one(dialog.get_by_role("button", name="Add a note", exact=True), "Add a note button")
            note.click()
            textbox = self.one(dialog.get_by_role("textbox"), "invitation note field")
            maximum = textbox.get_attribute("maxlength")
            if maximum and len(text) > int(maximum):
                raise Halt("Invitation exceeds the account's displayed character allowance")
            textbox.fill(text)
            self.guard()
            if textbox.input_value() != text:
                raise Halt("Invitation text does not match the prepared note")
            self.one(dialog.get_by_role("button", name=re.compile(r"^Send$|^Send invitation$", re.I)), "invitation Send button").click()
            # Positive evidence is required; a disappearing dialog alone is not proof.
            self.top.get_by_role("button", name=re.compile(r"^Pending$|^Invitation pending", re.I)).wait_for(state="visible", timeout=15000)
        else:
            self.guard()
            history = self.composer.locator(".msg-s-message-list-content")
            if not self.history_allows_introduction(history):
                raise Halt("Conversation changed before send; action cancelled")
            editor = self.one(self.composer.locator('.msg-form__contenteditable[contenteditable="true"]'), "message editor")
            if compact(editor.inner_text()) != compact(text):
                raise Halt("Message text does not match the prepared draft")
            self.send_button.click()
            history.locator(".msg-s-event-listitem").filter(has_text=text).first.wait_for(state="visible", timeout=15000)
        self.guard()
        self.prepared = None
