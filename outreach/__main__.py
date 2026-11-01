from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .core import Contact, Halt, Store, load_config, qualify, queries
from .report import write_preview
from .runner import Runner, single_runner

ROOT = Path(__file__).resolve().parent.parent


def parser():
    p = argparse.ArgumentParser(description="IT talent acquisition outreach. No follow-ups.")
    p.add_argument("--config", type=Path, default=ROOT / "settings.json")
    p.add_argument("--data-dir", type=Path, default=ROOT / ".outreach")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="Reuse the saved app session; ask for sign-in only if needed")
    sub.add_parser("session", help="Check the saved browser session without requesting sign-in or sending outreach")
    sub.add_parser("queries", help="Print the configured search queries without opening LinkedIn")
    sub.add_parser("discover", help="Find and qualify contacts, without sending")
    run = sub.add_parser("run", help="Discover and process contacts; preview unless --live is supplied")
    run.add_argument("--live", action="store_true", help="Send actual invitations and introductory messages")
    run.add_argument("--watch", action="store_true", help="Repeat cycles at the configured interval")
    run.add_argument("--skip-discovery", action="store_true", help="Process existing contacts only")
    sub.add_parser("status", help="Show contacts, action ledger, and pause reason as JSON")
    sub.add_parser("pause", help="Pause the current worker before its next action")
    sub.add_parser("resume", help="Clear pause after resolving any uncertain send")
    for name in ("preview", "demo"):
        command = sub.add_parser(name, help="Export an HTML preview" if name == "preview" else "Render fictional examples without a browser")
        command.add_argument("--output", type=Path)
    resolve = sub.add_parser("resolve", help="Record the manually verified outcome of an uncertain action")
    resolve.add_argument("action_id", type=int)
    resolve.add_argument("--outcome", choices=["sent", "abandoned"], required=True)
    stop = sub.add_parser("stop-contact", help="Permanently suppress a contact in this workflow")
    stop.add_argument("url")
    return p


def demo(config: dict) -> dict:
    people = [
        ("Priya Example", "Talent Acquisition Manager at Accenture", "Accenture", "Pune"),
        ("Rahul Example", "Technical Recruiter at Infosys", "Infosys", "Bangalore"),
        ("Neha Example", "HR Recruiter at IBM — hiring for talent acquisition", "IBM", "Mumbai"),
    ]
    rows = []
    for number, (name, title, company, city) in enumerate(people):
        c = Contact(f"https://www.linkedin.com/in/fictional-demo-{number}/", name, title, company, city, title, "not_connected")
        score, reason = qualify(c, config)
        rows.append({"data": asdict(c), "score": score, "reason": reason, "state": "fictional example"})
    return {"contacts": rows, "actions": [], "paused": None}


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    store = None
    try:
        # Stop/recovery controls must remain available if a config edit is invalid.
        config = load_config(args.config) if args.command in {"queries", "demo", "preview", "login", "session", "discover", "run"} else None
        if args.command == "queries":
            print("\n".join(queries(config)))
            return 0
        if args.command == "demo":
            output = args.output or ROOT / "output" / "demo-preview.html"
            print(write_preview(demo(config), config, output, demo=True))
            return 0
        args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        args.data_dir.chmod(0o700)
        store = Store(args.data_dir / "outreach.sqlite3")
        if args.command == "status":
            print(json.dumps(store.report(), indent=2, ensure_ascii=False))
        elif args.command == "pause":
            store.set("paused", "Paused by user")
            print("Paused. An action already submitted to LinkedIn cannot be recalled.")
        elif args.command == "preview":
            print(write_preview(store.report(), config, args.output or args.data_dir / "preview.html"))
        elif args.command in {"resume", "resolve", "stop-contact"}:
            with single_runner(args.data_dir):
                if args.command == "resume":
                    if store.unresolved():
                        raise Halt("An uncertain action still needs manual reconciliation; use status and resolve")
                    store.set("paused", "")
                    print("Pause cleared. Start run again when ready.")
                elif args.command == "resolve":
                    store.resolve(args.action_id, args.outcome)
                    print("Recorded. Use resume after resolving all outstanding actions.")
                else:
                    from .core import canonical_url
                    store.state(canonical_url(args.url), "stopped")
                    print("Contact suppressed.")
        else:
            from .browser import LinkedInBrowser
            with single_runner(args.data_dir):
                if args.command not in {"login", "session"} and (store.get("paused") or store.unresolved()):
                    raise Halt(store.get("paused") or "An earlier send is unresolved")
                # Session checks and manual sign-in remain available while paused.
                def check_pause():
                    if args.command not in {"login", "session"} and store.get("paused"):
                        raise Halt(store.get("paused"))
                with LinkedInBrowser(args.data_dir / "browser", config, check_pause) as browser:
                    if args.command == "session":
                        if not browser.check_session():
                            raise Halt("No active session was confirmed in the app's browser profile. "
                                       "No sign-in was requested and no outreach was sent.")
                        print("Saved session verified. No login or outreach was performed. "
                              "The browser window will close; the saved profile is retained.")
                    elif args.command == "login":
                        session = browser.login()
                        print("Existing browser session reused. No new login or outreach." if session == "reused"
                              else "Browser session saved locally. No outreach was sent.")
                        print("The browser window will close; the saved profile is retained.")
                    else:
                        runner = Runner(store, browser, config)
                        browser.check_stop = runner.check_pause
                        while True:
                            if args.command == "discover" or not args.skip_discovery:
                                runner.discover()
                            if args.command == "run":
                                runner.process(live=args.live)
                            preview = write_preview(store.report(), config, args.data_dir / "preview.html")
                            print(f"Preview: {preview}")
                            if args.command != "run" or not args.watch:
                                break
                            print(f"Waiting {config['cycle_interval_seconds']} seconds for next cycle; use pause or Ctrl+C to stop.")
                            runner.sleep(config["cycle_interval_seconds"])
        return 0
    except KeyboardInterrupt:
        print("Stopped. Check status for any send that was interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        if store and args.command in {"discover", "run"}:
            store.set("paused", f"{type(exc).__name__}: {exc}")
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
