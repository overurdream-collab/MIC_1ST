from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4
import json
from sqlalchemy import select
from mcias.audit_engine import money
from mcias.claim_batches import confirmed_mapping
from mcias.claim_normalization import (FIXED_COLUMNS, FIXED_HEADERS,
    ClaimValidationError, source_decimal)
from mcias.models import AuditLog, SourceFile
from mcias.models_claims import ClaimBatch, ClaimEntry
from mcias.reporting_g7 import canonical
from mcias.schemas.reference_links import PROVIDERS
from mcias.services import registry_service as registry
from mcias.services.claim_reference_service import ReferenceIndex
from mcias.services.rbac_service import _atomic
from mcias.services.transaction_lock import lock_record

STAGE = 'phase1_rules'
SCHEMA = 'mcias-phase1-rules-v1'
ACCEPTED = 'accepted'
REJECTED = 'rejected'
STATUS_LABELS = {ACCEPTED: 'مطابق مبدئي', REJECTED: 'مرفوض'}
REASON_CATEGORIES = (
    ('inputs', 'سبب المدخلات'),
    ('calculation', 'سبب الحساب'),
    ('beneficiary', 'سبب المستفيد'),
    ('provider_service', 'سبب المزود والخدمة'),
    ('contract', 'سبب العقد'),
    ('tariff', 'سبب التسعيرة'),
    ('cost_sharing', 'سبب التحمل'),
    ('duplicate', 'سبب التكرار'),
    ('other_references', 'سبب المرجعيات الأخرى'),
)
SOURCE_FIELDS = {
    'invoice_number': 'claim_reference',
    'beneficiary_token': 'beneficiary_token',
    'beneficiary_name': 'beneficiary_name',
    'category_code': 'category_code',
    'provider_code': 'provider_code',
    'service_code': 'service_code',
    'service_date': 'service_date',
    'unit_price': 'unit_price',
    'gross_amount': 'gross_amount',
    'copay_percent': 'copay_percent',
    'copay_amount': 'copay_amount',
    'net_amount': 'claimed_amount',
}


def _text(value):
    return ' '.join(str(value).split()) if value is not None else ''


def _source(raw, columns, field):
    header = columns.get(SOURCE_FIELDS[field]) or FIXED_COLUMNS[SOURCE_FIELDS[field]]
    value = raw.get(header) if header else None
    return '' if value is None else str(value).strip()


def _original(raw):
    return {header: '' if raw.get(header) is None else str(raw.get(header)) for header in FIXED_HEADERS}


def _add(reasons, category, message):
    if message not in reasons[category]:
        reasons[category].append(message)


def _note(notes, message):
    if message not in notes:
        notes.append(message)


def _one(records, category, reasons, missing, ambiguous):
    if len(records) == 1:
        return records[0]
    _add(reasons, category, missing if not records else ambiguous)
    return None


def _decimal(value, label, reasons, *, percent=False):
    if not value:
        return None
    try:
        return source_decimal(value, allow_percent=percent)
    except ClaimValidationError:
        _add(reasons, 'inputs', f'قيمة {label} غير صالحة')
        return None


def _scope_matches(scope, provider, service, category, token):
    scope_text = _text(scope)
    if not scope_text:
        return False
    if scope_text in ('*', 'all', 'الكل'):
        return True
    for record in (provider, service, category):
        if not record:
            continue
        values = record.get('values', {})
        if scope_text in {_text(record.get('title')), _text(values.get('provider_code')),
                          _text(values.get('service_code')), _text(values.get('category_code'))}:
            return True
    return scope_text == _text(token)


def _duplicate_key(source, computed):
    def amount(value):
        try:
            return format(source_decimal(value), 'f') if value not in (None, '') else ''
        except ClaimValidationError:
            return _text(value)

    def percent(value):
        try:
            return format(source_decimal(value, allow_percent=True), 'f') if value not in (None, '') else ''
        except ClaimValidationError:
            return _text(value)

    return (
        _text(source['beneficiary_token']),
        _text(source['provider_code']),
        _text(source['service_code']),
        _text(source['service_date']),
        amount(computed.get('unit_price') or source['unit_price']),
        amount(computed.get('gross_amount') or source['gross_amount']),
        percent(computed.get('copay_percent') or source['copay_percent']),
        amount(computed.get('copay_amount') or source['copay_amount']),
        amount(computed.get('net_amount') or source['net_amount']),
    )


