"""G4 batch import with fixed-template validation and all-or-nothing row acceptance."""
import logging
from datetime import date
from pathlib import PurePath
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from mcias.models import AuditLog
from mcias.models_ingestion import IngestionDocument, IngestionJob, ExtractedSourceRecord, QuarantineItem
from mcias.models_claims import ClaimBatch, ClaimEntry, ClaimAttachment
from mcias.claim_normalization import (normalize, validate_fixed_record,
    ClaimValidationError, FIXED_COLUMNS, FIXED_HEADERS, header_mismatch_message)

logger=logging.getLogger(__name__)

class BatchConflict(ValueError):
    pass

def _document(db,org,document_id):
    document=db.get(IngestionDocument,document_id)
    if document is None or document.organization_id!=org:
        raise BatchConflict("source document not accessible")
    return document

def _job(db,org,document_id):
    jobs=db.scalars(select(IngestionJob).where(IngestionJob.organization_id==org,
        IngestionJob.document_id==document_id).order_by(IngestionJob.created_at.desc())).all()
    if not jobs:
        raise BatchConflict("no extraction job")
    return jobs[0]

def _batch_display_name(value, filename):
    name=(value or '').strip()
    if not name:name=PurePath(filename or '').stem.strip()
    return (name or 'دفعة بلا اسم')[:200]

def stage(db,actor,document_id,display_name=None,entry_date=None):
    if not actor.active or not actor.organization_id:
        raise BatchConflict("active organization required")
    doc=_document(db,actor.organization_id,document_id)
    job=_job(db,actor.organization_id,document_id)
    if doc.status!="extracted" or job.status!="extracted" or not job.manifest.get("complete"):
        raise BatchConflict("source extraction incomplete or quarantined")
    if db.scalar(select(QuarantineItem.id).where(QuarantineItem.job_id==job.id)):
        raise BatchConflict("source has quarantine items")
    existing=db.scalar(select(ClaimBatch).where(ClaimBatch.organization_id==actor.organization_id,
                                                ClaimBatch.ingestion_document_id==doc.id))
    if existing:
        if display_name is not None or entry_date is not None:
            if display_name is not None:existing.display_name=_batch_display_name(display_name,doc.filename)
            if entry_date is not None:existing.entry_date=entry_date
            db.add(AuditLog(actor_id=actor.id,action="claim_batch_metadata_updated",resource_type="claim_batch",
                resource_id=existing.id,details={"display_name":existing.display_name,
                "entry_date":existing.entry_date.isoformat()}))
            db.commit()
        return existing
    count=db.scalar(select(func.count()).select_from(ExtractedSourceRecord).where(
        ExtractedSourceRecord.job_id==job.id))
    if not count:
        raise BatchConflict("source has no extracted records")
    first=db.scalar(select(ExtractedSourceRecord).where(ExtractedSourceRecord.job_id==job.id)
        .order_by(ExtractedSourceRecord.id).limit(1))
    headers=tuple(first.values) if first and isinstance(first.values,dict) else ()
    if len(headers)!=len(FIXED_HEADERS) or set(headers)!=set(FIXED_HEADERS):
        missing=sorted(set(FIXED_HEADERS)-set(headers));extra=sorted(set(headers)-set(FIXED_HEADERS))
        logger.warning('claim_batch_header_mismatch document_id=%s organization_id=%s missing=%s unexpected=%s',
            doc.id,actor.organization_id,','.join(missing) or '-',','.join(extra) or '-')
        raise BatchConflict(header_mismatch_message(missing,extra))
    batch=ClaimBatch(organization_id=actor.organization_id,ingestion_document_id=doc.id,
        created_by=actor.id,display_name=_batch_display_name(display_name,doc.filename),
        entry_date=entry_date or date.today(),source_sha256=doc.sha256,record_count=count,
        imported_count=0,status="staged")
    db.add(batch);db.flush()
    db.add(AuditLog(actor_id=actor.id,action="claim_batch_staged",resource_type="claim_batch",
        resource_id=batch.id,details={"document_id":doc.id,"sha256":doc.sha256,"records":count,
        "display_name":batch.display_name,"entry_date":batch.entry_date.isoformat()}))
    db.commit()
    return batch

def confirmed_mapping(db,batch):
    event=db.scalar(select(AuditLog).where(AuditLog.organization_id==batch.organization_id,
        AuditLog.action=='claim_batch.mapping',AuditLog.resource_id==batch.id).order_by(AuditLog.created_at.desc(),AuditLog.id.desc()).limit(1))
    if not event:return None
    import json
    from mcias.models import SourceFile
    from mcias.services.registry_service import store
    source=db.get(SourceFile,event.details['source_id'])
    if not source:raise BatchConflict('Mapping source missing')
    mapping=json.loads(store().get(source.storage_key,source.sha256))
    if mapping['organization_id']!=batch.organization_id or mapping['batch_id']!=batch.id:
        raise BatchConflict('Mapping scope mismatch')
    return mapping


