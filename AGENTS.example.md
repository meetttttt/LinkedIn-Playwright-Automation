# Hiring tracker workspace

This is a shareable template. Copy it to `AGENTS.md` only when that file does not
already exist; preserve existing workspace instructions and personal settings.

## Repository guide

- `outreach/` contains the networking CLI, session handling, local ledger and
  preview generation. It is separate from the daily Google Sheets workflow.
- `hiring_tracker.py` is a historical Excel exporter. Always require an explicit
  `--workbook`; never use its older schema to update the live tracker.
- `tests/` contains offline behavioral tests with fictional data and simulated
  browser state. `docs/tracker-workflow.md` defines the native tracker contract.
- Read `README.md` for setup and supported platforms. Use the example JSON files
  for new configurations; do not replace existing private configuration.

## Verification

Install development dependencies and run the offline checks:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m outreach --config settings.example.json queries
.venv/bin/python -m outreach --config settings.example.json demo
```

For behavior changes, add a regression test that fails before the fix and passes
afterward. Keep tests independent of private files, live accounts and the network.
Preserve preview-only defaults, saved-session reuse and uncertain-send recovery.
CI runs offline tests on Python 3.10 and 3.12; it never performs live outreach or
daily research. Passing offline tests does not validate live browser selectors or
the spreadsheet's separate 149-check suite.

## Daily tracker

Use the private `tracker_config.json` to locate the existing Google Sheet and its
tabs. If it is absent or contains placeholders, ask for the intended existing
tracker. Never infer a spreadsheet ID or create a replacement without a request.

Read `docs/tracker-workflow.md` and the live metadata, settings and records before
any update. Preserve direct spreadsheet edits and personal application progress.
The sheet is the source of truth; local files are pointers, not a second runtime.

Daily research is initiated on request. Use the Google Drive/Sheets connector to
edit the existing sheet. Never run networking outreach, send applications or
repeatedly log in as part of a daily tracker update.

Keep private settings, resumes, research exports, browser cookies, databases and
personal agent instructions out of Git. Use fictional examples in tests.
