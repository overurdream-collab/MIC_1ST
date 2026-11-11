"""G4 strict source-record normalization. No guessed clinical or monetary values."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

FIELDS=("claim_reference","beneficiary_token","provider_code","service_code",
        "service_date","claimed_amount","currency")

# The laptop edition deliberately accepts one published workbook layout. This
# keeps imports reproducible and removes per-batch column mapping from the UI.
FIXED_COLUMNS={
    "claim_reference":"رقم الفاتورة",
    "beneficiary_token":"الرقم التأميني",
    "beneficiary_name":"اسم المستفيد",
    "category_code":"الدرجة",
    "provider_code":"مقدم الخدمة",
    "service_code":"الخدمة",
    "service_date":"تاريخ الخدمة",
    "unit_price":"السعر",
    "gross_amount":"المطلوب",
    "copay_percent":"نسبة التحمل",
    "copay_amount":"إجمالي التحمل",
    "claimed_amount":"الصافي",
}
FIXED_HEADERS=tuple(FIXED_COLUMNS.values())
REQUIRED_HEADERS=tuple(FIXED_COLUMNS[key] for key in (
    "claim_reference","beneficiary_token","beneficiary_name","category_code",
    "provider_code","service_code","service_date"))

class ClaimValidationError(ValueError):
    pass

def _present(values, field):
    header=FIXED_COLUMNS[field]
    return header in values and values[header] is not None and str(values[header]).strip()!=""

def validate_fixed_record(values: dict) -> None:
    """Validate required template cells before any claim row is imported."""
    if not isinstance(values,dict):
        raise ClaimValidationError("record is not a mapping")
    missing=[header for header in REQUIRED_HEADERS
             if header not in values or values[header] is None or not str(values[header]).strip()]
    if missing:
        raise ClaimValidationError("missing required cells: "+", ".join(missing))
    raw_date=str(values[FIXED_COLUMNS["service_date"]]).strip()
    try:
        datetime.strptime(raw_date,"%d/%m/%Y")
    except ValueError:
        raise ClaimValidationError("service date must use DD/MM/YYYY") from None
    for field in ("unit_price","gross_amount","copay_amount","claimed_amount"):
        if not _present(values,field):
            continue
        try:
            source_decimal(values[FIXED_COLUMNS[field]])
        except ClaimValidationError as exc:
            raise ClaimValidationError(f"invalid {FIXED_COLUMNS[field]}: {exc}") from None
    if _present(values,"copay_percent"):
        try:
            percent=source_decimal(values[FIXED_COLUMNS["copay_percent"]],allow_percent=True)
        except ClaimValidationError as exc:
            raise ClaimValidationError(f"invalid {FIXED_COLUMNS['copay_percent']}: {exc}") from None
        if percent>100:
            raise ClaimValidationError("copay percentage must be between 0 and 100")
    if not _present(values,"claimed_amount") and not (_present(values,"gross_amount") or _present(values,"unit_price")):
        raise ClaimValidationError("missing required cells: الصافي أو السعر أو المطلوب")

def source_decimal(value, *, allow_percent=False):
    """Accept explicit decimal amounts and unambiguous three-digit grouping."""
    raw=str(value).strip()
    if allow_percent:raw=raw.removesuffix('%').strip()
    if ',' in raw and not re.fullmatch(r'[0-9]{1,3}(,[0-9]{3})+(\.[0-9]{1,4})?',raw):
        raise ClaimValidationError('ambiguous numeric grouping')
    try:number=Decimal(raw.replace(',',''))
    except InvalidOperation:raise ClaimValidationError('invalid source number') from None
    if not number.is_finite() or number<0 or number>=Decimal('100000000000000') or number.as_tuple().exponent < -4:
        raise ClaimValidationError('source number out of range or precision')
    return number

def _mapped_values(values,mapping):
    try:
        data={}
        for field,source in mapping['columns'].items():
            raw=values.get(source)
            data[field]="" if raw is None else str(raw).strip()
        data['currency']=mapping['currency']
        raw=data['service_date']
        if mapping['date_format']=='DMY':
            data['service_date']=datetime.strptime(raw,'%d/%m/%Y').date().isoformat()
        elif mapping['date_format']=='YMD_SLASH':
            data['service_date']=datetime.strptime(raw,'%Y/%m/%d').date().isoformat()
        else:
            data['service_date']=datetime.fromisoformat(raw).date().isoformat()
        unit=source_decimal(data['unit_price']) if data.get('unit_price') else None
        gross=source_decimal(data['gross_amount']) if data.get('gross_amount') else None
        if gross is None and unit is not None:
            gross=unit
        if unit is None and gross is not None:
            unit=gross
        percent=source_decimal(data['copay_percent'],allow_percent=True) if data.get('copay_percent') else None
        copay=source_decimal(data['copay_amount']) if data.get('copay_amount') else None
        if copay is None and gross is not None and percent is not None:
            copay=gross*percent/Decimal(100)
        if data.get('claimed_amount'):
            net=source_decimal(data['claimed_amount'])
        else:
            net=None
        data['claimed_amount']=None if net is None else format(net.quantize(Decimal('0.0001'),rounding=ROUND_HALF_UP),'f')
        return data
    except ClaimValidationError:
        raise
    except (KeyError,TypeError,ValueError):
        raise ClaimValidationError('source values do not match the confirmed mapping') from None

def normalize(values: dict, mapping=None) -> dict:
    if not isinstance(values,dict):
        raise ClaimValidationError("record is not a mapping")
    if mapping:
        values=_mapped_values(values,mapping)
    required=FIELDS if not mapping else ("claim_reference","beneficiary_token","provider_code",
        "service_code","service_date","currency")
    missing=[key for key in required if key not in values or not isinstance(values[key],str) or not values[key].strip()]
    if missing:
        raise ClaimValidationError("missing required fields: "+",".join(missing))
    data={key:values[key].strip() for key in ("claim_reference","beneficiary_token",
        "provider_code","service_code","service_date","currency")}
    claimed=values.get("claimed_amount")
    data["claimed_amount"]=None if claimed is None or not str(claimed).strip() else str(claimed).strip()
    for key,limit in (("claim_reference",120),("beneficiary_token",128),("provider_code",80),("service_code",100)):
        if len(data[key])>limit or any(ord(c)<32 for c in data[key]):
            raise ClaimValidationError("invalid "+key)
    if not re.fullmatch(r"[A-Z]{3}",data["currency"]):
        raise ClaimValidationError("invalid currency")
    try:
        day=date.fromisoformat(data["service_date"])
    except ValueError as exc:
        raise ClaimValidationError("service_date must be ISO YYYY-MM-DD") from exc
    if day.isoformat()!=data["service_date"]:
        raise ClaimValidationError("noncanonical service date")
    if data["claimed_amount"] is None:
        amount=None
    else:
        try:
            amount=Decimal(data["claimed_amount"])
        except InvalidOperation as exc:
            raise ClaimValidationError("invalid claimed amount") from exc
        if not amount.is_finite() or amount<0 or amount>=Decimal("100000000000000") or amount.as_tuple().exponent < -4:
            raise ClaimValidationError("claimed amount out of range or precision")
    data["service_date"]=day
    data["claimed_amount"]=amount
    return data
