import tempfile
import shutil
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from hiring_tracker import ROOT, build_workbook, due_values, merge_records, post_key, read_master


class HiringTrackerTests(unittest.TestCase):
    def test_clean_checkout_requires_explicit_export_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            script = checkout / 'hiring_tracker.py'
            shutil.copyfile(ROOT / 'hiring_tracker.py', script)
            result = subprocess.run(
                [sys.executable, str(script), '--input', 'not-read.json'],
                capture_output=True, text=True, cwd=checkout,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn('--workbook', result.stderr)
            self.assertNotIn('FileNotFoundError', result.stderr)
            self.assertFalse((checkout / 'outputs').exists())

    def test_default_command_does_not_write_local_google_master(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'hiring_tracker.py'), '--input', 'not-read.json'],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('The master tracker is in Google Sheets:', result.stderr)
        self.assertIn('No local workbook was changed.', result.stderr)
        self.assertNotIn('FileNotFoundError', result.stderr)

    def setUp(self):
        self.today = date(2026, 10, 30)
        self.item = {'post': 'https://lnkd.in/p/Example', 'company': 'Example',
                     'role': 'Recruiter', 'status': 'Shortlist', 'fit': 'High',
                     'observed_date': '2026-10-30'}
        self.rows, _, _ = merge_records([], [self.item], self.today)

    def test_default_due_boundary(self):
        row = self.rows[0]
        self.assertEqual(due_values(row, self.today), (date(2026, 11, 6), 'Upcoming', 7))
        self.assertEqual(due_values(row, date(2026, 11, 6))[1:], ('Due today', 0))
        self.assertEqual(due_values(row, date(2026, 11, 7))[1:], ('Overdue', -1))

    def test_employer_deadline_only_shortens(self):
        row = self.rows[0]
        row['employer_deadline'] = date(2026, 11, 2)
        self.assertEqual(due_values(row, self.today)[0], date(2026, 11, 2))
        row['employer_deadline'] = date(2026, 12, 1)
        self.assertEqual(due_values(row, self.today)[0], date(2026, 11, 6))

    def test_inactive_records_are_never_overdue(self):
        row = self.rows[0]
        for state in ['Applied', 'Interview', 'Not interested', 'Closed']:
            row['application_status'] = state
            self.assertEqual(due_values(row, date(2027, 1, 1)), (None, 'No action', None))
        row['application_status'] = 'Not started'
        row['status'] = 'Closed / excluded'
        self.assertEqual(due_values(row, self.today), (None, 'No action', None))
        row['status'] = 'Shortlist'
        row['applied_on'] = self.today
        self.assertEqual(due_values(row, self.today), (None, 'No action', None))

    def test_repeat_import_preserves_user_work_and_dates(self):
        row = self.rows[0]
        row.update(application_status='Applied', personal_notes='Interview booked', target_days=3,
                   employer_deadline=date(2026, 11, 1), applied_on=self.today)
        new = dict(self.item, observed_date='2026-10-31', work='Hybrid')
        result, added, updated = merge_records(self.rows, [new, new], date(2026, 10, 31), 10)
        self.assertEqual((len(result), added, updated), (1, 0, 1))
        for field in ['added', 'application_status', 'personal_notes', 'target_days', 'employer_deadline', 'applied_on']:
            self.assertEqual(result[0][field], row[field])
        self.assertEqual(result[0]['last_seen'], date(2026, 10, 31))

    def test_new_rows_get_new_dates_and_default_without_altering_old(self):
        new = dict(self.item, post='https://lnkd.in/p/New', observed_date='2026-10-31')
        result, added, _ = merge_records(self.rows, [new], date(2026, 10, 31), 10)
        self.assertEqual((len(result), added), (2, 1))
        self.assertEqual(result[0]['added'], self.today)
        self.assertEqual(result[0]['target_days'], 7)
        self.assertEqual(result[1]['added'], date(2026, 10, 31))
        self.assertEqual(result[1]['target_days'], 10)

    def test_old_input_does_not_reopen_fresher_closed_vacancy(self):
        self.rows[0].update(last_seen=date(2026, 11, 1), status='Closed / excluded')
        result, added, updated = merge_records(self.rows, [self.item], date(2026, 11, 2))
        self.assertEqual((added, updated), (0, 0))
        self.assertEqual(result[0]['status'], 'Closed / excluded')
        self.assertEqual(result[0]['last_seen'], date(2026, 11, 1))

    def test_tracking_parameters_and_activity_aliases_dedupe(self):
        self.assertEqual(post_key('https://lnkd.in/p/Example?utm_source=share'), post_key(self.item['post']))
        self.assertEqual(post_key('https://www.linkedin.com/feed/update/urn:li:activity:1234567890123456789/'),
                         post_key('https://www.linkedin.com/posts/name_activity-1234567890123456789-xyz'))

    def test_workbook_roundtrip_caches_and_edit_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'tracker.xlsx'
            settings = {'default_days': 7, 'as_of': date(2026, 11, 6)}
            logs = [(self.today, 'batch.json', 1, 1, 0, 1, 'test')]
            self.rows[0]['personal_notes'] = '=DO_NOT_EXECUTE()'
            build_workbook(self.rows, settings, logs, output, self.today)
            data = load_workbook(output, data_only=True)
            self.assertEqual(data['Opportunities']['B6'].value.date(), date(2026, 11, 6))
            self.assertEqual(data['Opportunities']['C6'].value, 'Due today')
            self.assertEqual(data['Overview']['G14'].value, 1)
            formulas = load_workbook(output, data_only=False)
            self.assertEqual(formulas['Opportunities']['Y6'].data_type, 's')
            formulas['Opportunities']['E6'] = 'Applied'
            formulas.save(output)
            rows, saved_settings, saved_logs = read_master(output)
            merged, added, _ = merge_records(rows, [self.item], date(2026, 10, 31))
            self.assertEqual(added, 0)
            build_workbook(merged, saved_settings, saved_logs, output, date(2026, 10, 31))
            reread = load_workbook(output, data_only=True)
            self.assertIsNone(reread['Opportunities']['B6'].value)
            self.assertEqual(reread['Opportunities']['C6'].value, 'No action')
            self.assertEqual(len(list((Path(tmp) / 'backups').glob('*.xlsx'))), 1)


if __name__ == '__main__':
    unittest.main()
