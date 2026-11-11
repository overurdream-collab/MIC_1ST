"""Phase 1 rules-only audit: green/red partition, reasons and frozen outputs."""
from decimal import Decimal
from io import BytesIO
import pytest
from openpyxl import load_workbook
from sqlalchemy.orm import Session
from tests.api.conftest import api_factory, synthetic_password_hash
from tests.features.conftest import features_api
from tests.workspace_v2.test_access_and_registry import workspace, grants
from tests.workspace_v2.test_mapping_and_results import mapping, upload_batch
from mcias.models import User
from mcias.seed.linked_references import seed_linked_references, sample_workbook


@pytest.fixture
def linked(workspace, monkeypatch):
    api = workspace
    monkeypatch.setenv('MCIAS_ENVIRONMENT', 'development')
    monkeypatch.setenv('MCIAS_LAPTOP_TEST_MODE', 'synthetic_localhost')
    with Session(api.engine) as db:
        result = seed_linked_references(db, db.get(User, api.ids['root']))
        db.commit()
    api.linked = result['records']
    return api


def import_phase1(api, updates=None, at=None):
    raw = sample_workbook(at)
    if updates:
        book = load_workbook(BytesIO(raw))
        sheet = book.active
        columns = {cell.value: cell.column for cell in sheet[1]}
        for (row, header), value in updates.items():
            sheet.cell(row, columns[header]).value = value
        output = BytesIO()
        book.save(output)
        raw = output.getvalue()
    batch, doc = upload_batch(api, raw, 'SYN-phase1-claims.xlsx')
    base = '/api/v1/claims/batches/' + batch
    response = api.client.put(base + '/mapping', json=mapping(), headers=api.headers['root'])
    assert response.status_code == 200, response.text
    response = api.client.post(base + '/resume', headers=api.headers['root'])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'imported' and result['imported_count'] == 4, result
    return batch, raw


def run_phase1(api, batch, headers=None):
    response = api.client.post('/api/v1/phase1/batches/' + batch + '/run',
                               headers=headers or api.headers['root'])
    assert response.status_code == 201, response.text
    return response.json()


def phase1_lines(api, result_id, headers=None, status=None):
    query = '?limit=100' + ('' if status is None else '&status=' + status)
    response = api.client.get(f'/api/v1/phase1/results/{result_id}/lines{query}',
                              headers=headers or api.headers['root'])
    assert response.status_code == 200, response.text
    return response.json()['items']


def phase1_file(api, result_id, color, headers=None):
    response = api.client.get(f'/api/v1/phase1/results/{result_id}/{color}',
                              headers=headers or api.headers['root'])
    assert response.status_code == 200, response.text
    return load_workbook(BytesIO(response.content))


def first_line(api, result, invoice='SYN-INVOICE-1', service='SYN زيارة طبيب'):
    for line in phase1_lines(api, result['id']):
        values = line['values']
        if values.get('رقم الفاتورة') == invoice and values.get('الخدمة') == service:
            return line
    raise AssertionError('phase 1 line not found')


def test_valid_sample_produces_green_and_empty_red(linked):
    batch, raw = import_phase1(linked)
    result = run_phase1(linked, batch)
    summary = result['summary']
    assert summary['total_lines'] == 4
    assert summary['accepted'] == 4 and summary['rejected'] == 0
    assert summary['duplicates'] == 0
    assert summary['green_lines'] == 4 and summary['red_lines'] == 0
    assert summary['financially_final'] is False
    lines = phase1_lines(linked, result['id'])
    assert len(lines) == 4
    assert all(line['status'] == 'accepted' and not line['reasons'] for line in lines)
    green = phase1_file(linked, result['id'], 'green')
    rows = list(green.active.values)
    assert len(rows) == 5
    assert rows[0][12] == 'الحالة' and rows[0][13] == 'الصافي المحتسب'
    assert rows[0][-1] == 'ملاحظات'
    assert 'سبب المدخلات' not in rows[0]
    assert all(row[12] == 'مطابق مبدئي' for row in rows[1:])
    red = phase1_file(linked, result['id'], 'red')
    assert red.active.max_row == 1
    assert 'سبب المدخلات' in list(red.active.values)[0]


def test_missing_net_is_computed_and_accepted(linked):
    batch, _ = import_phase1(linked, {(2, 'الصافي'): None})
    result = run_phase1(linked, batch)
    assert result['summary']['accepted'] == 4 and result['summary']['rejected'] == 0
    line = first_line(linked, result)
    assert Decimal(line['computed']['net_amount']) == Decimal('900')
    assert any('الصافي' in note for note in line['notes'])
    green = phase1_file(linked, result['id'], 'green')
    row = next(row for row in green.active.values
               if row[0] == 'SYN-INVOICE-1' and row[5] == 'SYN زيارة طبيب')
    assert Decimal(row[13]) == Decimal('900')
    assert row[11] is None


def test_missing_copay_is_computed_from_percent(linked):
    batch, _ = import_phase1(linked, {(2, 'إجمالي التحمل'): None})
    result = run_phase1(linked, batch)
    assert result['summary']['accepted'] == 4
    line = first_line(linked, result)
    assert Decimal(line['computed']['copay_amount']) == Decimal('100')
    assert any('التحمل' in note for note in line['notes'])