def source_columns(db,actor,batch_id):
    batch=db.get(ClaimBatch,batch_id)
    if not batch or batch.organization_id!=actor.organization_id:raise BatchConflict('Batch not accessible')
    job=_job(db,actor.organization_id,batch.ingestion_document_id)
    first=db.scalar(select(ExtractedSourceRecord).where(ExtractedSourceRecord.job_id==job.id).order_by(ExtractedSourceRecord.id).limit(1))
    return {'columns':list(first.values) if first else [],'mapping':confirmed_mapping(db,batch),'imported_count':batch.imported_count}


def set_mapping(db,actor,batch_id,columns,currency,date_format):
    import json
    from mcias.models import SourceFile
    from mcias.services.registry_service import store
    from mcias.services.rbac_service import _atomic
    from mcias.services.transaction_lock import lock_record
    with _atomic(db):
        lock_record(db,'claim_mapping',actor.organization_id,batch_id)
        batch=db.scalar(select(ClaimBatch).where(ClaimBatch.id==batch_id,ClaimBatch.organization_id==actor.organization_id).with_for_update())
        if not batch or batch.imported_count:raise BatchConflict('Mapping must be confirmed before importing any row')
        allowed=set(source_columns(db,actor,batch_id)['columns'])
        if (columns!=FIXED_COLUMNS or set(allowed)!=set(FIXED_HEADERS)
                or currency!='LYD' or date_format!='DMY'):
            raise BatchConflict('This laptop edition accepts the approved fixed Excel template only')
        mapping={'organization_id':actor.organization_id,'batch_id':batch.id,'columns':FIXED_COLUMNS,
            'currency':'LYD','date_format':'DMY','amount_basis':'NET','price_basis':'PER_UNIT_GROSS'}
        object_id,digest=store().put(json.dumps(mapping,ensure_ascii=False,sort_keys=True).encode())
        source=SourceFile(sha256=digest,original_filename='claim-column-mapping.json',storage_key=object_id)
        db.add(source);db.flush()
        db.add(AuditLog(actor_id=actor.id,organization_id=actor.organization_id,action='claim_batch.mapping',
            resource_type='claim_batch',resource_id=batch.id,details={'source_id':source.id,'source_sha256':digest,'amount_basis':'NET'}))
        db.flush();return {'batch_id':batch.id,'amount_basis':'NET','source_sha256':digest}


def preview(db,actor,batch_id,limit=100,offset=0):
    batch=db.get(ClaimBatch,batch_id)
    if batch is None or batch.organization_id!=actor.organization_id:
        raise BatchConflict("batch not accessible")
    job=_job(db,actor.organization_id,batch.ingestion_document_id)
    records=db.scalars(select(ExtractedSourceRecord).where(ExtractedSourceRecord.job_id==job.id)
        .order_by(ExtractedSourceRecord.id).offset(offset).limit(limit)).all()
    results=[]
    mapping=confirmed_mapping(db,batch)
    for record in records:
        try:
            validate_fixed_record(record.values)
            normalized=normalize(record.values,mapping)
            results.append({"record_id":record.id,"locator":record.locator,"valid":True,
                            "claim_reference":normalized["claim_reference"]})
        except ClaimValidationError as exc:
            results.append({"record_id":record.id,"locator":record.locator,"valid":False,"reason":str(exc)})
    return {"batch_id":batch.id,"record_count":batch.record_count,"rows":results}

