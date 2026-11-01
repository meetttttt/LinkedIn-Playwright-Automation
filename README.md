# LinkedIn-Playwright-Automation

A Google Sheets hiring tracker workflow, with a separate Python networking CLI
and a historical Excel exporter. Personal configuration, research, resumes,
browser sessions and live contact history stay outside Git.

The **Google Sheet is the daily tracker's source of truth**. This repository
contains supporting code and the workflow contract; cloning it does not create
or copy a Google Sheet. Daily research uses connected Google Sheets tools and
requires an existing configured tracker. It is initiated on request, not by CI.

## Set up a checkout

Requires Python 3.10+ and macOS or Linux. The networking worker uses POSIX file
locking; Windows is not supported. Google Chrome is needed only for browser work.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp -n settings.example.json settings.json
cp -n tracker_config.example.json tracker_config.json
```

Use these copy commands only for a new checkout. Preserve existing personal
files. Edit `settings.json` with your own factual candidate details and target
employers. The example candidate is fictional. In `tracker_config.json`, replace
`YOUR_SPREADSHEET_ID` in the ID and URL with your existing tracker. Neither private
file is committed. No credentials belong in either example file.

For networking-only use, `requirements.txt` is sufficient. Development requirements
also install `openpyxl`, needed by the legacy exporter and its tests.

## Daily hiring tracker

Read [the tracker workflow](docs/tracker-workflow.md). The existing spreadsheet
holds settings, formulas, native tests and the daily procedure. Its visible tabs
are **Daily Overview** and **Opportunities**, plus hidden **Tracker Checks**.
Shortlist and related views are saved filters, not duplicate record tables.

The tracker records source links, first-added dates, application targets,
application progress, vacancy evidence, salary basis and working schedules.
Updates preserve notes and progress and deduplicate repeated posts. Missing
facts remain explicitly unverified. Full-time hours do not establish permanent
employment; an IST timestamp does not establish a daytime shift.

Ask your connected agent: “Find the latest matching hiring posts and update my
Google job tracker.” It must read live settings and use the existing sheet.
The Python networking CLI does not perform this Sheets workflow, and editing
`settings.json` does not synchronize daily-search settings into the sheet.

For a new agent workspace, adapt [AGENTS.example.md](AGENTS.example.md) into a
local `AGENTS.md`. Do not overwrite existing workspace instructions.

## Offline verification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m outreach --config settings.example.json queries
.venv/bin/python -m outreach --config settings.example.json demo
```

Tests use fictional configuration, a simulated browser and temporary files. They
do not log in, send invitations, edit the spreadsheet or require private settings.
The demo writes an ignored `output/demo-preview.html` file.

GitHub Actions runs the offline suite on pushes and pull requests with Python
3.10 and 3.12. The live spreadsheet has a separate 149-check suite; it must be
verified through Google Sheets before and after actual tracker updates.
The workflow follows the official [checkout](https://github.com/actions/checkout)
and [setup-python](https://github.com/actions/setup-python) action documentation.

## Separate networking CLI

Normal runs produce previews. Sending requires the explicit `--live` flag.
There are no follow-ups or automatic job applications.

```sh
# Read-only search plan; no browser.
.venv/bin/python -m outreach queries
# Inspect the saved session without requesting sign-in.
.venv/bin/python -m outreach session
# Only if no saved session exists, sign in once interactively.
.venv/bin/python -m outreach login
# Discovery and preview; no sending.
.venv/bin/python -m outreach run
.venv/bin/python -m outreach preview
```

The app uses its own persistent profile in `.outreach/browser/`; it does not
import your normal Chrome profile. Session-check commands close their browser
window but retain the saved profile. Normal runs never trigger login automatically.
Expiry, verification challenges and ambiguous UI states stop the run. Do not use
the same profile from this app and another browser controller at the same time.

To use Chromium, set `browser_channel` to `chromium` in your private settings and
run `.venv/bin/python -m playwright install chromium`.

The browser adapter has offline tests; its live selectors still require validation.
LinkedIn automation can cause restrictions. Delays and workload caps are not
protection from detection or platform-approved quotas. See
[LinkedIn's automation policy](https://www.linkedin.com/help/linkedin/answer/a1341387/).

For an independently authorized outreach run:

```sh
.venv/bin/python -m outreach run --live
.venv/bin/python -m outreach run --live --watch
.venv/bin/python -m outreach status
.venv/bin/python -m outreach pause
.venv/bin/python -m outreach stop-contact https://www.linkedin.com/in/PROFILE/
```

The watch process runs in the foreground. Stop it before running recovery commands.
A pause cannot recall a send already submitted. An uncertain send is recorded
and blocks retries until its outcome is manually checked:

```sh
.venv/bin/python -m outreach resolve 1 --outcome sent
# Or: resolve 1 --outcome abandoned
.venv/bin/python -m outreach resume
```

Introductions require an invitation recorded by this app and observed acceptance.
Existing conversations, pre-existing connections, replies and ambiguous history
are not automatically enrolled. Send budgets persist across cycles. These controls
are separate from the daily hiring tracker.

## Historical Excel exporter

`hiring_tracker.py` supports explicitly requested local exports. It is not the
Google Sheets pipeline and cannot synchronize the current native schema. It
requires `--workbook`, even when private configuration is absent. Its older
Overview/Run Log layout must never be written back to the live sheet.

```sh
.venv/bin/python hiring_tracker.py --input reviewed-research.json \
  --workbook output/explicit-export.xlsx
```

## Repository contents

| Path | Purpose |
| --- | --- |
| `outreach/` | Networking workflow, browser adapter, SQLite ledger and previews |
| `tests/` | Offline behavioral tests with fictional data |
| `settings.example.json` | Shareable networking configuration example |
| `tracker_config.example.json` | Native tracker location/schema pointer template |
| `docs/tracker-workflow.md` | Google Sheets update contract |
| `docs/repository-alignment.md` | Repository preparation scope and verification |
| `.github/workflows/tests.yml` | Offline CI; no scheduled research or live actions |

`.gitignore` excludes private settings, local agent context, resumes, generated
exports, browser data and databases. Review staged files before committing.