def _row_label(claim, source):
    invoice = _text(source['invoice_number']) or claim.claim_reference
    locator = claim.source_locator if isinstance(claim.source_locator, dict) else {}
    row = locator.get('row')
    return f'فاتورة {invoice}' + (f' — صف {row}' if row else '')


def evaluate_line(index, claim, mapping, seen):
    reasons = {key: [] for key, _ in REASON_CATEGORIES}
    notes = []
    computed = {}
    columns = mapping.get('columns', {}) if mapping else {}
    raw = claim.source_values if isinstance(claim.source_values, dict) else {}
    source = {field: _source(raw, columns, field) for field in SOURCE_FIELDS}
    original = _original(raw)
    day = claim.service_date

    for field, label in (('invoice_number', 'رقم الفاتورة'), ('beneficiary_token', 'الرقم التأميني'),
                         ('beneficiary_name', 'اسم المستفيد'), ('category_code', 'الدرجة'),
                         ('provider_code', 'مقدم الخدمة'), ('service_code', 'الخدمة'),
                         ('service_date', 'تاريخ الخدمة')):
        if not source[field]:
            _add(reasons, 'inputs', f'{label} مفقود')

    if mapping and mapping.get('amount_basis') != 'NET':
        _add(reasons, 'inputs', 'أساس المبلغ في الربط يجب أن يكون صافياً (NET)')

    unit = _decimal(source['unit_price'], 'السعر', reasons)
    gross = _decimal(source['gross_amount'], 'المطلوب', reasons)
    copay_percent = _decimal(source['copay_percent'], 'نسبة التحمل', reasons, percent=True)
    copay_amount = _decimal(source['copay_amount'], 'إجمالي التحمل', reasons)
    net = _decimal(source['net_amount'], 'الصافي', reasons)

    if gross is None and unit is not None:
        gross = unit
        _note(notes, 'تم احتساب المطلوب من السعر (الكمية = 1).')
    if unit is None and gross is not None:
        unit = gross
        _note(notes, 'تم احتساب السعر من المطلوب (الكمية = 1).')
    if gross is None:
        _add(reasons, 'inputs', 'المطلوب غير متوفر ولا يمكن اشتقاقه')
    if unit is not None:
        computed['unit_price'] = format(unit, 'f')
    if gross is not None:
        computed['gross_amount'] = format(gross, 'f')

    if copay_percent is None and copay_amount is not None and gross not in (None, Decimal(0)):
        copay_percent = (copay_amount * Decimal(100) / gross).quantize(
            Decimal('0.0001'), rounding=ROUND_HALF_UP)
        _note(notes, 'تم احتساب نسبة التحمل من إجمالي التحمل والمطلوب.')
    if copay_amount is None and copay_percent is not None and gross is not None:
        copay_amount = gross * copay_percent / Decimal(100)
        _note(notes, 'تم احتساب إجمالي التحمل من المطلوب والنسبة.')
    if net is None and gross is not None and copay_amount is not None:
        net = gross - copay_amount
        _note(notes, 'تم احتساب الصافي من المطلوب وإجمالي التحمل.')
    if copay_percent is not None:
        computed['copay_percent'] = format(copay_percent, 'f')
    if copay_amount is not None:
        computed['copay_amount'] = format(copay_amount, 'f')
    if net is not None:
        computed['net_amount'] = format(net, 'f')

    beneficiary = _one([r for r in index.at('beneficiaries.names', day)
                        if _text(r['values'].get('beneficiary_token')) == _text(source['beneficiary_token'])],
                       'beneficiary', reasons, 'المستفيد غير مسجل أو اشتراكه غير ساري بتاريخ الخدمة',
                       'مرجع المستفيد غير وحيد')
    category = None
    if beneficiary:
        if source['beneficiary_name'] and _text(source['beneficiary_name']) != _text(beneficiary.get('title')):
            _add(reasons, 'beneficiary', 'اسم المستفيد لا يطابق المرجع: '
                 f"المتوقع {beneficiary.get('title')}، الوارد {source['beneficiary_name']}")
        expected_category = _text(beneficiary['values'].get('category_code'))
        if source['category_code'] and expected_category and _text(source['category_code']) != expected_category:
            _add(reasons, 'beneficiary', 'الدرجة لا تطابق فئة المستفيد: '
                 f"المتوقع {expected_category}، الوارد {source['category_code']}")
        category = _one([r for r in index.at('beneficiaries.categories', day)
                         if r['id'] == beneficiary.get('links', {}).get('category')
                         and _text(r['values'].get('category_code')) == expected_category],
                        'beneficiary', reasons, 'فئة المستفيد غير موجودة أو غير سارية',
                        'مرجع الفئة غير وحيد')
        if category:
            computed['category_code'] = category['values'].get('category_code')
    else:
        _add(reasons, 'cost_sharing', 'تعذر التحقق من التحمل لعدم تحديد المستفيد أو الفئة')

    provider = _one([r for section in PROVIDERS for r in index.at(section, day)
                     if _text(source['provider_code']) in (_text(r['values'].get('provider_code')), _text(r.get('title')))],
                    'provider_service', reasons, 'مقدم الخدمة غير موجود أو غير ساري بتاريخ الخدمة',
                    'مرجع مقدم الخدمة غير وحيد')
    service = _one([r for r in index.at('providers.services', day)
                    if _text(source['service_code']) in (_text(r['values'].get('service_code')), _text(r.get('title')))],
                   'provider_service', reasons, 'الخدمة غير موجودة أو غير سارية بتاريخ الخدمة',
                   'مرجع الخدمة غير وحيد')
    if provider:
        computed['provider_code'] = provider['values'].get('provider_code')
    else:
        _add(reasons, 'tariff', 'تعذر التحقق من التسعيرة لعدم تحديد المزود')
        _add(reasons, 'contract', 'تعذر التحقق من العقد لعدم تحديد المزود')
    if service:
        computed['service_code'] = service['values'].get('service_code')
        service_type = service.get('terms', {}).get('service_type')
        if service_type:
            computed['service_type'] = service_type
        else:
            _add(reasons, 'provider_service', 'نوع الخدمة غير محدد في مرجع الخدمة')
    else:
        _add(reasons, 'tariff', 'تعذر التحقق من التسعيرة لعدم تحديد الخدمة')

    price = None
    if provider and service:
        price = _one([r for r in index.at('providers.prices', day)
                      if r.get('links', {}).get('provider') == provider['id']
                      and r.get('links', {}).get('service') == service['id']
                      and r['values'].get('provider_code') == provider['values'].get('provider_code')
                      and r['values'].get('service_code') == service['values'].get('service_code')],
                     'tariff', reasons, 'لا توجد تسعيرة سارية للمزود والخدمة بتاريخ الخدمة',
                     'مرجع التسعيرة غير وحيد')
    elif not provider or not service:
        _add(reasons, 'tariff', 'لا توجد تسعيرة قابلة للتحقق لعدم اكتمال مرجع المزود والخدمة')
    if price:
        computed['price_id'] = price['id']
        computed['tariff_unit'] = price['values'].get('amount')

    contract = None
    if provider:
        candidates = [r for r in index.at('inputs.contracts', day)
                      if r.get('links', {}).get('provider') == provider['id']
                      and r['values'].get('provider_code') == provider['values'].get('provider_code')]
        if price:
            candidates = [r for r in candidates if r['id'] == price.get('links', {}).get('contract')]
        contract = _one(candidates, 'contract', reasons, 'لا يوجد عقد ساري مرتبط بالمزود والخدمة بتاريخ الخدمة',
                        'مرجع العقد غير وحيد')
    if contract:
        computed['contract_id'] = contract['id']
        terms = contract.get('terms', {})
        if terms.get('currency') and claim.currency and terms.get('currency') != claim.currency:
            _add(reasons, 'contract', 'عملة العقد لا تطابق عملة المطالبة: '
                 f"المتوقع {claim.currency}، الوارد {terms.get('currency')}")
        if terms.get('precision') not in (0, 2, 4) or terms.get('rounding') not in ('HALF_UP', 'HALF_EVEN'):
            _add(reasons, 'contract', 'شروط التقريب أو الخانات العشرية في العقد غير مكتملة')
        if not terms.get('network'):
            _add(reasons, 'contract', 'الشبكة غير محددة في العقد')
        computed['rounding'] = terms.get('rounding')
        computed['precision'] = terms.get('precision')
    elif provider:
        _add(reasons, 'other_references', 'تعذر التحقق من اللائحة لعدم تحديد العقد')
        _add(reasons, 'cost_sharing', 'تعذر التحقق من التحمل لعدم تحديد العقد')

    regulation = None
    if contract:
        regulation = _one([r for r in index.at('inputs.regulations', day)
                           if r.get('links', {}).get('contract') == contract['id']],
                          'other_references', reasons, 'لا توجد لائحة سارية مرتبطة بالعقد',
                          'مرجع اللائحة غير وحيد')
    if regulation:
        computed['regulation_id'] = regulation['id']
        if regulation.get('terms', {}).get('amount_basis') != 'NET':
            _add(reasons, 'other_references', 'اللائحة لا تعتمد أساس المبلغ الصافي (NET)')
        if regulation.get('terms', {}).get('unit_basis') != 'ONE_SERVICE_PER_ROW':
            _add(reasons, 'other_references', 'اللائحة لا تعتمد بند خدمة واحداً لكل صف')

    rate = None
    if category and contract and service:
        service_type = service.get('terms', {}).get('service_type')
        network = contract.get('terms', {}).get('network')
        rate = _one([r for r in index.at('beneficiaries.cost_sharing', day)
                     if r.get('links', {}).get('category') == category['id']
                     and r.get('links', {}).get('contract') == contract['id']
                     and r['values'].get('category_code') == category['values'].get('category_code')
                     and r.get('terms', {}).get('service_type') == service_type
                     and r.get('terms', {}).get('network') == network],
                    'cost_sharing', reasons, 'لا توجد قاعدة تحمل مطابقة للفئة والخدمة والشبكة',
                    'مرجع قاعدة التحمل غير وحيد')
    elif not category or not contract or not service:
        _add(reasons, 'cost_sharing', 'تعذر التحقق من التحمل لعدم اكتمال مرجع الفئة أو العقد أو الخدمة')
    if rate:
        computed['copay_rule_id'] = rate['id']

    policy = None
    terms = contract.get('terms', {}) if contract else {}
    if terms.get('precision') in (0, 2, 4) and terms.get('rounding') in ('HALF_UP', 'HALF_EVEN'):
        policy = SimpleNamespace(precision=terms['precision'], rounding=terms['rounding'])

    gross_m = unit_m = copay_m = net_m = None
    if policy is None:
        _add(reasons, 'calculation', 'تعذر التحقق الحسابي لعدم اكتمال شروط العقد (التقريب/الخانات)')
    else:
        gross_m = money(gross, policy) if gross is not None else None
        unit_m = money(unit, policy) if unit is not None else None
        copay_m = money(copay_amount, policy) if copay_amount is not None else None
        net_m = money(net, policy) if net is not None and net >= 0 else None
        if net is not None and net < 0:
            _add(reasons, 'calculation', 'الصافي سالب')
        if unit_m is not None and gross_m is not None and unit_m != gross_m:
            _add(reasons, 'calculation', 'المطلوب لا يساوي السعر × الكمية (الكمية = 1): '
                 f'المتوقع {gross_m}، الوارد {unit_m}')
        if price:
            tariff = money(Decimal(price['values']['amount']), policy)
            computed['tariff_total'] = str(tariff)
            if price['values'].get('currency') and claim.currency and price['values']['currency'] != claim.currency:
                _add(reasons, 'tariff', 'عملة التسعيرة لا تطابق عملة المطالبة: '
                     f"المتوقع {claim.currency}، الوارد {price['values']['currency']}")
            if unit_m is not None and unit_m != tariff:
                _add(reasons, 'tariff', 'السعر لا يطابق التسعيرة المرجعية: '
                     f'المتوقع {tariff}، الوارد {unit_m}')
            if gross_m is not None and gross_m != tariff:
                _add(reasons, 'tariff', 'المطلوب لا يطابق التسعيرة المرجعية: '
                     f'المتوقع {tariff}، الوارد {gross_m}')
        if copay_percent is not None and not Decimal(0) <= copay_percent <= Decimal(100):
            _add(reasons, 'calculation', 'نسبة التحمل خارج النطاق 0–100')
        if gross_m is not None and copay_m is not None and net_m is not None and gross_m - copay_m != net_m:
            _add(reasons, 'calculation', 'تعارض حسابي: المطلوب − إجمالي التحمل لا يساوي الصافي')
        expected_copay = None
        expected_net = None
        if gross_m is not None and copay_percent is not None and Decimal(0) <= copay_percent <= Decimal(100):
            expected_copay = money(gross_m * copay_percent / Decimal(100), policy)
            expected_net = money(gross_m - expected_copay, policy)
            computed['expected_copay'] = str(expected_copay)
            computed['expected_net'] = str(expected_net)
            if copay_m is not None and copay_m != expected_copay:
                _add(reasons, 'calculation', 'إجمالي التحمل لا يساوي المطلوب × نسبة التحمل: '
                     f'المتوقع {expected_copay}، الوارد {copay_m}')
            if net_m is not None and net_m != expected_net:
                _add(reasons, 'calculation', 'الصافي لا يساوي المطلوب − إجمالي التحمل: '
                     f'المتوقع {expected_net}، الوارد {net_m}')
            if copay_m is None:
                computed['copay_amount'] = str(expected_copay)
                _note(notes, 'تم احتساب إجمالي التحمل من المطلوب والنسبة.')
            if net_m is None:
                computed['net_amount'] = str(expected_net)
                _note(notes, 'تم احتساب الصافي من المطلوب وإجمالي التحمل.')
        elif gross_m is not None and rate:
            reference_percent = Decimal(rate['values']['percent'])
            if not Decimal(0) <= reference_percent <= Decimal(100):
                _add(reasons, 'cost_sharing', 'نسبة التحمل المرجعية خارج النطاق 0–100')
            else:
                copay_percent = reference_percent
                expected_copay = money(gross_m * reference_percent / Decimal(100), policy)
                expected_net = money(gross_m - expected_copay, policy)
                computed['copay_percent'] = format(reference_percent, 'f')
                computed['expected_copay'] = str(expected_copay)
                computed['expected_net'] = str(expected_net)
                _note(notes, 'تم احتساب نسبة التحمل من المرجع.')
                if copay_m is not None and copay_m != expected_copay:
                    _add(reasons, 'calculation', 'إجمالي التحمل لا يطابق النسبة المرجعية: '
                         f'المتوقع {expected_copay}، الوارد {copay_m}')
                if net_m is not None and net_m != expected_net:
                    _add(reasons, 'calculation', 'الصافي لا يطابق النسبة المرجعية: '
                         f'المتوقع {expected_net}، الوارد {net_m}')
                if copay_m is None:
                    computed['copay_amount'] = str(expected_copay)
                    _note(notes, 'تم احتساب إجمالي التحمل من المرجع.')
                if net_m is None:
                    computed['net_amount'] = str(expected_net)
                    _note(notes, 'تم احتساب الصافي من المرجع.')
        if gross_m is not None and expected_net is not None and expected_net < 0:
            _add(reasons, 'calculation', 'الصافي المحتسب سالب')
        if rate and copay_percent is not None:
            reference_percent = Decimal(rate['values']['percent'])
            if copay_percent != reference_percent:
                _add(reasons, 'cost_sharing', 'نسبة التحمل لا تطابق قاعدة الفئة: '
                     f'المتوقع {reference_percent}%، الوارد {copay_percent}%')
            computed['beneficiary_copay_percent'] = format(reference_percent, 'f')
    if net_m is not None:
        computed.setdefault('net_amount', str(net_m))
    if copay_m is not None:
        computed.setdefault('copay_amount', str(copay_m))

    caps = [r for r in index.at('inputs.caps', day)
            if _scope_matches(r['values'].get('scope'), provider, service, category, source['beneficiary_token'])]
    for cap in caps:
        cap_currency = cap['values'].get('currency')
        if cap_currency and claim.currency and cap_currency != claim.currency:
            _add(reasons, 'other_references', 'عملة السقف المرجعي لا تطابق عملة المطالبة')
            continue
        if gross is not None and Decimal(cap['values']['amount']) < gross:
            _add(reasons, 'other_references', 'تجاوز السقف المرجعي: '
                 f"السقف {cap['values']['amount']}، المطلوب {format(gross, 'f')}")
    exceptions = [r for r in index.at('inputs.exceptions', day)
                  if _scope_matches(r['values'].get('scope'), provider, service, category, source['beneficiary_token'])]
    if exceptions:
        _add(reasons, 'other_references', 'يوجد استثناء مرجعي ينطبق على البند — يلزم مراجعة بشرية')
    coverage = [r for r in index.at('inputs.coverage', day)
                if _scope_matches(r['values'].get('scope'), provider, service, category, source['beneficiary_token'])]
    if coverage:
        _note(notes, 'توجد تغطية مرجعية مطابقة للبند.')

    key = _duplicate_key(source, computed)
    duplicate_of = None
    if key in seen:
        duplicate_of = seen[key]
        _add(reasons, 'duplicate', f'مكرر — تكرار للبند {duplicate_of}')
    else:
        seen[key] = _row_label(claim, source)

    active = {category_key: values for category_key, values in reasons.items() if values}
    status = REJECTED if active else ACCEPTED
    return {
        'line_id': claim.id,
        'source_locator': claim.source_locator,
        'source_record_id': claim.extracted_record_id,
        'values': original,
        'status': status,
        'status_ar': STATUS_LABELS[status],
        'reasons': active,
        'notes': notes,
        'computed': computed,
        'duplicate_of': duplicate_of,
        'currency': claim.currency,
    }


