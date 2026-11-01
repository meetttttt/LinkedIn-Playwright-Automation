# Native Google Sheets tracker contract

Read the private `tracker_config.json` for the existing spreadsheet ID and tab
names. This document describes schema v5; live headers and settings take
precedence. It does not provision a spreadsheet or duplicate its runtime.

## Structure and live settings

- Two visible tabs: Daily Overview and Opportunities. Tracker Checks is hidden.
- Opportunities is the sole record table. Saved filters provide Shortlist,
  Check Before Applying, Closed, daily views and application progress.
- Do not recreate Run Log, an extra Overview or snapshot record tabs.
- Read Daily Overview!A113:H156 before each update. Target days comes from B5.
- Research all titles in B115, cities in B116 and employer scope in B118.
- B129 specifies full-time non-contract work. Contract, fixed-term, part-time,
  temporary and freelance roles are excluded even when hours are full-time.
- B141:B144 holds minimum/preferred annual INR CTC and undisclosed-pay policy.
  Preferred upper CTC is not a hard ceiling. Never use fixture values as settings.
- B147:B150 specifies the workweek, shift and timezone. Five days per week does
  not establish Monday-Friday, and IST alone does not establish daytime hours.
- B151:B153 holds approximate mid-size headcount guidance and employer evidence
  requirements. Verify identity and operations; headcount is not a quality rating.

## Record and input boundaries

The native table covers A5:BF1000. Row 6 anchors calculations in C:D, G:J, T:U,
AD:AE, AH:AJ, AW, BC and BF. Never write into calculated columns or spill cells.
Choose the next unused input row, not a blind append to a calculated range.

Column C opens AC's original LinkedIn post/reference, falls back to AS, or shows
No link. Preserve the original URL and source evidence.

Added on (K), Record ID (AZ) and the initial numeric Target days (O) are permanent
literals. Last seen (AB) is the actual research observation date; Last import
date (BA) records a successful import separately. Keep Target days untyped in
the native table so strict whole-number validation (1–365) remains available.
Copy that validation to new rows.

Salary X:Y is annual INR LPA. BB records CTC, Fixed pay, Unspecified or Unknown;
BC calculates eligibility. Compare with total CTC only when the source confirms
that basis. Unknown pay remains unverified according to the live policy.

BD Workweek and BE Shift (IST) are source-backed dropdown inputs. Use Unknown
unless the source establishes the classification; retain wording in AN and
explanations in AK. BF calculates schedule eligibility. Incompatible schedules
make active records Not suitable / No action. Unknown schedules block readiness.

## Update and verification procedure

1. Read metadata, headers, current records and all live settings. Require
   Tracker Checks!F3 = PASS, H3 = 88, K3 = 14, O3 = 15 and R3 = 32.
2. Normalize post identity using the canonical activity ID, or exact case-sensitive
   short-link path when unresolved. Compare vacancy identity separately using a
   requisition ID or specific application URL. Generic careers pages are not IDs.
3. Match existing records before writing. Preserve application stage, personal
   notes, applied dates, first-added dates, target days and employer deadlines.
   A sourced earlier employer deadline may shorten the personal target.
4. Missing incoming facts never erase known data. Older observations do not
   replace newer evidence. Keep records absent from subsequent research.
5. Write new records only to empty input rows. Capture source-backed evidence,
   compensation basis and schedule classifications. Never invent deadlines or
   mark requirements confirmed from inference alone.
6. Re-read written cells. Require all 149 checks to pass and no data issues.
   Simulate the unchanged incoming batch again: zero new rows and no changes to
   personal fields. Verify dropdowns, formulas, table/filter ranges and validation.
7. Only after validation, update the successful-update date and Last import date.
   Do not add a run log. Stop at row 1000 unless all dependent ranges are extended.

Apply by is the earlier of Added on + Target days and a sourced employer deadline.
It remains visible after applying or closing; urgency becomes No action.
Application progress and vacancy availability are separate. An applied record
must have the user's actual application date; never guess it.

Daily research and imports require a connected agent and an initiated request.
The repository's Python tests and CI do not execute the native 149-check suite,
research LinkedIn or modify the spreadsheet.
