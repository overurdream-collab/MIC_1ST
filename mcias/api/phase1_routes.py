from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from mcias.api import dependencies as deps
from mcias.api.features_routes import no_store
from mcias.reporting_g7 import ReportBlocked
from mcias.report_exports_g7 import render_phase1_file
from mcias.services import phase1_rules as service

router = APIRouter(prefix='/api/v1/phase1', tags=['Phase 1 rules audit'],
                   dependencies=[Depends(no_store)])


def perform(db, action):
    try:
        return action()
    except LookupError:
        db.rollback()
        raise HTTPException(404, 'Phase 1 result or batch not found') from None
    except (ValueError, ReportBlocked) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    except RuntimeError:
        db.rollback()
        raise HTTPException(503, 'Encrypted results storage unavailable') from None


@router.post('/batches/{batch_id}/run', status_code=201)
def run(batch_id: UUID, actor=Depends(deps.require_permission('claims.read', 'claims.review')),
        db=Depends(deps.get_db)):
    def action():
        result = service.freeze(db, actor, str(batch_id))
        db.commit()
        return result
    return perform(db, action)


@router.get('/results')
def listing(offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
            actor=Depends(deps.require_permission('reports.read')), db=Depends(deps.get_db)):
    return service.list_results(db, actor, offset, limit)


@router.get('/results/{result_id}/lines')
def lines(result_id: UUID, status: Literal['accepted', 'rejected'] | None = None,
          offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
          actor=Depends(deps.require_permission('reports.read', 'claims.read')),
          db=Depends(deps.get_db)):
    dataset = perform(db, lambda: service.read(db, actor, str(result_id)))
    rows = [row for row in dataset['rows'] if status is None or row['status'] == status]
    return {'items': rows[offset:offset + limit], 'has_more': len(rows) > offset + limit,
            'summary': dataset['summary'], 'stage': service.STAGE}


@router.get('/results/{result_id}/green')
def green(result_id: UUID, actor=Depends(deps.require_permission('reports.export')),
          db=Depends(deps.get_db)):
    def action():
        dataset = service.read(db, actor, str(result_id))
        return render_phase1_file(dataset, 'green')
    data = perform(db, action)
    return Response(data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="mcias-green-{result_id}.xlsx"',
                             'Cache-Control': 'no-store'})


@router.get('/results/{result_id}/red')
def red(result_id: UUID, actor=Depends(deps.require_permission('reports.export')),
        db=Depends(deps.get_db)):
    def action():
        dataset = service.read(db, actor, str(result_id))
        return render_phase1_file(dataset, 'red')
    data = perform(db, action)
    return Response(data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="mcias-red-{result_id}.xlsx"',
                             'Cache-Control': 'no-store'})
