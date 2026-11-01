"""Session behavior tests using a simulated page; no browser or network calls."""
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from outreach.browser import LinkedInBrowser
from outreach.core import Halt


class Locator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    def filter(self, **kwargs):
        return self

    @property
    def first(self):
        return self

    def or_(self, other):
        return Locator(self.page, self.selector + "|" + other.selector)

    def count(self):
        return int(("/mynetwork/" in self.selector and self.page.state == "active")
                   or ("session_key" in self.selector and self.page.state == "expired"))

    def wait_for(self, **kwargs):
        if self.page.after_wait_state:
            state, self.page.after_wait_state = self.page.after_wait_state, None
            self.page.change_state(state)
        if not self.count():
            raise TimeoutError("No matching visible element")

    def inner_text(self, **kwargs):
        return "unusual activity" if self.page.state == "restricted" else "Example page"


class Page:
    def __init__(self, state):
        self.visits = []
        self.closed = False
        self.on_goto_state = None
        self.after_wait_state = None
        self.change_state(state)

    def change_state(self, state):
        self.state = state
        self.url = {
            "active": "https://www.linkedin.com/feed/",
            "expired": "https://www.linkedin.com/login",
            "checkpoint": "https://www.linkedin.com/checkpoint/challenge/",
        }.get(state, "https://www.linkedin.com/feed/")

    def goto(self, url, **kwargs):
        if self.closed:
            raise RuntimeError("Page is closed")
        self.visits.append(url)
        if self.on_goto_state:
            state, self.on_goto_state = self.on_goto_state, None
            self.change_state(state)

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        return Locator(self, selector)


class SessionTests(unittest.TestCase):
    def browser(self, state):
        browser = LinkedInBrowser(Path("unused"), {})
        browser.page = Page(state)
        browser.context = SimpleNamespace(pages=[browser.page])
        return browser

    def test_active_session_never_prompts_or_visits_login(self):
        browser = self.browser("active")
        with patch("builtins.input") as prompt:
            self.assertEqual(browser.login(), "reused")
        prompt.assert_not_called()
        self.assertEqual(browser.page.visits, [])

    def test_expired_session_allows_one_manual_sign_in(self):
        browser = self.browser("expired")
        with patch("builtins.input", side_effect=lambda _: browser.page.change_state("active")) as prompt:
            self.assertEqual(browser.login(), "signed_in")
        prompt.assert_called_once()
        self.assertEqual(browser.page.visits, ["https://www.linkedin.com/feed/"])

    def test_failed_manual_sign_in_does_not_retry(self):
        browser = self.browser("expired")
        with patch("builtins.input", return_value="") as prompt:
            with self.assertRaises(Halt):
                browser.login()
        prompt.assert_called_once()
        self.assertEqual(browser.page.visits, ["https://www.linkedin.com/feed/"] * 2)

    def test_ambiguous_session_does_not_start_login(self):
        browser = self.browser("unknown")
        with patch("builtins.input") as prompt:
            with self.assertRaisesRegex(Halt, "Cannot determine saved-session status"):
                browser.login()
        prompt.assert_not_called()

    def test_challenge_and_restriction_do_not_prompt_or_retry(self):
        for state in ("checkpoint", "restricted"):
            with self.subTest(state=state):
                browser = self.browser(state)
                with patch("builtins.input") as prompt:
                    with self.assertRaises(Halt):
                        browser.login()
                prompt.assert_not_called()
                self.assertEqual(browser.page.visits, [])

    def test_normal_run_guard_stops_on_expiry_without_login(self):
        browser = self.browser("expired")
        with patch("builtins.input") as prompt:
            with self.assertRaises(Halt):
                browser.guard()
        prompt.assert_not_called()
        self.assertEqual(browser.page.visits, [])

    def test_manual_login_completes_in_different_tab(self):
        browser = self.browser("expired")
        original = browser.page
        authenticated = Page("active")
        with patch("builtins.input", side_effect=lambda _: browser.context.pages.append(authenticated)) as prompt:
            self.assertEqual(browser.login(), "signed_in")
        self.assertIs(browser.page, authenticated)
        self.assertEqual(original.visits, ["https://www.linkedin.com/feed/"])
        self.assertEqual(authenticated.visits, [])
        prompt.assert_called_once()

    def test_existing_feed_tab_avoids_relogin_from_stale_login_tab(self):
        browser = self.browser("expired")
        original = browser.page
        authenticated = Page("active")
        browser.context.pages.append(authenticated)
        with patch("builtins.input") as prompt:
            self.assertEqual(browser.login(), "reused")
        prompt.assert_not_called()
        self.assertIs(browser.page, authenticated)
        self.assertEqual(original.visits, [])

    def test_closed_original_tab_does_not_invalidate_active_feed(self):
        browser = self.browser("expired")
        original = browser.page
        authenticated = Page("active")
        def complete(_):
            original.closed = True
            browser.context.pages.append(authenticated)
        with patch("builtins.input", side_effect=complete):
            self.assertEqual(browser.login(), "signed_in")
        self.assertIs(browser.page, authenticated)

    def test_enter_rechecks_shared_session_instead_of_rejecting_stale_url(self):
        browser = self.browser("expired")
        def complete(_):
            # Login set a shared session, but the original tab is still on /login.
            browser.page.on_goto_state = "active"
        with patch("builtins.input", side_effect=complete) as prompt:
            self.assertEqual(browser.login(), "signed_in")
        prompt.assert_called_once()
        self.assertEqual(browser.page.visits, ["https://www.linkedin.com/feed/"] * 2)

    def test_waits_for_navigation_after_enter(self):
        browser = self.browser("expired")
        def complete(_):
            browser.page.on_goto_state = "loading"
            browser.page.after_wait_state = "active"
        with patch("builtins.input", side_effect=complete):
            self.assertEqual(browser.login(), "signed_in")

    def test_saved_session_check_never_requests_sign_in(self):
        for state, expected in (("active", True), ("expired", False)):
            with self.subTest(state=state):
                browser = self.browser(state)
                with patch("builtins.input") as prompt:
                    self.assertEqual(browser.check_session(), expected)
                prompt.assert_not_called()

    def test_another_feed_tab_does_not_bypass_verification(self):
        browser = self.browser("checkpoint")
        browser.context.pages.append(Page("active"))
        with patch("builtins.input") as prompt:
            with self.assertRaises(Halt):
                browser.check_session()
        prompt.assert_not_called()
        self.assertEqual(browser.page.visits, [])


if __name__ == "__main__":
    unittest.main()