def resume(db,actor,batch_id,chunk_size=100):
    if not actor.active or not actor.organization_id:
        raise BatchConflict("active organization required")
    batch=db.scalar(select(ClaimBatch).where(ClaimBatch.id==batch_id, ClaimBatch.organization_id==actor.organization_id).with_for_update())
    if batch is None or batch.organization_id!=actor.organization_id:
        raise BatchConflict("batch not accessible")
    if batch.status=="imported":
        return {"batch_id":batch.id,"status":batch.status,"imported_count":batch.imported_count}
    if batch.status not in ("staged","importing","review_required","invalid_source"):
        raise BatchConflict("invalid batch state")
    if batch.status=="invalid_source" and batch.imported_count:
        raise BatchConflict("invalid batch state")
    doc=_document(db,actor.organization_id,batch.ingestion_document_id)
    if doc.sha256!=batch.source_sha256 or doc.status!="extracted":
        raise BatchConflict("source changed or incomplete")
    job=_job(db,actor.organization_id,doc.id)
    if job.status!="extracted" or not job.manifest.get("complete"):
        raise BatchConflict("extraction not complete")
    if db.scalar(select(QuarantineItem.id).where(QuarantineItem.job_id==job.id)):
        raise BatchConflict("source has quarantine")
    records=db.scalars(select(ExtractedSourceRecord).where(ExtractedSourceRecord.job_id==job.id)
        .order_by(ExtractedSourceRecord.id)).all()
    if len(records)!=batch.record_count:
        raise BatchConflict("source record count changed")
    already=set(db.scalars(select(ClaimEntry.extracted_record_id).where(ClaimEntry.batch_id==batch.id)).all())
    if chunk_size<=0 or chunk_size>1000:
        raise BatchConflict("invalid chunk size")
    mapping=confirmed_mapping(db,batch)
    if mapping is None or mapping.get('columns')!=FIXED_COLUMNS:
        raise BatchConflict('The approved fixed template must be confirmed before import')
    normalized_records={}
    rejected=[]
    for record in records:
        try:
            validate_fixed_record(record.values)
            normalized_records[record.id]=normalize(record.values,mapping)
        except ClaimValidationError as exc:
            rejected.append({"record_id":record.id,"locator":record.locator,"reason":str(exc)})
    if rejected:
        if batch.imported_count:
            logger.warning('claim_batch_validation_failed batch_id=%s organization_id=%s imported=%s reason=partial_import_conflict',
                batch.id,actor.organization_id,batch.imported_count)
            raise BatchConflict("A partially imported legacy batch has validation errors; no new rows were imported")
        logger.warning('claim_batch_validation_failed batch_id=%s organization_id=%s invalid_rows=%s record_count=%s reasons=%s',
            batch.id,actor.organization_id,len(rejected),batch.record_count,
            [item['reason'] for item in rejected[:20]])
        batch.status="invalid_source"
        db.add(AuditLog(actor_id=actor.id,action="claim_batch.validation_failed",resource_type="claim_batch",
            resource_id=batch.id,details={"invalid_rows":len(rejected),"record_count":batch.record_count,
                "source_sha256":batch.source_sha256}))
        db.commit()
        return {"batch_id":batch.id,"status":batch.status,"imported_count":0,
            "record_count":batch.record_count,"rejected":rejected}
    accepted=0
    for record in records:
        if record.id in already:
            continue
        if accepted>=chunk_size:
            break
        accepted+=1
        db.add(ClaimEntry(organization_id=actor.organization_id,batch_id=batch.id,
            extracted_record_id=record.id,source_locator=record.locator,source_values=record.values,
            **normalized_records[record.id]))
        batch.imported_count+=1
    db.flush()
    batch.imported_count=db.scalar(select(func.count()).select_from(ClaimEntry).where(ClaimEntry.batch_id==batch.id))
    batch.status="review_required" if rejected else ("imported" if batch.imported_count==batch.record_count else "importing")
    logger.info('claim_batch_import_status batch_id=%s organization_id=%s status=%s imported=%s/%s',
        batch.id,actor.organization_id,batch.status,batch.imported_count,batch.record_count)
    db.add(AuditLog(actor_id=actor.id,action="claim_batch_resumed",resource_type="claim_batch",
        resource_id=batch.id,details={"imported":batch.imported_count,"rejected_count":len(rejected),
                                       "status":batch.status}))
    db.commit()
    return {"batch_id":batch.id,"status":batch.status,"imported_count":batch.imported_count,
            "record_count":batch.record_count,"rejected":rejected}

def attach(db,actor,claim_id,document_id):
    claim=db.get(ClaimEntry,claim_id)
    if claim is None or claim.organization_id!=actor.organization_id:
        logger.warning('claim_attachment_conflict claim_id=%s organization_id=%s reason=claim_not_accessible',
            claim_id,actor.organization_id)
        raise BatchConflict("claim not accessible")
    doc=_document(db,actor.organization_id,document_id)
    if doc.status not in ("extracted","review_required"):
        logger.warning('claim_attachment_conflict claim_id=%s document_id=%s document_status=%s organization_id=%s reason=source_not_processed',
            claim_id,doc.id,doc.status,actor.organization_id)
        raise BatchConflict("attachment source is not processed")
    existing=db.scalar(select(ClaimAttachment).where(ClaimAttachment.claim_id==claim.id,
        ClaimAttachment.ingestion_document_id==doc.id))
    if existing:
        return existing
    link=ClaimAttachment(organization_id=actor.organization_id,claim_id=claim.id,
        ingestion_document_id=doc.id,document_sha256=doc.sha256,linked_by=actor.id)
    db.add(link);db.flush()
    db.add(AuditLog(actor_id=actor.id,action="claim_attachment_linked",resource_type="claim_attachment",
        resource_id=link.id,details={"claim_id":claim.id,"document_id":doc.id,"sha256":doc.sha256}))
    db.commit()
    return link
