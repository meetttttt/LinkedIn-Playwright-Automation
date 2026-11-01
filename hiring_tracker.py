"""Export a cumulative Excel hiring tracker from reviewed research JSON.

The active tracker is in Google Sheets (tracker_config.json). Its live updates
use the Google Sheets connector; this legacy exporter requires --workbook when
Google Sheets is selected. It does not browse or send applications/messages.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import to_excel
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'tracker_config.json'
NAVY, TEAL, INK, PALE = '17324D', '087F8C', '243746', 'EFF5F9'
INACTIVE = {'Applied', 'Interview', 'Not interested', 'Closed', 'Do not apply — closed'}
COLS = [
    ('Added on', 'added', 15), ('Apply by', 'due', 15), ('Due state', 'due_state', 16),
    ('Days left', 'days_left', 11), ('Application status', 'application_status', 20),
    ('Employer', 'company', 29), ('Role', 'role', 43), ('Location', 'city', 25),
    ('Research category', 'status', 23), ('Fit', 'fit', 17), ('Target days', 'target_days', 12),
    ('Employer deadline', 'employer_deadline', 18), ('Last seen', 'last_seen', 15),
    ('LinkedIn post URL', 'post', 38), ('Apply / contact', 'apply', 49),
    ('Experience required', 'experience', 29), ('Work arrangement', 'work', 33),
    ('Fit notes and conditions', 'notes', 65), ('Availability evidence', 'availability', 39),
    ('Employer category', 'type', 41), ('Post author', 'author', 27),
    ('Author profile URL', 'profile', 45), ('Supporting source URL', 'source', 49),
    ('Post age when first captured', 'age', 23), ('My notes', 'personal_notes', 45),
    ('Applied on', 'applied_on', 15),
]
FACTS = {'company', 'role', 'city', 'status', 'fit', 'post', 'apply', 'experience',
         'work', 'notes', 'availability', 'type', 'author', 'profile', 'source'}


def as_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def post_key(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise ValueError(f'Invalid post URL: {url!r}')
    host = parts.hostname.lower()
    if host.endswith('.linkedin.com') or host == 'linkedin.com':
        match = re.search(r'(?:activity[:\-]|ugcPost:|share:)(\d{15,})', parts.path)
        if match:
            return 'linkedin:' + match.group(1)
        host = 'www.linkedin.com'
    return urlunsplit(('https', host, parts.path.rstrip('/'), '', ''))


def inactive(record):
    return (record.get('application_status') in INACTIVE
            or record.get('status') == 'Closed / excluded'
            or bool(record.get('applied_on')))


def due_values(record, today):
    if inactive(record):
        return None, 'No action', None
    due = record['added'] + timedelta(days=int(record['target_days']))
    if record.get('employer_deadline'):
        due = min(due, record['employer_deadline'])
    days = (due - today).days
    return due, ('Overdue' if days < 0 else 'Due today' if days == 0 else 'Upcoming'), days


def read_master(path):
    wb = load_workbook(path, data_only=False)
    ws = wb['Opportunities']
    headers = [cell.value for cell in ws[5]]
    if headers != [c[0] for c in COLS]:
        raise ValueError('Tracker headers changed. Restore the expected headers before importing.')
    rows = []
    for values in ws.iter_rows(min_row=6, values_only=True):
        if not values[13]:
            continue
        record = {key: value for (_, key, _), value in zip(COLS, values)
                  if key not in {'due', 'due_state', 'days_left'}}
        for key in ['added', 'last_seen', 'employer_deadline', 'applied_on']:
            record[key] = as_date(record.get(key))
        rows.append(record)
    logs = list(wb['Run Log'].iter_rows(min_row=6, values_only=True))
    settings = {'default_days': int(wb['Overview']['B5'].value),
                'as_of': as_date(wb['Overview']['B7'].value)}
    return rows, settings, logs


def migrate_original(path):
    wb = load_workbook(path, data_only=False)
    rows = []
    old_keys = ['company', 'role', 'city', 'fit', 'experience', 'work', 'age', 'post',
                'apply', 'notes', 'availability', 'type', 'author', 'profile', 'source',
                'observed_date', 'status', 'application_status']
    for ws in wb:
        if ws.title == 'Overview':
            continue
        for values in ws.iter_rows(min_row=6, values_only=True):
            if not values[7]:
                continue
            record = dict(zip(old_keys, values))
            record['added'] = record['last_seen'] = as_date(record.pop('observed_date'))
            record['target_days'] = 7
            record['employer_deadline'] = record['applied_on'] = None
            record['personal_notes'] = ''
            if record['application_status'] == 'Do not apply — closed':
                record['application_status'] = 'Closed'
            rows.append(record)
    return rows


def merge_records(existing, incoming, today, default_days=7):
    records = [dict(row) for row in existing]
    indexed = {post_key(row['post']): row for row in records}
    if len(indexed) != len(records):
        raise ValueError('Existing tracker has duplicate post identities; resolve them before import.')
    added, changed = 0, set()
    for item in incoming:
        key = post_key(item['post'])
        observed = as_date(item.get('observed_date')) or today
        if observed > today:
            raise ValueError('Observation date is in the future.')
        if key not in indexed:
            row = {k: item.get(k, '') for k in FACTS}
            row.update(added=today, last_seen=observed, target_days=default_days,
                       employer_deadline=as_date(item.get('employer_deadline')),
                       applied_on=None, personal_notes='', age=item.get('age', 'Not recorded'),
                       application_status='Closed' if item.get('status') == 'Closed / excluded' else 'Not started')
            records.append(row)
            indexed[key] = row
            added += 1
        else:
            row = indexed[key]
            # An old research file must not roll back fresher availability data.
            if observed < row['last_seen']:
                continue
            before = dict(row)
            for field in FACTS - {'post'}:
                if item.get(field) not in (None, ''):
                    row[field] = item[field]
            row['last_seen'] = observed
            if item.get('employer_deadline'):
                new_deadline = as_date(item['employer_deadline'])
                row['employer_deadline'] = min(row['employer_deadline'], new_deadline) if row.get('employer_deadline') else new_deadline
            if row != before:
                changed.add(key)
    return records, added, len(changed)


def plain(cell, value):
    cell.value = value
    if isinstance(value, str):
        cell.data_type = 's'  # imported notes cannot become spreadsheet formulas


def base_sheet(wb, name, title, note, widths):
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 85
    for c, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=min(8, len(widths)))
    ws.cell(1, 1, title).font = Font(name='Calibri', size=21, bold=True, color='FFFFFF')
    for c in range(1, len(widths) + 1):
        ws.cell(1, c).fill = PatternFill('solid', fgColor=NAVY)
    ws.row_dimensions[1].height = 38
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=min(10, len(widths)))
    ws.cell(2, 1, note).font = Font(name='Calibri', size=11, color=INK)
    ws.cell(2, 1).alignment = Alignment(wrap_text=True, vertical='center')
    ws.row_dimensions[2].height = 32
    ws.freeze_panes = 'A6'
    return ws


def table_sheet(ws, headers, end, name):
    for col, label in enumerate(headers, 1):
        cell = ws.cell(5, col, label)
        cell.font = Font(name='Calibri', size=11, color='FFFFFF', bold=True)
        cell.fill = PatternFill('solid', fgColor=TEAL)
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    ws.row_dimensions[5].height = 34
    table = Table(displayName=name, ref=f'A5:{get_column_letter(len(headers))}{end}')
    table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
    ws.add_table(table)


def save_cached(wb, path, caches):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(suffix='.xlsx', dir=path.parent)
    os.close(fd)
    tmp = Path(tmpname)
    try:
        wb.save(tmp)
        ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(tmp) as z:
            members = {n: z.read(n) for n in z.namelist()}
        for sheet_index, ws in enumerate(wb, 1):
            xmlpath = f'xl/worksheets/sheet{sheet_index}.xml'
            tree = ET.fromstring(members[xmlpath])
            for c in tree.findall('.//m:c', ns):
                key = (ws.title, c.attrib['r'])
                if key not in caches:
                    continue
                value = caches[key]
                if isinstance(value, date):
                    value = to_excel(value)
                c.set('t', 'str' if isinstance(value, str) or value is None else 'n')
                v = c.find('m:v', ns)
                if v is None:
                    v = ET.SubElement(c, '{' + ns['m'] + '}v')
                v.text = '' if value is None else str(value)
            members[xmlpath] = ET.tostring(tree, encoding='utf-8')
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in members.items():
                z.writestr(name, data)
        load_workbook(tmp, data_only=False).close()
        if path.exists():
            backup = path.parent / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + path.name)
            backup.parent.mkdir(exist_ok=True)
            shutil.copy2(path, backup)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def build_workbook(records, settings, logs, path, today):
    wb = Workbook()
    wb.remove(wb.active)
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.properties.title = 'Daily LinkedIn job tracker'
    wb.properties.creator = 'Hiring Tracker'
    overview = base_sheet(wb, 'Overview', 'Daily job application tracker',
                          'One cumulative list • filter by Added on, Apply by, Due state and Application status.', [29, 18, 18, 18, 18, 18, 18, 18])
    ws = base_sheet(wb, 'Opportunities', 'Opportunities',
                    'Use the header filters. Blue input cells are editable. Apply by is your personal target, not a verified employer closing date.', [c[2] for c in COLS])
    ws.freeze_panes = 'F6'
    ws.merge_cells('A3:J3')
    ws['A3'] = 'Repeat imports keep Added on, application progress and notes. Last seen is the date the post was actually observed.'
    ws['A3'].font = Font(name='Calibri', size=10, color='5C6D7D')
    ws.row_dimensions[3].height = 24
    as_of = settings.get('as_of') or today
    caches = {('Overview', 'B6'): as_of}
    active_states = []
    ordered = sorted(records, key=lambda r: (r['added'], r['company']), reverse=True)
    for r, record in enumerate(ordered, 6):
        maximum_lines = 1
        for c, (_, key, width) in enumerate(COLS, 1):
            if key in {'due', 'due_state', 'days_left'}:
                continue
            value = record.get(key) or None
            cell = ws.cell(r, c)
            plain(cell, value)
            cell.font = Font(name='Calibri', size=11, color='176BB0' if key in {'application_status', 'target_days', 'employer_deadline', 'personal_notes', 'applied_on'} else INK)
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            cell.fill = PatternFill('solid', fgColor='FFFFFF' if r % 2 == 0 else PALE)
            if isinstance(value, date):
                cell.number_format = 'dd mmm yyyy'
            elif key == 'target_days':
                cell.number_format = '0'
            if isinstance(value, str) and value.startswith('https://') and ' ' not in value:
                cell.hyperlink = value
                cell.font = Font(name='Calibri', size=11, color='176BB0', underline='single')
            maximum_lines = max(maximum_lines, sum(max(1, (len(line) + int(width * 0.95) - 1) // int(width * 0.95)) for line in str(value or '').split('\n')))
        skip = f'OR(E{r}="Applied",E{r}="Interview",E{r}="Not interested",E{r}="Closed",I{r}="Closed / excluded",Z{r}<>"")'
        ws.cell(r, 2, f'=IF({skip},"",IF(L{r}="",A{r}+K{r},MIN(A{r}+K{r},L{r})))')
        ws.cell(r, 3, f'=IF(B{r}="","No action",IF(B{r}<\'Overview\'!$B$6,"Overdue",IF(B{r}=\'Overview\'!$B$6,"Due today","Upcoming")))')
        ws.cell(r, 4, f'=IF(B{r}="","",B{r}-\'Overview\'!$B$6)')
        ws.cell(r, 2).number_format = 'dd mmm yyyy'
        ws.cell(r, 4).number_format = '0;[Red]-0;0'
        for c in (2, 3, 4):
            ws.cell(r, c).font = Font(name='Calibri', size=11, color=INK, bold=c == 3)
            ws.cell(r, c).alignment = Alignment(vertical='top', wrap_text=True)
        due, state, days = due_values(record, as_of)
        active_states.append((record, state))
        for col, value in [('B', due), ('C', state), ('D', days)]:
            caches[('Opportunities', f'{col}{r}')] = value
        ws.row_dimensions[r].height = min(240, max(72, maximum_lines * 15 + 18))
    end = 5 + len(records)
    table_sheet(ws, [c[0] for c in COLS], end, 'OpportunitiesTable')
    for state, fill, color in [('Overdue', 'FBE5E5', '9A2929'), ('Due today', 'FFF2D8', '805A10'), ('Upcoming', 'E2F2E8', '175C35'), ('No action', 'E8EDF2', '5C6D7D')]:
        ws.conditional_formatting.add(f'B6:D{end}', FormulaRule(formula=[f'$C6="{state}"'], fill=PatternFill('solid', fgColor=fill), font=Font(color=color)))
    validation = DataValidation(type='list', formula1='"Not started,Considering,Applied,Interview,Not interested,Closed"')
    validation.error = 'Choose a status from the dropdown.'
    validation.showErrorMessage = True
    ws.add_data_validation(validation)
    validation.add(f'E6:E{end}')
    days_validation = DataValidation(type='whole', operator='between', formula1='1', formula2='365', allow_blank=False)
    days_validation.showErrorMessage = True
    days_validation.error = 'Enter a whole number of days between 1 and 365.'
    ws.add_data_validation(days_validation)
    days_validation.add(f'K6:K{end}')
    date_validation = DataValidation(type='date', operator='between', formula1='DATE(2000,1,1)', formula2='DATE(2100,12,31)', allow_blank=True)
    date_validation.showErrorMessage = True
    ws.add_data_validation(date_validation)
    date_validation.add(f'L6:L{end}')
    date_validation.add(f'Z6:Z{end}')

    def note(row, text, fill=None, height=38):
        overview.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        overview.cell(row, 1, text).font = Font(name='Calibri', size=11, color='FFFFFF' if fill else INK, bold=bool(fill))
        overview.cell(row, 1).alignment = Alignment(wrap_text=True, vertical='center')
        if fill:
            for c in range(1, 9):
                overview.cell(row, c).fill = PatternFill('solid', fgColor=fill)
        overview.row_dimensions[row].height = height

    for row, label, value, explanation in [
        (5, 'Default target days', settings['default_days'], 'For new records. Each existing row keeps its Target days value.'),
        (6, 'As-of date', '=IF(B7="",TODAY(),B7)', 'Automatic: current date on your computer. Use India local time.'),
        (7, 'Optional date override', settings.get('as_of'), 'Leave blank for today; set a date to review past or future deadlines.'),
        (8, 'Last tracker update', today, 'Last seen on each row is separate from this update date.'),
    ]:
        overview.cell(row, 1, label)
        overview.cell(row, 2, value)
        overview.merge_cells(start_row=row, start_column=3, end_row=row, end_column=8)
        overview.cell(row, 3, explanation)
        for c in (1, 2, 3):
            overview.cell(row, c).font = Font(name='Calibri', size=11, color='176BB0' if c == 2 and row in (5, 7) else INK)
            overview.cell(row, c).alignment = Alignment(wrap_text=True, vertical='center')
        overview.row_dimensions[row].height = 35
        overview.cell(row, 2).number_format = '0' if row == 5 else 'dd mmm yyyy'
    default_validation = DataValidation(type='whole', operator='between', formula1='1', formula2='365')
    default_validation.showErrorMessage = True
    overview.add_data_validation(default_validation)
    default_validation.add('B5')
    override_validation = DataValidation(type='date', operator='between', formula1='DATE(2000,1,1)', formula2='DATE(2100,12,31)', allow_blank=True)
    override_validation.showErrorMessage = True
    overview.add_data_validation(override_validation)
    override_validation.add('B7')
    note(10, 'APPLICATION QUEUE', TEAL, 28)
    metrics = [
        ('Total tracked posts', '=COUNTA(OpportunitiesTable[LinkedIn post URL])', len(records)),
        ('Added on the as-of date', '=COUNTIF(OpportunitiesTable[Added on],B6)', sum(r['added'] == as_of for r in records)),
        ('Pending applications', '=COUNTIF(OpportunitiesTable[Due state],"Upcoming")+COUNTIF(OpportunitiesTable[Due state],"Due today")+COUNTIF(OpportunitiesTable[Due state],"Overdue")', sum(not inactive(r) for r in records)),
        ('Due today', '=COUNTIF(OpportunitiesTable[Due state],"Due today")', sum(s == 'Due today' for _, s in active_states)),
        ('Overdue', '=COUNTIF(OpportunitiesTable[Due state],"Overdue")', sum(s == 'Overdue' for _, s in active_states)),
        ('Shortlist still pending', '=COUNTIFS(OpportunitiesTable[Research category],"Shortlist",OpportunitiesTable[Due state],"<>No action")', sum(r['status'] == 'Shortlist' and not inactive(r) for r in records)),
    ]
    for row, (label, formula, count) in enumerate(metrics, 11):
        overview.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        overview.cell(row, 1, label).font = Font(name='Calibri', size=12, color=INK)
        overview.cell(row, 1).alignment = Alignment(vertical='center')
        overview.merge_cells(start_row=row, start_column=7, end_row=row, end_column=8)
        overview.cell(row, 7, formula).font = Font(name='Calibri', size=18, bold=True, color=TEAL)
        overview.cell(row, 7).alignment = Alignment(horizontal='center', vertical='center')
        overview.cell(row, 7).number_format = '0'
        overview.row_dimensions[row].height = 32
        for c in range(1, 9):
            overview.cell(row, c).fill = PatternFill('solid', fgColor=PALE)
        caches[('Overview', f'G{row}')] = count
    note(18, 'DAILY WORKFLOW', TEAL, 28)
    note(19, '1. Add the latest reviewed research to this same tracker. New posts get today’s Added on date; existing posts keep their dates, progress and personal notes.')
    note(20, '2. Filter Added on for a day or date range. Filter Due state for Due today / Overdue. Filter Research category = Shortlist to focus on recommended options.')
    note(21, '3. Update Application status and optionally Applied on / My notes. Applied, Interview, Not interested and Closed records have no active application due date.')
    note(23, 'DUE-DATE RULES AND EVIDENCE', TEAL, 28)
    note(24, 'Apply by = Added on + Target days (default 7 calendar days), shortened by an earlier Employer deadline. Blank employer deadlines mean unknown, not unlimited availability.')
    note(25, 'Example: added 30 Oct → personal target 6 Nov. On 6 Nov: Due today. On 7 Nov: Overdue. A repeat sighting does not restart the seven-day period.')
    note(26, 'Initial research: 23–30 Oct 2026, MNCs and startups, Pune / Mumbai / Bengaluru / Jammu. 13 records retained; none qualified for Jammu in the inspected results. Search was non-exhaustive.')
    note(27, 'Post age is the original rounded value. Last seen is observation evidence, not proof a vacancy remains open. Review availability and fit notes before applying.')
    note(28, 'The tracker does not schedule daily searches or send applications. Future daily research updates this same workbook. Save and close the workbook before an import.')
    overview.freeze_panes = 'A10'
    overview.print_area = 'A1:H28'

    log = base_sheet(wb, 'Run Log', 'Research import history', 'One row per import. No LinkedIn searches or outreach are triggered by importing data.', [19, 48, 15, 15, 15, 15, 23])
    for r, values in enumerate(logs, 6):
        for c, value in enumerate(values[:7], 1):
            cell = log.cell(r, c)
            plain(cell, value)
            cell.font = Font(name='Calibri', size=11, color=INK)
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            if c == 1:
                cell.number_format = 'dd mmm yyyy'
            elif c >= 3 and c <= 6:
                cell.number_format = '0'
        log.row_dimensions[r].height = 44
    table_sheet(log, ['Import date', 'Research file', 'Input posts', 'New posts', 'Updated posts', 'Total tracked', 'Batch reference'], 5 + len(logs), 'ImportLog')
    for sheet in wb:
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = 'landscape'
        sheet.page_setup.fitToWidth = 1 if sheet.title != 'Opportunities' else 4
        sheet.page_setup.fitToHeight = 0
        sheet.print_title_rows = '1:5'
        sheet.sheet_properties.tabColor = TEAL
    save_cached(wb, path, caches)
    return caches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Reviewed research JSON with a rows array')
    parser.add_argument('--workbook', type=Path, help='Explicit local export path; not a Google Sheets sync')
    parser.add_argument('--migrate', type=Path, help='Existing one-off workbook; used only when master does not exist')
    parser.add_argument('--date', type=date.fromisoformat, help='Import date; defaults to today in India')
    args = parser.parse_args()
    if args.workbook is None:
        config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        location = config.get('spreadsheet_url') or 'see your private tracker_config.json'
        parser.error(
            'The master tracker is in Google Sheets: '
            + location
            + '. Update it with the Google Sheets connector. No local workbook was changed. '
            'Use --workbook only for an explicitly requested local export.'
        )
    today = args.date or datetime.now(ZoneInfo('Asia/Kolkata')).date()
    raw = args.input.read_bytes()
    incoming = json.loads(raw)['rows']
    if args.workbook.exists():
        records, settings, logs = read_master(args.workbook)
    else:
        records = migrate_original(args.migrate) if args.migrate else []
        settings, logs = {'default_days': 7, 'as_of': None}, []
    if not 1 <= settings['default_days'] <= 365:
        raise ValueError('Default target days must be between 1 and 365.')
    initial_count = len(records)
    records, added, updated = merge_records(records, incoming, today, settings['default_days'])
    if args.migrate and not args.workbook.exists():
        added += initial_count
    if not records:
        raise ValueError('No records to export.')
    for record in records:
        if not record.get('added') or not record.get('last_seen') or not 1 <= int(record['target_days']) <= 365:
            raise ValueError('Invalid tracking date or target days.')
    logs.append((today, args.input.name, len(incoming), added, updated, len(records), hashlib.sha256(raw).hexdigest()[:12]))
    build_workbook(records, settings, logs, args.workbook, today)
    print(json.dumps({'workbook': str(args.workbook.resolve()), 'new': added, 'updated': updated, 'total': len(records)}))


if __name__ == '__main__':
    main()
