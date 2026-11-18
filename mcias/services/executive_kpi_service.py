"""Read-only executive KPI aggregation over frozen preliminary result sets.

The dashboard never re-audits, approves, or mutates claims. It projects the
latest frozen result set per batch so historical batches remain reproducible,
and keeps every monetary total separated by currency.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from mcias.models import AuditLog
from mcias.models_audit import AuditSnapshot
from mcias.models_claims import ClaimBatch
from mcias.services import result_service

BUCKETS = ("accepted", "rejected", "held", "review")
MAX_RESULT_SETS = 100
MAX_LINES = 200_000
OUTCOME_ALIASES = {
    "matched": "accepted",
    "unmatched": "rejected",
    "unverified": "unverified",
    "flagged": "flagged",
}
CATEGORY_LABELS = {
    "price": ("فرق السعر", "Price variance"),
    "duplicate": ("تكرار", "Duplicate"),
    "missing_data": ("نقص بيانات", "Missing data"),
    "eligibility": ("أهلية", "Eligibility"),
    "policy": ("سياسة أو تعرفة", "Policy or tariff"),
    "verification": ("تحقق", "Verification"),
    "evidence": ("دليل أو مرفق", "Evidence or attachment"),
    "other": ("ملاحظات أخرى", "Other notes"),
}
_MISSING_TOKENS = (
    "missing", "not_audited", "not provided", "unavailable", "absent",
    "نقص", "مفقود", "غير متوفر", "غير مرفق", "بدون فاتورة", "لا توجد",
)
_PRICE_TOKENS = ("price", "tariff", "سعر", "تعرفة")
_DUPLICATE_TOKENS = ("duplicate", "duplicated", "مكرر", "تكرار")
_VERIFICATION_TOKENS = ("verif", "تحقق", "مطابق")
_EVIDENCE_TOKENS = ("evidence", "invoice", "attachment", "دليل", "فاتورة", "مرفق")
_ELIGIBILITY_TOKENS = ("eligib", "أهلية", "استحقاق")
_POLICY_TOKENS = (
    "policy", "tariff", "cap", "copay", "exclusion", "contract", "net_basis",
    "second_copay", "سياسة", "تعرفة", "سقف", "تحمل", "استثناء", "عقد",
)
_AGENT_NAMES = (
    "eligibility", "tariff", "contract", "exclusion", "caps",
    "cost_sharing", "duplicates", "evidence",
)


def _dec(value: Any) -> Decimal:
    if value is None or value == "":
        raise ValueError("invalid KPI monetary value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid KPI monetary value") from exc
    if not result.is_finite():
        raise ValueError("non-finite KPI monetary value")
    return result


def _money(value: Decimal) -> str:
    return format(value, "f")


def _amounts_out(amounts: dict[str, dict[str, Decimal]]) -> dict:
    return {
        currency: {key: _money(value) for key, value in values.items()}
        for currency, values in sorted(amounts.items())
    }


def _blank_amount() -> dict:
    return {
        "claimed": Decimal(0),
        "accepted": Decimal(0),
        "flagged": Decimal(0),
        "proposed_difference": Decimal(0),
        "price_variance": Decimal(0),
        "duplicate_exposure": Decimal(0),
    }


def _empty_amounts() -> dict[str, dict]:
    return defaultdict(_blank_amount)


def _contains(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _reason_items(value: Any) -> list[dict]:
    output = []
    if not isinstance(value, list):
        return output
    for item in value:
        if isinstance(item, dict):
            code = str(item.get("code") or item.get("reason") or "note")
            source = str(item.get("source") or "")
            message = str(item.get("message") or item.get("reason") or code)
        else:
            code = source = message = str(item)
        output.append({"source": source, "code": code, "message": message})
    return output


def _category(text: str) -> str:
    if _contains(text, _PRICE_TOKENS):
        return "price"
    if _contains(text, _DUPLICATE_TOKENS):
        return "duplicate"
    if _contains(text, _MISSING_TOKENS):
        return "missing_data"
    if _contains(text, _ELIGIBILITY_TOKENS):
        return "eligibility"
    if _contains(text, _VERIFICATION_TOKENS):
        return "verification"
    if _contains(text, _EVIDENCE_TOKENS):
        return "evidence"
    if _contains(text, _POLICY_TOKENS):
        return "policy"
    return "other"


def _price_variance(snapshot: AuditSnapshot | None) -> tuple[Decimal | None, bool]:
    """Return the tariff deduction only where a frozen comparison exists."""
    if snapshot is None or not isinstance(snapshot.calculation, dict):
        return None, False
    calculation = snapshot.calculation
    if calculation.get("status") != "calculated":
        return None, False
    evidence = calculation.get("gross_evidence")
    steps = evidence.get("deductions") if isinstance(evidence, dict) else calculation.get("deductions")
    if not isinstance(steps, list):
        return None, False
    total = Decimal(0)
    for step in steps:
        if not isinstance(step, dict) or step.get("rule") != "tariff":
            continue
        total += _dec(step.get("deduction") or 0)
    return total, True


def _duplicate(snapshot: AuditSnapshot | None, text: str) -> bool:
    if _contains(text, _DUPLICATE_TOKENS):
        return True
    if snapshot is None or not isinstance(snapshot.findings, dict):
        return False
    for finding in snapshot.findings.get("agents", []):
        if not isinstance(finding, dict):
            continue
        agent = str(finding.get("agent") or "").lower()
        status = str(finding.get("status") or "").lower()
        if agent == "duplicates" and status not in ("pass", ""):
            return True
    return False


def _missing(reasons: list[dict]) -> bool:
    return any(_contains(f"{item['code']} {item['message']}".lower(), _MISSING_TOKENS) for item in reasons)


def _event_query(organization_id: str):
    return select(AuditLog).where(
        AuditLog.organization_id == organization_id,
        AuditLog.action == "result_set.created",
        AuditLog.resource_type == "audit_result_set",
    )


def _select_events(db, organization_id: str, filters: dict) -> tuple[list[AuditLog], dict]:
    result_id = filters.get("result_id")
    batch_id = filters.get("batch_id")
    query = _event_query(organization_id)
    if result_id:
        query = query.where(AuditLog.resource_id == str(result_id))
    elif batch_id:
        query = query.where(AuditLog.details["batch_id"].as_string() == str(batch_id))
    events = list(db.scalars(query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(500)))
    if not events:
        return [], {"result_ids": [], "batch_ids": [], "truncated": False, "latest_result_created_at": None}

    if result_id:
        selected = events[:1]
        truncated = False
    else:
        selected, seen, total, truncated = [], set(), 0, False
        for event in events:
            key = str(event.details.get("batch_id") or "")
            if key in seen:
                continue
            lines = int((event.details.get("summary") or {}).get("total_lines") or 0)
            if selected and (len(selected) >= MAX_RESULT_SETS or total + lines > MAX_LINES):
                truncated = True
                continue
            selected.append(event)
            seen.add(key)
            total += lines
    return selected, {
        "result_ids": [str(event.resource_id) for event in selected],
        "batch_ids": [str(event.details.get("batch_id")) for event in selected],
        "truncated": truncated,
        "latest_result_created_at": selected[0].created_at if selected else None,
    }


def _collect(db, actor, filters: dict) -> tuple[list[dict], dict, list[dict], dict, dict]:
    events, scope = _select_events(db, actor.organization_id, filters)
    if not events:
        return [], {}, [], {}, scope

    batch_ids = {event.details.get("batch_id") for event in events}
    batches = {
        str(batch.id): batch
        for batch in db.scalars(select(ClaimBatch).where(
            ClaimBatch.organization_id == actor.organization_id,
            ClaimBatch.id.in_(batch_ids),
        ))
    }
    datasets = []
    for event in events:
        dataset = result_service.read(db, actor, str(event.resource_id))
        datasets.append((event, dataset))

    snapshot_ids = {
        row.get("audit_snapshot_id")
        for _, dataset in datasets
        for row in dataset["rows"]
        if row.get("audit_snapshot_id")
    }
    snapshots = {}
    if snapshot_ids:
        snapshots = {
            str(snapshot.id): snapshot
            for snapshot in db.scalars(select(AuditSnapshot).where(
                AuditSnapshot.organization_id == actor.organization_id,
                AuditSnapshot.id.in_(snapshot_ids),
            ))
        }

    all_records = []
    batch_rows = []
    for event, dataset in datasets:
        batch_id = str(dataset.get("batch_id") or event.details.get("batch_id"))
        batch = batches.get(batch_id)
        result_id = str(event.resource_id)
        for row in dataset["rows"]:
            reasons = _reason_items(row.get("reasons"))
            fallback = str(row.get("reason") or "").strip()
            if fallback and not any(item["code"] == fallback for item in reasons):
                reasons.append({"source": "result", "code": fallback, "message": fallback})
            text = " ".join(f"{item['code']} {item['message']}" for item in reasons).lower()
            snapshot = snapshots.get(str(row.get("audit_snapshot_id") or ""))
            price_variance, price_available = _price_variance(snapshot)
            duplicate = _duplicate(snapshot, text)
            missing = _missing(reasons)
            categories = {_category(text)}
            if duplicate:
                categories.add("duplicate")
            if missing:
                categories.add("missing_data")
            if price_variance is not None and price_variance > 0:
                categories.add("price")
            proposed = row.get("proposed_difference")
            claimed = _dec(row.get("claimed_net"))
            all_records.append({
                "line_id": str(row.get("line_id")),
                "batch_id": batch_id,
                "result_id": result_id,
                "invoice_number": row.get("invoice_number"),
                "provider_code": row.get("provider_code") or "",
                "service_code": row.get("service_code") or "",
                "service_date": row.get("service_date"),
                "currency": str(row.get("currency") or ""),
                "bucket": row.get("bucket"),
                "claimed_net": claimed,
                "proposed_difference": _dec(proposed) if proposed not in (None, "") else None,
                "price_variance": price_variance,
                "price_available": price_available,
                "duplicate": duplicate,
                "missing": missing,
                "reasons": reasons,
                "categories": categories,
                "audit_snapshot_id": str(row.get("audit_snapshot_id") or "") or None,
                "audited_at": snapshot.created_at if snapshot else None,
            })
        summary = dataset.get("summary") or {}
        batch_rows.append({
            "id": batch_id,
                "name": (getattr(batch, "display_name", None) if batch else None) or batch_id,
            "result_id": result_id,
            "created_at": event.created_at,
            "total_lines": int(summary.get("total_lines") or 0),
            "counts": summary.get("counts") or {},
            "record_count": int(batch.record_count or 0) if batch else 0,
            "imported_count": int(batch.imported_count or 0) if batch else 0,
        })
    return all_records, snapshots, batch_rows, batches, scope


def _filtered(records: list[dict], filters: dict) -> list[dict]:
    provider = (filters.get("provider") or "").strip()
    service = (filters.get("service") or "").strip()
    currency = (filters.get("currency") or "").strip()
    outcome = (filters.get("outcome") or "").strip()
    rule = (filters.get("rule") or "").strip()
    search = (filters.get("q") or "").strip().lower()
    basis = filters.get("date_basis") or "service"
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from must not be after date_to")

    result = []
    for record in records:
        if provider and record["provider_code"] != provider:
            continue
        if service and record["service_code"] != service:
            continue
        if currency and record["currency"] != currency:
            continue
        if outcome:
            mapped = OUTCOME_ALIASES.get(outcome, outcome)
            if mapped == "unverified" and record["bucket"] not in ("held", "review"):
                continue
            if mapped == "flagged" and record["bucket"] == "accepted":
                continue
            if mapped not in ("unverified", "flagged") and record["bucket"] != mapped:
                continue
        if rule:
            if rule not in record["categories"] and not any(item["code"] == rule for item in record["reasons"]):
                continue
        if search:
            haystack = " ".join(str(record.get(key) or "") for key in (
                "invoice_number", "provider_code", "service_code", "line_id",
            )).lower()
            if search not in haystack:
                continue
        if basis == "audit":
            if not record.get("audited_at"):
                continue
            value = record["audited_at"].date() if isinstance(record["audited_at"], datetime) else record["audited_at"]
        else:
            value = record.get("service_date")
            if isinstance(value, str):
                try:
                    value = date.fromisoformat(value[:10])
                except ValueError:
                    value = None
        if date_from:
            if value is None or value < date_from:
                continue
        if date_to:
            if value is None or value > date_to:
                continue
        result.append(record)
    return result


def _aggregate(records: list[dict]) -> tuple[dict, dict[str, dict], dict, dict]:
    counts = Counter(record["bucket"] for record in records)
    amounts = _empty_amounts()
    providers = defaultdict(lambda: {"lines": 0, "counts": Counter(), "amounts": _empty_amounts()})
    services = defaultdict(lambda: {"lines": 0, "counts": Counter(), "amounts": _empty_amounts()})
    note_categories = Counter()
    note_codes = Counter()
    agents = set()

    for record in records:
        currency = record["currency"]
        bucket = record["bucket"]
        claimed = record["claimed_net"]
        values = amounts[currency]
        values["claimed"] += claimed
        if bucket == "accepted":
            values["accepted"] += claimed
        else:
            values["flagged"] += claimed
        if bucket == "rejected" and record["proposed_difference"] is not None:
            values["proposed_difference"] += record["proposed_difference"]
        if record["price_available"] and record["price_variance"] is not None:
            values["price_variance"] += record["price_variance"]
        if record["duplicate"]:
            values["duplicate_exposure"] += claimed

        for group, key in ((providers, record["provider_code"]), (services, record["service_code"])):
            item = group[key or "—"]
            item["lines"] += 1
            item["counts"][bucket] += 1
            item_amount = item["amounts"][currency]
            item_amount["claimed"] += claimed
            if bucket != "accepted":
                item_amount["flagged"] += claimed
            if record["price_available"] and record["price_variance"] is not None:
                item_amount["price_variance"] += record["price_variance"]

        for category in record["categories"]:
            note_categories[category] += 1
        for reason in record["reasons"]:
            note_codes[reason["code"]] += 1
        if record["audit_snapshot_id"]:
            snapshot_agents = record.get("agents") or set()
            agents.update(snapshot_agents)

    total = len(records)
    unverified = counts["held"] + counts["review"]
    missing = sum(1 for record in records if record["missing"])
    available = sum(1 for record in records if record["price_available"])
    duplicate = sum(1 for record in records if record["duplicate"])
    summary = {
        "total_lines": total,
        "counts": {bucket: counts[bucket] for bucket in BUCKETS},
        "unverified": unverified,
        "flagged": total - counts["accepted"],
        "missing_data": missing,
        "duplicate_lines": duplicate,
        "price_available_lines": available,
        "price_unavailable_lines": total - available,
        "rates": {
            "matched": _rate(counts["accepted"], total),
            "unmatched": _rate(counts["rejected"], total),
            "unverified": _rate(unverified, total),
            "missing_data": _rate(missing, total),
            "flagged": _rate(total - counts["accepted"], total),
        },
        "by_currency": _amounts_out(amounts),
        "currency_count": len(amounts),
        "single_currency": len(amounts) == 1,
    }
    provider_rows = _group_rows(providers)
    service_rows = _group_rows(services)
    reasons = {
        "categories": [
            {"value": value, "count": count, "labels": CATEGORY_LABELS.get(value, (value, value))}
            for value, count in note_categories.most_common()
        ],
        "codes": [{"value": value, "count": count} for value, count in note_codes.most_common(50)],
    }
    return summary, amounts, {"providers": provider_rows, "services": service_rows}, reasons


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _group_rows(groups: dict) -> list[dict]:
    rows = []
    for key, item in groups.items():
        counts = item["counts"]
        rows.append({
            "value": key,
            "lines": item["lines"],
            "matched": counts["accepted"],
            "unmatched": counts["rejected"],
            "unverified": counts["held"] + counts["review"],
            "amounts": _amounts_out(item["amounts"]),
        })
    return sorted(rows, key=lambda row: (-row["lines"], row["value"]))[:100]


def _rules(db, snapshots: dict, records: list[dict]) -> dict:
    agents = set()
    for snapshot in snapshots.values():
        findings = snapshot.findings if isinstance(snapshot.findings, dict) else {}
        for finding in findings.get("agents", []):
            if isinstance(finding, dict) and finding.get("agent"):
                agents.add(str(finding["agent"]))
    note_codes = {item["code"] for record in records for item in record["reasons"]}
    return {
        "available": bool(agents or note_codes),
        "count": len(agents),
        "agents": sorted(agents),
        "note_codes_count": len(note_codes),
    }


def _quality(records: list[dict], batch_rows: list[dict], snapshots: dict) -> dict:
    input_records = sum(row["record_count"] for row in batch_rows)
    imported = sum(row["imported_count"] for row in batch_rows)
    result_lines = sum(row["total_lines"] for row in batch_rows)
    total = len(records)
    missing = sum(1 for record in records if record["missing"])
    unverified = sum(1 for record in records if record["bucket"] in ("held", "review"))
    audited = sum(1 for record in records if record["audit_snapshot_id"])
    return {
        "processing_completion": {
            "numerator": imported,
            "denominator": input_records,
            "rate": _rate(imported, input_records),
        },
        "reconciliation": {
            "input_records": input_records,
            "imported_records": imported,
            "result_lines": result_lines,
            "difference": imported - result_lines,
            "status": "unavailable" if not batch_rows else ("reconciled" if imported == result_lines else "mismatch"),
        },
        "missing_data_rate": {
            "numerator": missing,
            "denominator": total,
            "rate": _rate(missing, total),
        },
        "unverified_rate": {
            "numerator": unverified,
            "denominator": total,
            "rate": _rate(unverified, total),
        },
        "audited_lines": audited,
        "rules_run": _rules(None, snapshots, records),
        "audit_duration": {"available": False, "value_ms": None},
    }


def _options(all_records: list[dict], batch_rows: list[dict], summary: dict, reasons: dict) -> dict:
    providers = Counter(record["provider_code"] for record in all_records if record["provider_code"])
    services = Counter(record["service_code"] for record in all_records if record["service_code"])
    currencies = sorted({record["currency"] for record in all_records if record["currency"]})
    return {
        "batches": [
            {"value": row["id"], "label": row["name"], "lines": row["total_lines"]}
            for row in batch_rows
        ],
        "providers": [
            {"value": value, "label": value, "lines": count}
            for value, count in providers.most_common(500)
        ],
        "services": [
            {"value": value, "label": value, "lines": count}
            for value, count in services.most_common(500)
        ],
        "currencies": currencies,
        "outcomes": ["accepted", "rejected", "held", "review", "unverified", "flagged"],
        "rules": reasons["categories"],
    }


def dashboard(db, actor, filters: dict) -> dict:
    all_records, snapshots, batch_rows, _, scope = _collect(db, actor, filters)
    filtered = _filtered(all_records, filters)
    summary, amounts, groups, reasons = _aggregate(filtered)
    quality = _quality(filtered, batch_rows, snapshots)
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "scope": {
            **scope,
            "batch_count": len(batch_rows),
            "total_result_lines": sum(row["total_lines"] for row in batch_rows),
            "truncated": scope.get("truncated", False),
        },
        "filters": filters,
        "options": _options(all_records, batch_rows, summary, reasons),
        "summary": summary,
        "quality": quality,
        "providers": groups["providers"],
        "services": groups["services"],
        "reasons": reasons,
        "batches": batch_rows,
        "by_currency": _amounts_out(amounts),
        "definitions": {
            "matched": "accepted bucket",
            "unmatched": "rejected bucket",
            "unverified": "held + review buckets",
            "flagged": "unique non-accepted lines, not a confirmed loss",
            "price_variance": "sum of positive tariff deductions where frozen comparison exists",
            "duplicate_exposure": "unique duplicate lines, potential exposure only",
            "proposed_difference": "preliminary rejected-line difference, not final approval",
            "reconciliation": "imported records versus frozen result lines",
        },
    }


def lines(db, actor, filters: dict, offset: int = 0, limit: int = 50) -> dict:
    all_records, _, _, _, scope = _collect(db, actor, filters)
    filtered = _filtered(all_records, filters)
    items = []
    for record in filtered[offset:offset + limit]:
        items.append({
            "line_id": record["line_id"],
            "batch_id": record["batch_id"],
            "result_id": record["result_id"],
            "invoice_number": record["invoice_number"],
            "provider_code": record["provider_code"],
            "service_code": record["service_code"],
            "service_date": record["service_date"],
            "currency": record["currency"],
            "bucket": record["bucket"],
            "claimed_net": _money(record["claimed_net"]),
            "proposed_difference": _money(record["proposed_difference"]) if record["proposed_difference"] is not None else None,
            "price_variance": _money(record["price_variance"]) if record["price_variance"] is not None else None,
            "price_available": record["price_available"],
            "duplicate": record["duplicate"],
            "missing": record["missing"],
            "categories": sorted(record["categories"]),
            "reasons": record["reasons"],
            "audit_snapshot_id": record["audit_snapshot_id"],
            "audited_at": record["audited_at"].isoformat() if record["audited_at"] else None,
        })
    return {
        "items": items,
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "has_more": len(filtered) > offset + limit,
        "scope": scope,
    }
