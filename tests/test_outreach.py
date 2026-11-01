import copy
import json
import tempfile
import unittest
from pathlib import Path

from outreach.__main__ import main
from outreach.core import Contact, Halt, Skip, Store, canonical_url, drafts, introduction_allowed, load_config, qualify
from outreach.report import write_preview
from outreach.runner import Runner, single_runner


ROOT = Path(__file__).resolve().parent.parent


def contact(name="Priya Example", relationship="not_connected"):
    return Contact("https://www.linkedin.com/in/fictional-priya/", name,
                   "Talent Acquisition Manager at Accenture", "Accenture", "Pune",
                   "Talent Acquisition Manager at Accenture", relationship)


class FakeBrowser:
    def __init__(self, person):
        self.person = person
        self.sent = []
        self.failure = None
        self.existing_conversation = False

    def search(self, query, limit):
        return [self.person.url]

    def read_profile(self, url):
        return copy.deepcopy(self.person)

    def prepare(self, person, kind, text):
        if self.existing_conversation:
            raise Skip("Existing conversation")

    def send(self, person, kind, text):
        self.sent.append((kind, text))
        if kind == "invitation":
            self.person.relationship = "pending"
        if self.failure:
            raise self.failure


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.config = load_config(ROOT / "settings.example.json")
        self.store = Store(self.directory / "test.sqlite3")
        self.person = contact()
        self.browser = FakeBrowser(self.person)
        self.runner = Runner(self.store, self.browser, self.config, log=lambda _: None, wait=lambda _: None)
        self.store.upsert(self.person, *qualify(self.person, self.config))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_complete_sequence_and_no_followups(self):
        self.assertEqual(self.runner.process(live=True), 1)
        self.assertEqual(self.runner.process(live=True), 0)
        self.browser.person.relationship = "connected"
        self.assertEqual(self.runner.process(live=True), 1)
        for _ in range(3):
            self.assertEqual(self.runner.process(live=True), 0)
        self.assertEqual([kind for kind, _ in self.browser.sent], ["invitation", "introduction"])
        self.assertEqual(self.store.contacts()[0]["state"], "introduction_sent")

    def test_preview_never_prepares_or_sends(self):
        self.browser.prepare = lambda *args: self.fail("Preview must not open a send dialog")
        self.runner.process(live=False)
        self.assertEqual(self.browser.sent, [])
        self.assertEqual(self.store.report()["actions"], [])

    def test_lost_acknowledgement_blocks_retry_after_restart(self):
        self.browser.failure = TimeoutError("lost confirmation")
        with self.assertRaises(TimeoutError):
            self.runner.process(live=True)
        action = self.store.action(self.person.url, "invitation")
        self.assertEqual(action["status"], "uncertain")
        self.store.close()
        self.store = Store(self.directory / "test.sqlite3")
        restarted = Runner(self.store, self.browser, self.config, wait=lambda _: None)
        with self.assertRaises(Halt):
            restarted.process(live=True)
        self.assertEqual(len(self.browser.sent), 1)

    def test_crash_after_intent_is_not_retryable(self):
        self.store.begin(self.person.url, "invitation", "example note")
        with self.assertRaises(Halt):
            self.runner.process(live=True)
        self.assertEqual(self.browser.sent, [])

    def test_resolve_sent_advances_to_introduction_only(self):
        action_id = self.store.begin(self.person.url, "invitation", "example note")
        self.store.resolve(action_id, "sent")
        self.browser.person.relationship = "connected"
        self.runner.process(live=True)
        self.assertEqual([a[0] for a in self.browser.sent], ["introduction"])

    def test_resolve_abandoned_stops_contact(self):
        action_id = self.store.begin(self.person.url, "invitation", "example note")
        self.store.resolve(action_id, "abandoned")
        self.runner.process(live=True)
        self.assertEqual(self.browser.sent, [])

    def test_existing_connection_is_not_enrolled(self):
        self.browser.person.relationship = "connected"
        self.runner.process(live=True)
        self.assertEqual(self.browser.sent, [])

    def test_existing_conversation_stops_introduction(self):
        self.runner.process(live=True)
        self.browser.person.relationship = "connected"
        self.browser.existing_conversation = True
        self.runner.process(live=True)
        self.assertEqual(len(self.browser.sent), 1)
        self.assertEqual(self.store.contacts()[0]["state"], "stopped")

    def test_only_known_outgoing_invitation_is_ignored_in_history(self):
        note = "Hi Priya, I'd like to connect."
        own_note = ("Alex Example", note)
        self.assertTrue(introduction_allowed([], "Alex Example", note))
        self.assertTrue(introduction_allowed([own_note], "Alex Example", note))
        self.assertFalse(introduction_allowed([("Priya Example", note)], "Alex Example", note))
        self.assertFalse(introduction_allowed([own_note, ("Priya Example", "Thanks!")], "Alex Example", note))
        self.assertFalse(introduction_allowed([("Alex Example", "Another message")], "Alex Example", note))
        self.assertFalse(introduction_allowed([("", note)], "Alex Example", note))
        self.assertFalse(introduction_allowed([own_note], "Alex Example", None))

    def test_profile_change_requalifies_before_send(self):
        self.browser.person.company = "Unrelated Employer"
        self.runner.process(live=True)
        self.assertEqual(self.browser.sent, [])

    def test_unknown_connection_state_does_not_send(self):
        self.browser.person.relationship = "unknown"
        self.runner.process(live=True)
        self.assertEqual(self.browser.sent, [])

    def test_stop_before_final_click(self):
        def stop_after_prepare(*args):
            self.store.set("paused", "Paused by user")
        self.browser.prepare = stop_after_prepare
        with self.assertRaises(Halt):
            self.runner.process(live=True)
        self.assertEqual(self.browser.sent, [])
        self.assertEqual(self.store.report()["actions"], [])

    def test_send_budget_persists_across_cycles(self):
        self.config["max_sends_per_24h"] = 1
        self.runner.process(live=True)
        self.browser.person.relationship = "connected"
        self.runner.process(live=True)
        self.assertEqual(len(self.browser.sent), 1)

    def test_check_cursor_prevents_pending_contact_starvation(self):
        self.config["max_profiles_per_cycle"] = 1
        second = Contact("https://www.linkedin.com/in/fictional-other/", "Other Example",
                         "HR recruiter at IBM", "IBM", "Mumbai", "HR recruiter at IBM", "pending")
        self.store.upsert(second, *qualify(second, self.config))
        self.person.relationship = "pending"
        self.store.upsert(self.person, *qualify(self.person, self.config))
        seen = []
        people = {self.person.url: self.person, second.url: second}
        def read(url):
            seen.append(url)
            return people[url]
        self.browser.read_profile = read
        self.runner.process(live=True)
        self.runner.process(live=True)
        self.assertEqual(set(seen), set(people))
        self.assertEqual(len(seen), 2)

    def test_deduplicate_discovery_preserves_outreach_state(self):
        self.runner.process(live=True)
        self.runner.discover()
        self.assertEqual(len(self.store.contacts()), 1)
        self.assertEqual(self.store.contacts()[0]["state"], "invitation_pending")

    def test_lock_rejects_second_worker(self):
        with single_runner(self.directory):
            with self.assertRaises(Halt):
                with single_runner(self.directory):
                    self.fail("Second worker must not enter")

    def test_targeting_locations_aliases_and_ranking(self):
        self.person.location = "Bangalore, Karnataka, India"
        self.assertGreater(qualify(self.person, self.config)[0], 0)
        self.person.location = "London"
        self.assertEqual(qualify(self.person, self.config)[0], 0)
        self.person.location = "Mumbai"
        self.person.headline = "Software Engineer at Accenture"
        self.assertEqual(qualify(self.person, self.config)[0], 0)
        self.person.headline = "HR recruiter at Accenture"
        baseline = qualify(self.person, self.config)[0]
        self.person.evidence = "Hiring for talent acquisition"
        self.assertGreater(qualify(self.person, self.config)[0], baseline)

    def test_profile_url_validation_and_deduplication(self):
        self.assertEqual(canonical_url(self.person.url + "?trk=search"), self.person.url)
        for url in ("https://linkedin.com.evil.test/in/name", "file:///in/name", "https://www.linkedin.com/jobs/123", "https://user@www.linkedin.com/in/name", "https://www.linkedin.com/in/foo%2Fbar"):
            with self.assertRaises(ValueError):
                canonical_url(url)

    def test_drafts_are_bounded_and_do_not_invent_hiring(self):
        result = drafts(self.person, self.config)
        self.assertLessEqual(len(result["invitation"]), self.config["invitation_character_limit"])
        self.assertIn("exploring", result["invitation"])
        self.assertNotIn("you are hiring", result["introduction"])
        self.config["invitation_character_limit"] = 10
        with self.assertRaises(Skip):
            drafts(self.person, self.config)

    def test_preview_escapes_untrusted_profile_text(self):
        self.person.headline = '<script>alert("x")</script>'
        self.store.upsert(self.person, 0, "not qualified")
        output = write_preview(self.store.report(), self.config, self.directory / "preview.html")
        html = output.read_text()
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_configuration_rejects_invalid_send_budgets(self):
        for value in (0, -1, True, 1.5):
            self.config["max_sends_per_24h"] = value
            path = self.directory / "settings.json"
            path.write_text(json.dumps(self.config))
            with self.assertRaises(ValueError):
                load_config(path)

    def test_demo_does_not_create_live_database(self):
        output = self.directory / "demo.html"
        data = self.directory / "unused"
        self.assertEqual(main(["--config", str(ROOT / "settings.example.json"),
                               "--data-dir", str(data), "demo", "--output", str(output)]), 0)
        self.assertFalse(data.exists())
        self.assertIn("fictional", output.read_text().lower())

    def test_missing_config_explains_example_setup(self):
        with self.assertRaisesRegex(ValueError, "settings.example.json"):
            load_config(self.directory / "missing.json")

    def test_pause_works_even_when_config_file_is_missing(self):
        result = main(["--config", str(self.directory / "missing.json"),
                       "--data-dir", str(self.directory / "control"), "pause"])
        self.assertEqual(result, 0)
        other = Store(self.directory / "control" / "outreach.sqlite3")
        try:
            self.assertEqual(other.get("paused"), "Paused by user")
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
