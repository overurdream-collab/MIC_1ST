"""Tenant-isolated G4 batch and claim API."""
import logging
from datetime import date
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from mcias.app import get_db
from mcias.auth import resolve_user
from mcias.tenant import require_organization
from mcias.models_claims import ClaimBatch, ClaimEntry, ClaimAttachment
from mcias.claim_batches import BatchConflict, stage, preview, resume, attach, source_columns, set_mapping
from mcias.services import claim_reference_service as reference_checks
from mcias.services.claim_reference_service import ReferenceCheckConflict
from mcias.services.authorization_service import check_permission_unified
from mcias.schemas.common import Input
from pydantic import Field
from typing import Literal

logger=logging.getLogger(__name__)

router=APIRouter(prefix="/api/v1/claims",tags=["claims"])

class ColumnMapping(Input):
    columns:dict[str,str]=Field(max_length=14)
    currency:str=Field(pattern=r'^[A-Z]{3}$')
    date_format:Literal['ISO','DMY','YMD_SLASH']

class BatchMetadata(Input):
    display_name:str=Field(min_length=1,max_length=200)
    entry_date:date

@router.get('/batches/{batch_id}/columns')
def columns(batch_id:str,authorization:str|None=Header(default=None),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,'claims.import')
    return _run(db,lambda:source_columns(db,actor,batch_id))

@router.put('/batches/{batch_id}/mapping')
def mapping(batch_id:str,payload:ColumnMapping,authorization:str|None=Header(default=None),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,'claims.import')
    result=_run(db,lambda:set_mapping(db,actor,batch_id,payload.columns,payload.currency,payload.date_format))
    db.commit();return result

def _actor(db,authorization,permission):
    user,_=resolve_user(db,authorization)
    if not user.organization_id:
        raise HTTPException(403,"organization required")
    require_organization(db,user,user.organization_id,permission)
    return user

def _run(db,fn):
    try:
        return fn()
    except BatchConflict as exc:
        db.rollback()
        raise HTTPException(409,str(exc)) from exc
    except __import__("sqlalchemy").exc.IntegrityError as exc:
        db.rollback()
        raise HTTPException(409,"claim integrity constraint") from exc

def _reference_detail(exc):
    detail={'code':'reference_check_conflict','message':str(exc)}
    if exc.batch_id is not None:detail['batch_id']=exc.batch_id
    if exc.status is not None:detail['status']=exc.status
    if exc.reason is not None:detail['reason']=exc.reason
    if exc.imported_count is not None:detail['imported_count']=exc.imported_count
    if exc.record_count is not None:detail['record_count']=exc.record_count
    return detail

def _reference_run(db,fn):
    try:return fn()
    except LookupError:db.rollback();raise HTTPException(404,'Batch or reference check not found') from None
    except ReferenceCheckConflict as exc:
        db.rollback()
        logger.warning('reference_check_conflict batch_id=%s status=%s reason=%s message=%s',
            exc.batch_id,exc.status,exc.reason,str(exc))
        raise HTTPException(409,detail=_reference_detail(exc)) from None
    except ValueError as exc:
        db.rollback()
        logger.warning('reference_check_conflict message=%s',str(exc))
        raise HTTPException(409,detail={'code':'reference_check_conflict','message':str(exc)}) from None
    except (RuntimeError,OSError):
        db.rollback()
        logger.exception('reference_check_storage_unavailable')
        raise HTTPException(503,'Encrypted reference storage unavailable') from None

@router.post('/batches/{batch_id}/reference-checks',status_code=201)
def run_reference_checks(batch_id:str,authorization:str|None=Header(default=None),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,'claims.review')
    require_organization(db,actor,actor.organization_id,'claims.read')
    result=_reference_run(db,lambda:reference_checks.run(db,actor,batch_id))
    db.commit();return result

@router.get('/batches/{batch_id}/reference-checks')
def get_reference_checks(batch_id:str,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),
                         authorization:str|None=Header(default=None),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,'claims.read')
    require_organization(db,actor,actor.organization_id,'claims.review')
    result=_reference_run(db,lambda:reference_checks.read(db,actor,batch_id))
    rows=result.pop('rows');return {**result,'rows':rows[offset:offset+limit],'has_more':len(rows)>offset+limit}

