"""G4 source-to-batch, idempotent resume, attachment and tenant isolation."""
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from mcias.models import Base, Organization, User
from mcias.models_ingestion import IngestionDocument, IngestionJob, ExtractedSourceRecord
from mcias.models_claims import ClaimEntry,ClaimAttachment
from mcias.claim_batches import stage,resume,preview,attach,set_mapping,BatchConflict
from mcias.claim_normalization import normalize,FIXED_COLUMNS
from datetime import datetime,timezone
import pytest

@pytest.fixture(autouse=True)
def _storage(tmp_path,monkeypatch):
    monkeypatch.setenv("MCIAS_STORAGE_DIR",str(tmp_path/"sources"))
    monkeypatch.setenv("MCIAS_STORAGE_KEY","11"*32)

def _fixture(db,valid=True):
    org=Organization(name="G4 synthetic")
    user=User(username="g4-tester",password_hash="fixture")
    db.add_all([org,user]);db.flush()
    user.organization_id=org.id
    from mcias.models import SourceFile
    source=SourceFile(sha256="a"*64,original_filename="claims.csv",storage_key="synthetic-claims")
    db.add(source);db.flush()
    doc=IngestionDocument(organization_id=org.id,source_id=source.id,
        sha256="a"*64,filename="claims.csv",status="extracted")
    db.add(doc);db.flush()
    job=IngestionJob(organization_id=org.id,document_id=doc.id,status="extracted",
        manifest={"complete":True})
    db.add(job);db.flush()
    values={FIXED_COLUMNS["claim_reference"]:"C1",FIXED_COLUMNS["beneficiary_token"]:"opaque",
        FIXED_COLUMNS["beneficiary_name"]:"SYN beneficiary",FIXED_COLUMNS["category_code"]:"أساسي",
        FIXED_COLUMNS["provider_code"]:"P",FIXED_COLUMNS["service_code"]:"S",
        FIXED_COLUMNS["service_date"]:"10/10/2026",FIXED_COLUMNS["unit_price"]:"99.1250",
        FIXED_COLUMNS["gross_amount"]:"99.1250",FIXED_COLUMNS["copay_percent"]:"0",
        FIXED_COLUMNS["copay_amount"]:"0",FIXED_COLUMNS["claimed_amount"]:"99.1250"}
    if not valid:values[FIXED_COLUMNS["claimed_amount"]]="bad"
    row=ExtractedSourceRecord(job_id=job.id,locator={"kind":"row","index":1,"row":2},
        values=values,confidence=100)
    db.add(row);db.commit()
    return org,user,doc,row

def test_g4_stage_resume_idempotent():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        org,user,doc,row=_fixture(db)
        batch=stage(db,user,doc.id)
        assert stage(db,user,doc.id).id==batch.id
        set_mapping(db,user,batch.id,FIXED_COLUMNS,"LYD","DMY")
        assert preview(db,user,batch.id)["rows"][0]["valid"]
        first=resume(db,user,batch.id)
        assert first["status"]=="imported" and first["imported_count"]==1
        assert resume(db,user,batch.id)["imported_count"]==1
        entry=db.scalar(select(ClaimEntry))
        assert entry.source_locator["row"]==2
        assert entry.source_values[FIXED_COLUMNS["claim_reference"]]=="C1"
        assert str(entry.claimed_amount)=="99.1250"
        link=attach(db,user,entry.id,doc.id)
        assert attach(db,user,entry.id,doc.id).id==link.id
        assert db.query(ClaimAttachment).count()==1
    engine.dispose()

def test_g4_invalid_source_row_requires_review():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        org,user,doc,row=_fixture(db,valid=False)
        batch=stage(db,user,doc.id)
        set_mapping(db,user,batch.id,FIXED_COLUMNS,"LYD","DMY")
        result=resume(db,user,batch.id)
        assert result["status"]=="invalid_source"
        assert result["imported_count"]==0
        assert result["rejected"][0]["locator"]["row"]==2
        assert "الصافي" in result["rejected"][0]["reason"]
    engine.dispose()

