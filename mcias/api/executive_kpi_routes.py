"""Read-only executive KPI endpoints over frozen preliminary results."""
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from mcias.api import dependencies as deps
from mcias.api.features_routes import no_store
from mcias.reporting_g7 import ReportBlocked
from mcias.services import executive_kpi_service as service

router = APIRouter(
    prefix="/api/v1/analytics",
    tags=["Executive KPIs"],
    dependencies=[Depends(no_store)],
)


def _filters(
    batch_id: UUID | None,
    result_id: UUID | None,
    provider: str | None,
    service_code: str | None,
    currency: str | None,
    outcome: str | None,
    rule: str | None,
    date_basis: str,
    date_from: date | None,
    date_to: date | None,
    q: str | None,
) -> dict:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "date_from must not be after date_to")
    return {
        "batch_id": str(batch_id) if batch_id else None,
        "result_id": str(result_id) if result_id else None,
        "provider": provider,
        "service": service_code,
        "currency": currency,
        "outcome": outcome,
        "rule": rule,
        "date_basis": date_basis,
        "date_from": date_from,
        "date_to": date_to,
        "q": q,
    }


def _perform(action):
    try:
        return action()
    except HTTPException:
        raise
    except LookupError:
        raise HTTPException(404, "Result set or batch not found") from None
    except (ValueError, ReportBlocked) as exc:
        raise HTTPException(409, str(exc)) from None
    except RuntimeError:
        raise HTTPException(503, "Encrypted results storage unavailable") from None


@router.get("/kpi")
def dashboard(
    batch_id: UUID | None = None,
    result_id: UUID | None = None,
    provider: str | None = Query(None, max_length=80),
    service_code: str | None = Query(None, max_length=120),
    currency: str | None = Query(None, max_length=12),
    outcome: Literal["accepted", "rejected", "held", "review", "matched", "unmatched", "unverified", "flagged"] | None = None,
    rule: str | None = Query(None, max_length=120),
    date_basis: Literal["service", "audit"] = "service",
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(None, max_length=120),
    actor=Depends(deps.require_permission("reports.read")),
    db=Depends(deps.get_db),
):
    filters = _filters(batch_id, result_id, provider, service_code, currency, outcome, rule,
                       date_basis, date_from, date_to, q)
    return _perform(lambda: service.dashboard(db, actor, filters))


@router.get("/lines")
def result_lines(
    batch_id: UUID | None = None,
    result_id: UUID | None = None,
    provider: str | None = Query(None, max_length=80),
    service_code: str | None = Query(None, max_length=120),
    currency: str | None = Query(None, max_length=12),
    outcome: Literal["accepted", "rejected", "held", "review", "matched", "unmatched", "unverified", "flagged"] | None = None,
    rule: str | None = Query(None, max_length=120),
    date_basis: Literal["service", "audit"] = "service",
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(None, max_length=120),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    actor=Depends(deps.require_permission("reports.read")),
    db=Depends(deps.get_db),
):
    filters = _filters(batch_id, result_id, provider, service_code, currency, outcome, rule,
                       date_basis, date_from, date_to, q)
    return _perform(lambda: service.lines(db, actor, filters, offset, limit))
