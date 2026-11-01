# Repository preparation alignment

## Scope and contract

Publish the supporting Python source, fictional examples, tests, setup guidance
and native Google Sheets workflow contract. Keep the existing live spreadsheet
as the master and preserve local personal configurations and browser sessions.
Remote repository destination and visibility are user-selected.

This repository does not contain live hiring records, resumes, application
history, account identity, browser authentication or a spreadsheet data export.
It does not provision another tracker or enable live outreach from CI.

## Edge cases and verification

| Case | Handling | Verification |
| --- | --- | --- |
| Fresh checkout has no private settings | Use a fictional example in tests; show setup instructions in the CLI | Offline missing-config test and clean-checkout suite |
| No private tracker pointer | Legacy exporter still requires an explicit workbook path | Regression test runs the script in an isolated directory |
| Generated private files present locally | Ignore them and publish an explicit reviewed source set | Git candidate-file and staged-content inspection |
| Existing personal workspace | Preserve settings, sessions and the live spreadsheet | Local configuration untouched; no live browser or Sheets writes |
| Existing remote history | Inspect it before publishing; preserve files and avoid force-push | Remote metadata and commit/tree readback |

## Execution

Prepare shareable configuration examples and documentation; make tests independent
of personal files; verify behavioral fixes with failing tests first; run the suite
from a clean source-only checkout; inspect the commit contents; publish to the
selected repository and verify the remote result. If authentication or workspace
permissions block publishing, report the exact remaining step without claiming
the push succeeded.