def test_g4_tenant_cannot_stage_foreign_document():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        org,user,doc,row=_fixture(db)
        other=Organization(name="Other")
        stranger=User(username="other-g4",password_hash="fixture")
        db.add_all([other,stranger]);db.flush()
        stranger.organization_id=other.id;db.commit()
        with pytest.raises(BatchConflict,match="not accessible"):
            stage(db,stranger,doc.id)
    engine.dispose()

def test_g4_resume_in_bounded_chunks():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        org,user,doc,row=_fixture(db)
        for i in range(1,25):
            db.add(ExtractedSourceRecord(job_id=row.job_id,
                locator={"kind":"row","index":i+1,"row":i+2},
                values={**row.values,FIXED_COLUMNS["claim_reference"]:f"C{i+1}"},confidence=100))
        db.commit()
        batch=stage(db,user,doc.id)
        set_mapping(db,user,batch.id,FIXED_COLUMNS,"LYD","DMY")
        assert batch.record_count==25
        first=resume(db,user,batch.id,chunk_size=10)
        assert first["status"]=="importing" and first["imported_count"]==10
        db.expire_all()
        second=resume(db,user,batch.id,chunk_size=10)
        assert second["imported_count"]==20
        final=resume(db,user,batch.id,chunk_size=10)
        assert final["status"]=="imported" and final["imported_count"]==25
        assert resume(db,user,batch.id,chunk_size=10)["imported_count"]==25
        assert db.query(ClaimEntry).count()==25
    engine.dispose()

def test_g4_crash_before_commit_recovers_without_duplicates(monkeypatch):
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        org,user,doc,row=_fixture(db)
        batch=stage(db,user,doc.id)
        set_mapping(db,user,batch.id,FIXED_COLUMNS,"LYD","DMY")
        original_flush=db.flush
        calls=[0]
        def fail_once(*args,**kwargs):
            calls[0]+=1
            if calls[0]==1:
                raise RuntimeError("simulated process interruption before checkpoint")
            return original_flush(*args,**kwargs)
        monkeypatch.setattr(db,"flush",fail_once)
        with pytest.raises(RuntimeError,match="interruption"):
            resume(db,user,batch.id,chunk_size=1)
        db.rollback()
        monkeypatch.setattr(db,"flush",original_flush)
        assert db.query(ClaimEntry).count()==0
        recovered=resume(db,user,batch.id,chunk_size=1)
        assert recovered["status"]=="imported"
        assert recovered["imported_count"]==1
        assert db.query(ClaimEntry).count()==1
    engine.dispose()

def test_g4_large_synthetic_batch_checkpoints():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        org,user,doc,row=_fixture(db)
        for i in range(1,1001):
            db.add(ExtractedSourceRecord(job_id=row.job_id,
                locator={"kind":"row","row":i+2},
                values={**row.values,FIXED_COLUMNS["claim_reference"]:f"C{i+1}"},confidence=100))
        db.commit()
        batch=stage(db,user,doc.id)
        set_mapping(db,user,batch.id,FIXED_COLUMNS,"LYD","DMY")
        assert batch.record_count==1001
        for expected in (200,400,600,800,1000,1001):
            result=resume(db,user,batch.id,chunk_size=200)
            assert result["imported_count"]==expected
        assert result["status"]=="imported"
        assert db.query(ClaimEntry).count()==1001
    engine.dispose()

def test_g4_cross_tenant_attachment_denied():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        org,user,doc,row=_fixture(db)
        batch=stage(db,user,doc.id)
        set_mapping(db,user,batch.id,FIXED_COLUMNS,"LYD","DMY")
        resume(db,user,batch.id)
        entry=db.scalar(select(ClaimEntry))
        other=Organization(name="G4 foreign")
        stranger=User(username="g4-stranger",password_hash="fixture")
        db.add_all([other,stranger]);db.flush()
        stranger.organization_id=other.id
        db.commit()
        with pytest.raises(BatchConflict,match="not accessible"):
            attach(db,stranger,entry.id,doc.id)
        with pytest.raises(BatchConflict,match="not accessible"):
            attach(db,user,entry.id,"unknown")
    engine.dispose()