def summarize(rows):
    counts = Counter(row['status'] for row in rows)
    by_reason = Counter()
    duplicates = 0
    for row in rows:
        if row.get('reasons', {}).get('duplicate'):
            duplicates += 1
        for category in row.get('reasons', {}):
            by_reason[category] += 1
    return {
        'total_lines': len(rows),
        'accepted': counts[ACCEPTED],
        'rejected': counts[REJECTED],
        'duplicates': duplicates,
        'green_lines': counts[ACCEPTED],
        'red_lines': counts[REJECTED],
        'by_reason_category': dict(by_reason),
        'financially_final': False,
    }


def validate_dataset(dataset):
    if dataset.get('schema') != SCHEMA:
        raise ValueError('Unsupported phase 1 result schema')
    rows = dataset.get('rows')
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError('Invalid phase 1 result rows')
    ids = [row.get('line_id') for row in rows]
    if len(ids) != len(set(ids)) or any(not isinstance(row.get('reasons'), dict) for row in rows):
        raise ValueError('Duplicate or malformed phase 1 result line')
    if any(row.get('status') not in (ACCEPTED, REJECTED) for row in rows):
        raise ValueError('Unknown phase 1 result status')
    expected = summarize(rows)
    if dataset.get('summary') != expected:
        raise ValueError('Phase 1 result summary mismatch')
    return expected