def test_wrong_price_rejects_with_pricing_reason(linked):
    batch, _ = import_phase1(linked, {(2, 'السعر'): '1200'})
    result = run_phase1(linked, batch)
    assert result['summary']['accepted'] == 3 and result['summary']['rejected'] == 1
    line = first_line(linked, result)
    assert line['status'] == 'rejected'
    assert line['reasons']['tariff']
    assert any('السعر' in message or 'التسعيرة' in message
               for message in line['reasons']['tariff'])
    red = phase1_file(linked, result['id'], 'red')
    rows = list(red.active.values)
    tariff_index = rows[0].index('سبب التسعيرة')
    row = next(row for row in rows[1:]
               if row[0] == 'SYN-INVOICE-1' and row[5] == 'SYN زيارة طبيب')
    assert row[tariff_index]


def test_unknown_beneficiary_rejects_with_beneficiary_reason(linked):
    batch, _ = import_phase1(linked, {(2, 'الرقم التأميني'): 'SYN-UNKNOWN'})
    result = run_phase1(linked, batch)
    assert result['summary']['rejected'] == 1
    line = first_line(linked, result)
    assert line['reasons']['beneficiary']
    assert any('المستفيد' in message for message in line['reasons']['beneficiary'])


def test_duplicate_line_is_rejected_with_duplicate_reason(linked):
    duplicate = {
        (3, 'رقم الفاتورة'): 'SYN-INVOICE-1', (3, 'الرقم التأميني'): 'SYN-INS-EMPLOYEE',
        (3, 'اسم المستفيد'): 'SYN موظف تجريبي', (3, 'الدرجة'): 'B',
        (3, 'مقدم الخدمة'): 'SYN مستشفى النور', (3, 'الخدمة'): 'SYN زيارة طبيب',
        (3, 'السعر'): '1000', (3, 'المطلوب'): '1000', (3, 'نسبة التحمل'): '10',
        (3, 'إجمالي التحمل'): '100', (3, 'الصافي'): '900',
    }
    batch, _ = import_phase1(linked, duplicate)
    result = run_phase1(linked, batch)
    assert result['summary']['accepted'] == 3
    assert result['summary']['rejected'] == 1
    assert result['summary']['duplicates'] == 1
    rejected = phase1_lines(linked, result['id'], status='rejected')
    assert len(rejected) == 1 and rejected[0]['reasons']['duplicate']
    assert any('مكرر' in message for message in rejected[0]['reasons']['duplicate'])
    red = phase1_file(linked, result['id'], 'red')
    rows = list(red.active.values)
    duplicate_index = rows[0].index('سبب التكرار')
    assert rows[1][duplicate_index]


def test_red_file_lists_each_reason_in_its_own_column(linked):
    batch, _ = import_phase1(linked, {(2, 'السعر'): '1200', (2, 'الرقم التأميني'): 'SYN-UNKNOWN'})
    result = run_phase1(linked, batch)
    line = first_line(linked, result)
    assert line['reasons']['tariff'] and line['reasons']['beneficiary']
    red = phase1_file(linked, result['id'], 'red')
    rows = list(red.active.values)
    assert 'سبب التسعيرة' in rows[0] and 'سبب المستفيد' in rows[0]
    row = next(row for row in rows[1:]
               if row[0] == 'SYN-INVOICE-1' and row[5] == 'SYN زيارة طبيب')
    assert row[rows[0].index('سبب التسعيرة')]
    assert row[rows[0].index('سبب المستفيد')]


def test_result_is_idempotent(linked):
    batch, _ = import_phase1(linked)
    first = run_phase1(linked, batch)
    second = run_phase1(linked, batch)
    assert second['id'] == first['id']
    assert second['fingerprint'] == first['fingerprint']
    assert second['summary'] == first['summary']


def test_viewer_cannot_run_or_export_but_can_list(linked):
    response = grants(linked, ['reports.read', 'claims.read'], user='viewer')
    assert response.status_code == 200, response.text
    batch, _ = import_phase1(linked)
    result = run_phase1(linked, batch)
    denied = linked.client.post('/api/v1/phase1/batches/' + batch + '/run',
                                headers=linked.headers['viewer'])
    assert denied.status_code == 403
    listing = linked.client.get('/api/v1/phase1/results', headers=linked.headers['viewer'])
    assert listing.status_code == 200
    assert any(item['id'] == result['id'] for item in listing.json()['items'])
    lines = linked.client.get(f"/api/v1/phase1/results/{result['id']}/lines",
                              headers=linked.headers['viewer'])
    assert lines.status_code == 200
    export = linked.client.get(f"/api/v1/phase1/results/{result['id']}/green",
                               headers=linked.headers['viewer'])
    assert export.status_code == 403


def test_green_and_red_partition_every_row(linked):
    batch, _ = import_phase1(linked, {(2, 'السعر'): '1200', (3, 'الرقم التأميني'): 'SYN-UNKNOWN'})
    result = run_phase1(linked, batch)
    summary = result['summary']
    assert summary['accepted'] + summary['rejected'] == summary['total_lines'] == 4
    green = phase1_file(linked, result['id'], 'green')
    red = phase1_file(linked, result['id'], 'red')
    assert green.active.max_row - 1 == summary['accepted']
    assert red.active.max_row - 1 == summary['rejected']