@router.post("/batches/from-document/{document_id}",status_code=201)
def create_batch(document_id: str,payload:BatchMetadata|None=None,
                 authorization: str | None=Header(default=None),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,"claims.import")
    batch=_run(db,lambda:stage(db,actor,document_id,
        payload.display_name if payload else None,payload.entry_date if payload else None))
    return {"id":batch.id,"display_name":batch.display_name,"entry_date":batch.entry_date.isoformat(),
            "status":batch.status,"record_count":batch.record_count}

@router.get("/batches/{batch_id}/preview")
def batch_preview(batch_id:str,limit:int=Query(default=100,ge=1,le=500),
                  offset:int=Query(default=0,ge=0),
                  authorization:str|None=Header(default=None),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,"claims.read")
    return _run(db,lambda:preview(db,actor,batch_id,limit,offset))

@router.post("/batches/{batch_id}/resume")
def resume_batch(batch_id:str,chunk_size:int=Query(default=100,ge=1,le=1000),
                 authorization:str|None=Header(default=None),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,"claims.import")
    result=_run(db,lambda:resume(db,actor,batch_id,chunk_size))
    if result['status']=='imported' and check_permission_unified(db,actor,'claims.review') and check_permission_unified(db,actor,'claims.read'):
        result['reference_check']=_reference_run(db,lambda:reference_checks.run(db,actor,batch_id))
        db.commit()
    else:result['reference_check_required']=True
    return result

@router.get("/batches/{batch_id}")
def get_batch(batch_id:str,authorization:str|None=Header(default=None),
              db:Session=Depends(get_db)):
    actor=_actor(db,authorization,"claims.read")
    batch=db.get(ClaimBatch,batch_id)
    if batch is None or batch.organization_id!=actor.organization_id:
        raise HTTPException(404,"batch not found")
    return {"id":batch.id,"display_name":batch.display_name,"entry_date":batch.entry_date.isoformat(),
            "status":batch.status,"record_count":batch.record_count,
            "imported_count":batch.imported_count,"source_sha256":batch.source_sha256}

@router.get("/batches/{batch_id}/entries")
def batch_entries(batch_id:str,limit:int=Query(default=100,ge=1,le=500),
                  offset:int=Query(default=0,ge=0),
                  authorization:str|None=Header(default=None),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,"claims.read")
    batch=db.get(ClaimBatch,batch_id)
    if batch is None or batch.organization_id!=actor.organization_id:
        raise HTTPException(404,"batch not found")
    entries=db.scalars(select(ClaimEntry).where(ClaimEntry.batch_id==batch.id,
        ClaimEntry.organization_id==actor.organization_id).order_by(ClaimEntry.id)
        .offset(offset).limit(limit)).all()
    def readable(c):
        source=c.source_values if isinstance(c.source_values,dict) else {}
        return {"id":c.id,"claim_reference":c.claim_reference,
            "beneficiary_name":source.get("اسم المستفيد") or source.get("الرقم التأميني"),
            "beneficiary_token":source.get("الرقم التأميني") or c.beneficiary_token,
            "category_code":source.get("الدرجة"),"provider_code":source.get("مقدم الخدمة"),
            "service_code":source.get("الخدمة"),"source_locator":c.source_locator,
            "claimed_amount":None if c.claimed_amount is None else str(c.claimed_amount),
            "currency":c.currency,"service_date":c.service_date.isoformat()}
    return {"batch_id":batch.id,"entries":[readable(c) for c in entries]}

@router.post("/entries/{claim_id}/attachments/{document_id}",status_code=201)
def link_attachment(claim_id:str,document_id:str,authorization:str|None=Header(default=None),
                    kind:Literal['invoice','supporting']=Query('supporting'),db:Session=Depends(get_db)):
    actor=_actor(db,authorization,"claims.import")
    item=_run(db,lambda:attach(db,actor,claim_id,document_id))
    if kind=='invoice':
        from mcias.services.invoice_service import classify_invoice
        classify_invoice(db,actor,item);db.commit()
    return {"id":item.id,"claim_id":item.claim_id,"document_sha256":item.document_sha256}