def _events(db, org):
    return select(AuditLog).where(AuditLog.organization_id == org,
        AuditLog.action == 'phase1_rules.completed', AuditLog.resource_type == 'phase1_result')


def public(event):
    return {
        'id': event.resource_id,
        'batch_id': event.details['batch_id'],
        'fingerprint': event.details['fingerprint'],
        'summary': event.details['summary'],
        'created_at': event.created_at,
        'stage': STAGE,
        'financially_final': False,
    }


def freeze(db, actor, batch_id):
    with _atomic(db):
        lock_record(db, 'phase1_rules', actor.organization_id, batch_id)
        batch = db.get(ClaimBatch, batch_id)
        if not batch or batch.organization_id != actor.organization_id:
            raise LookupError('Batch not found')
        if batch.status != 'imported' or batch.record_count != batch.imported_count:
            raise ValueError('Complete the batch import before the phase 1 rules audit')
        claims = list(db.scalars(select(ClaimEntry).where(
            ClaimEntry.batch_id == batch.id, ClaimEntry.organization_id == actor.organization_id
        ).order_by(ClaimEntry.id)))
        if len(claims) != batch.record_count or len(claims) > 10000:
            raise ValueError('Batch count mismatch or phase 1 limit exceeded')
        mapping = confirmed_mapping(db, batch)
        if not mapping or not isinstance(mapping.get('columns'), dict):
            raise ValueError('Confirm the fixed claim template mapping before the phase 1 audit')
        index = ReferenceIndex(db, actor)
        seen = {}
        rows = [evaluate_line(index, claim, mapping, seen) for claim in claims]
        summary = summarize(rows)
        dataset = {
            'schema': SCHEMA,
            'organization_id': actor.organization_id,
            'batch_id': batch.id,
            'source_sha256': batch.source_sha256,
            'rows': rows,
            'summary': summary,
            'stage': STAGE,
            'financially_final': False,
        }
        fingerprint = sha256(canonical(dataset)).hexdigest()
        existing = db.scalar(_events(db, actor.organization_id).where(
            AuditLog.details['fingerprint'].as_string() == fingerprint).limit(1))
        if existing:
            return public(existing)
        object_id, digest = registry.store().put(canonical(dataset))
        source = SourceFile(sha256=digest, storage_key=object_id,
                            original_filename='phase1-rules-results.json')
        db.add(source)
        db.flush()
        event = AuditLog(actor_id=actor.id, organization_id=actor.organization_id,
            action='phase1_rules.completed', resource_type='phase1_result',
            resource_id=str(uuid4()), category=STAGE,
            details={'source_id': source.id, 'fingerprint': fingerprint,
                     'batch_id': batch.id, 'summary': summary})
        db.add(event)
        db.flush()
        return public(event)


