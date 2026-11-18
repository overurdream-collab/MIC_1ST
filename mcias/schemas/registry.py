"""Bounded reference-intake contracts, not an automatic financial policy."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID
from pydantic import Field, model_validator
from mcias.schemas.common import Input, Reason
from mcias.schemas.reference_links import ReferenceTerms

# key -> Arabic label, English label, browser input type
FIELDS = {
    "record_code": ("الرمز الداخلي", "Internal code", "text"),
    "provider_code": ("مقدم الخدمة", "Provider", "text"),
    "contract_number": ("رقم العقد", "Contract number", "text"),
    "insurer_code": ("شركة التأمين", "Insurance company", "text"),
    "beneficiary_token": ("الرقم التأميني", "Insurance number", "text"),
    "category_code": ("الدرجة / الفئة", "Category", "text"),
    "service_code": ("الخدمة", "Service", "text"),
    "coding_system": ("نظام الترميز", "Coding system", "text"),
    "currency": ("العملة", "Currency", "text"),
    "amount": ("المبلغ المرجعي", "Reference amount", "text"),
    "percent": ("النسبة المرجعية", "Reference percentage", "text"),
    "scope": ("النطاق", "Scope", "text"),
    "document_number": ("رقم المستند", "Document number", "text"),
}
SCHEMAS = {
    "inputs.contracts": ("record_code", "provider_code", "contract_number"),
    "inputs.insurers": ("record_code", "insurer_code", "contract_number"),
    "inputs.regulations": ("record_code", "document_number"),
    "inputs.exceptions": ("record_code", "scope"),
    "inputs.approvals": ("record_code", "document_number"),
    "inputs.caps": ("record_code", "amount", "currency", "scope"),
    "inputs.coverage": ("record_code", "scope"),
    "beneficiaries.names": ("record_code", "beneficiary_token", "category_code"),
    "beneficiaries.categories": ("record_code", "category_code"),
    "beneficiaries.cost_sharing": ("record_code", "category_code", "percent"),
    **{f"providers.{kind}": ("record_code", "provider_code") for kind in ("hospitals", "clinics", "labs", "radiology", "pharmacies")},
    "providers.services": ("record_code", "service_code", "coding_system"),
    "providers.prices": ("record_code", "provider_code", "service_code", "amount", "currency"),
}
FIELD_LIMITS = {"record_code":80,"provider_code":80,"contract_number":120,"beneficiary_token":128,
    "insurer_code":80,
    "category_code":80,"service_code":100,"currency":3,"document_number":120}
IMPORT_SYSTEM_FIELDS = {
    "title": ("الاسم / العنوان", "Name / title"),
    "effective_from": ("تاريخ بداية السريان", "Effective from"),
    "effective_to": ("تاريخ نهاية السريان", "Effective until"),
}
IMPORT_EXAMPLES = {
    "record_code":"SYN-001","provider_code":"HOSP-01","contract_number":"C-2026-001",
    "insurer_code":"INS-01","beneficiary_token":"123456789","category_code":"A",
    "service_code":"CONSULT","coding_system":"LOCAL","currency":"LYD","amount":"1000.00",
    "percent":"20","scope":"عام","document_number":"DOC-2026-001",
}

def import_labels(key):
    labels = IMPORT_SYSTEM_FIELDS.get(key) or FIELDS[key][:2]
    return key, labels[0], labels[1]

def import_value(values, key):
    for candidate in import_labels(key):
        if candidate in values:
            raw = values[candidate]
            return "" if raw is None else str(raw).strip()
    return ""

def import_date(value, label):
    raw = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"«{label}» غير صالح. استخدم YYYY-MM-DD أو DD/MM/YYYY، مثال: 2026-11-13.")

class RecordCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    effective_from: date
    effective_to: date | None = None
    values: dict[str, str] = Field(max_length=12)
    evidence_document_id: UUID | None = None
    parent_id: UUID | None = None
    clause_number: str | None = Field(default=None, pattern=r"^[1-9][0-9]*(\.[1-9][0-9]*)*$", max_length=60)
    body: str = Field(default="", max_length=20000)
    links: dict[str, UUID] = Field(default_factory=dict, max_length=3)
    terms: ReferenceTerms = Field(default_factory=ReferenceTerms)

    @model_validator(mode="after")
    def dates(self):
        if not self.title.strip(): raise ValueError("title required")
        if bool(self.parent_id) != bool(self.clause_number):
            raise ValueError("parent_id and clause_number must be supplied together")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self

class RecordVersion(RecordCreate):
    expected_version: int = Field(ge=1)

class RecordReplace(RecordVersion):
    reason: Reason

class RecordClose(Input):
    reason: Reason
    expected_version: int = Field(ge=1)

class RecordApprove(Input):
    expected_version: int = Field(ge=1)

class ImportCommit(Input):
    record_ids: list[UUID] = Field(default_factory=list, max_length=100)
    manual_record: RecordCreate | None = None

def validate_values(feature, values):
    if feature not in SCHEMAS: raise ValueError("القسم المرجعي غير مدعوم.")
    expected = set(SCHEMAS[feature])
    missing = expected - set(values)
    extra = set(values) - expected
    if missing:
        raise ValueError("الأعمدة المطلوبة التالية ناقصة: " + "، ".join(f"«{FIELDS[key][0]}»" for key in sorted(missing)) + ".")
    if extra:
        raise ValueError("أعمدة غير معروفة في هذا القسم: " + "، ".join(f"«{FIELDS.get(key,(key,))[0]}»" for key in sorted(extra)) + ".")
    result = {}
    for key, raw in values.items():
        label = FIELDS[key][0]
        limit = FIELD_LIMITS.get(key, 200)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"الحقل «{label}» مطلوب.")
        if len(raw) > limit:
            raise ValueError(f"الحقل «{label}» يتجاوز الحد ({limit} حرفاً).")
        if any(ord(c) < 32 for c in raw):
            raise ValueError(f"الحقل «{label}» يحتوي على محارف تحكم غير مسموحة.")
        value = raw.strip()
        if key in ("amount", "percent"):
            try:
                number = Decimal(value)
            except InvalidOperation:
                raise ValueError(f"القيمة في «{label}» يجب أن تكون رقماً صالحاً، مثال: 1000.00.") from None
            if not number.is_finite() or number < 0 or number >= Decimal("100000000000000") or number.as_tuple().exponent < -4:
                raise ValueError(f"القيمة في «{label}» خارج النطاق أو تتجاوز 4 منازل عشرية.")
            if key == "percent" and number > 100:
                raise ValueError(f"النسبة في «{label}» يجب أن تكون بين 0 و100.")
            value = format(number, "f")
        if key == "currency" and (len(value) != 3 or not value.isascii() or not value.isalpha() or not value.isupper()):
            raise ValueError("العملة يجب أن تكون ثلاثة أحرف إنجليزية كبيرة، مثال: LYD.")
        if key == "coding_system" and value not in ("LOCAL", "ICD", "CPT", "LOINC", "SNOMED"):
            raise ValueError("نظام الترميز يجب أن يكون أحد القيم: LOCAL أو ICD أو CPT أو LOINC أو SNOMED.")
        result[key] = value
    return result