def list_results(db, actor, offset=0, limit=25):
    events = list(db.scalars(_events(db, actor.organization_id).order_by(
        AuditLog.created_at.desc(), AuditLog.id).offset(offset).limit(limit + 1)))
    return {'items': [public(event) for event in events[:limit]],
            'has_more': len(events) > limit, 'offset': offset, 'limit': limit}


def read(db, actor, result_id):
    event = db.scalar(_events(db, actor.organization_id).where(AuditLog.resource_id == result_id))
    if not event:
        raise LookupError('Phase 1 result not found')
    source = db.get(SourceFile, event.details['source_id'])
    if not source:
        raise ValueError('Phase 1 result source missing')
    raw = registry.store().get(source.storage_key, source.sha256)
    dataset = json.loads(raw)
    if (dataset.get('organization_id') != actor.organization_id
            or dataset.get('batch_id') != event.details['batch_id']
            or sha256(canonical(dataset)).hexdigest() != event.details['fingerprint']):
        raise ValueError('Phase 1 result integrity failure')
    summary = validate_dataset(dataset)
    if summary != event.details['summary']:
        raise ValueError('Phase 1 result summary does not match the audit event')
    return {**dataset, 'id': event.resource_id, 'fingerprint': event.details['fingerprint'],
            'created_at': event.created_at}
