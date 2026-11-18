"""Real upload/import/export; synthetic pre-existing G5 evidence, not agent acceptance."""
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile
import json
import logging
import pytest
from openpyxl import load_workbook, Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.api.conftest import api_factory, synthetic_password_hash
from tests.features.conftest import features_api
from tests.workspace_v2.test_access_and_registry import workspace
from mcias.models import SourceFile
from mcias.models_claims import ClaimEntry, ClaimBatch
from mcias.models_ingestion import ExtractedSourceRecord, IngestionDocument, IngestionJob
from mcias.models_audit import AuditSnapshot
from mcias.audit_agents import AGENTS
from mcias.audit_engine import AuditPolicy, evaluate_net
from mcias.reporting_g7 import canonical, ReportBlocked
from mcias.services import result_service as results
from mcias.services.registry_service import store
from mcias.claim_normalization import FIXED_COLUMNS, validate_fixed_record, ClaimValidationError


def upload_batch(api,raw,filename='SYN-claims.xlsx'):
    upload=api.client.post('/api/v1/ingestion/uploads?filename='+filename,content=raw,
        headers={**api.headers['root'],'Content-Type':'application/octet-stream'})
    assert upload.status_code==200,upload.text
    doc=upload.json()['document_id']
    stage=api.client.post('/api/v1/claims/batches/from-document/'+doc,headers=api.headers['root'])
    assert stage.status_code==201,stage.text
    return stage.json()['id'],doc


def arabic_workbook():
    book=Workbook();sheet=book.active;sheet.title='تفاصيل المطالبات'
    sheet.append(['رقم الفاتورة','الرقم التأميني','اسم المستفيد','الدرجة','مقدم الخدمة','الخدمة',
        'تاريخ الخدمة','السعر','المطلوب','نسبة التحمل','إجمالي التحمل','الصافي'])
    for service in ('SYN-visit','SYN-test','SYN-drug','SYN-xray'):
        sheet.append(['SYN-SAME-INVOICE','SYN-BENEFICIARY','SYN مستفيد تجريبي','B','SYN-HOSPITAL',service,
            '08/06/2026','100','100','20','20','80'])
    stream=BytesIO();book.save(stream);return stream.getvalue()


def mapping():
    return {'columns':{'claim_reference':'رقم الفاتورة','beneficiary_token':'الرقم التأميني',
        'beneficiary_name':'اسم المستفيد','category_code':'الدرجة','provider_code':'مقدم الخدمة',
        'service_code':'الخدمة','service_date':'تاريخ الخدمة','unit_price':'السعر','gross_amount':'المطلوب',
        'copay_percent':'نسبة التحمل','copay_amount':'إجمالي التحمل','claimed_amount':'الصافي'},
        'currency':'LYD','date_format':'DMY'}


@pytest.mark.parametrize('header',['الرقم التأميني','اسم المستفيد'])
def test_fixed_template_requires_beneficiary_identity(header):
    values={name:'SYN' for name in FIXED_COLUMNS.values()}
    values.update({'تاريخ الخدمة':'08/06/2026','السعر':'100','المطلوب':'100',
        'نسبة التحمل':'20','إجمالي التحمل':'20','الصافي':'80',header:'   '})
    with pytest.raises(ClaimValidationError,match=header):
        validate_fixed_record(values)


def imported(api):
    raw=arabic_workbook();batch,doc=upload_batch(api,raw)
    path='/api/v1/claims/batches/'+batch
    mapped=api.client.put(path+'/mapping',json=mapping(),headers=api.headers['root'])
    assert mapped.status_code==200,mapped.text
    response=api.client.post(path+'/resume',headers=api.headers['root'])
    assert response.status_code==200 and response.json()['imported_count']==4,response.text
    return batch,doc,raw


def snapshots(api,batch):
    from tests.workspace_v2.invoice_fixtures import attach_invoices
    attach_invoices(api,batch)
    with Session(api.engine) as db:
        claims=list(db.scalars(select(ClaimEntry).where(ClaimEntry.batch_id==batch).order_by(ClaimEntry.service_code)))
        for i,claim in enumerate(claims):
            findings=[{'agent':name,'status':'pass','reason':'SYN checked evidence','reference':'SYN-reference'} for name in AGENTS]
            reference_check=None;policy_fp='SYN-fingerprint'
            if i==2:calc={'status':'held','reason':'missing_reference'};verification={'status':'held'}
            else:
                from mcias.services.invoice_service import invoices
                from mcias.services.net_audit_service import reference_fingerprint
                from mcias.audit_service import fingerprint_policy
                reference_check={'calculation':{'quantity':'1','gross':'100'},
                    'references':{'contract':{'id':'SYN-contract'},'beneficiary_enrollment':{'id':'SYN-enrollment'}},
                    'invoices':invoices(db,claim),'source_columns':{**mapping()['columns'],'gross_amount':'المطلوب','copay_amount':'إجمالي التحمل'}}
                policy=AuditPolicy('SYN-policy','LYD','HALF_UP',2,('exclusion','tariff','cap','copay'),Decimal('20'),None,Decimal('0'),Decimal('75' if i==1 else '100'),True,
                    amount_basis='NET',reference_fingerprint=reference_fingerprint(reference_check))
                policy_fp=fingerprint_policy(policy)
                calc=evaluate_net(claim,policy,reference_check)
                verification={'status':'verified'}
                if i==3:findings[0]['status']='review'
            db.add(AuditSnapshot(organization_id=claim.organization_id,claim_id=claim.id,policy_id='SYN-policy',
                policy_fingerprint=policy_fp,calculation=calc,findings={'agents':findings,'reference_check':reference_check},verification=verification,created_by=api.ids['root']))
        db.commit()


def test_arabic_mapping_imports_net_and_preserves_original_bytes(workspace):
    api=workspace;batch,doc,raw=imported(api)
    with Session(api.engine) as db:
        claims=list(db.scalars(select(ClaimEntry).where(ClaimEntry.batch_id==batch)))
        assert all(c.claimed_amount==Decimal('80') and c.service_date==date(2026,6,8) for c in claims)
        assert all(c.source_values['المطلوب']=='100' and c.source_values['الصافي']=='80' for c in claims)
        source=db.get(SourceFile,db.get(IngestionDocument,doc).source_id)
        assert source.sha256==sha256(raw).hexdigest()
        assert store().get(source.storage_key,source.sha256)==raw
    assert api.client.put('/api/v1/claims/batches/'+batch+'/mapping',json=mapping(),headers=api.headers['root']).status_code==409


def test_excel_date_cells_and_iso_dates_are_accepted(workspace):
    book=load_workbook(BytesIO(arabic_workbook()));sheet=book.active
    sheet['G2']=datetime(2026,6,8);sheet['G3']='2026-06-08'
    output=BytesIO();book.save(output)
    api=workspace;batch,_=upload_batch(api,output.getvalue())
    path='/api/v1/claims/batches/'+batch
    assert api.client.put(path+'/mapping',json=mapping(),headers=api.headers['root']).status_code==200
    preview=api.client.get(path+'/preview?limit=10',headers=api.headers['root'])
    assert preview.status_code==200 and all(row['valid'] for row in preview.json()['rows']),preview.text
    response=api.client.post(path+'/resume',headers=api.headers['root'])
    assert response.status_code==200 and response.json()['imported_count']==4,response.text


def test_gross_column_is_rejected_and_mapping_cannot_cross_tenants(workspace):
    api=workspace;batch,_=upload_batch(api,arabic_workbook());payload=mapping();payload['columns']['claimed_amount']='المطلوب'
    assert api.client.put('/api/v1/claims/batches/'+batch+'/mapping',json=payload,headers=api.headers['root']).status_code==409
    assert api.client.get('/api/v1/claims/batches/'+batch+'/columns',headers=api.headers['other']).status_code==403


def test_invalid_required_cell_rejects_entire_workbook_without_partial_import(workspace):
    from openpyxl import load_workbook
    book=load_workbook(BytesIO(arabic_workbook()));sheet=book.active;sheet['B3']='   '
    output=BytesIO();book.save(output)
    api=workspace;batch,_=upload_batch(api,output.getvalue())
    base='/api/v1/claims/batches/'+batch
    assert api.client.put(base+'/mapping',json=mapping(),headers=api.headers['root']).status_code==200
    response=api.client.post(base+'/resume',headers=api.headers['root'])
    assert response.status_code==200,response.text
    result=response.json()
    assert result['status']=='invalid_source' and result['imported_count']==0
    assert len(result['rejected'])==1 and 'الرقم التأميني' in result['rejected'][0]['reason']
    with Session(api.engine) as db:
        assert db.scalar(select(ClaimEntry.id).where(ClaimEntry.batch_id==batch)) is None


def test_invalid_source_can_be_rechecked_after_source_correction(workspace):
    book=load_workbook(BytesIO(arabic_workbook()));book.active['B3']='   '
    output=BytesIO();book.save(output)
    api=workspace;batch,doc=upload_batch(api,output.getvalue())
    base='/api/v1/claims/batches/'+batch
    assert api.client.put(base+'/mapping',json=mapping(),headers=api.headers['root']).status_code==200
    first=api.client.post(base+'/resume',headers=api.headers['root'])
    assert first.json()['status']=='invalid_source' and first.json()['imported_count']==0,first.text
    with Session(api.engine) as db:
        job=db.scalar(select(IngestionJob).where(IngestionJob.document_id==doc).order_by(IngestionJob.created_at.desc()))
        for record in db.scalars(select(ExtractedSourceRecord).where(ExtractedSourceRecord.job_id==job.id)):
            values=dict(record.values)
            if not str(values.get('الرقم التأميني','')).strip():
                values['الرقم التأميني']='SYN-BENEFICIARY'
                record.values=values
        db.commit()
    second=api.client.post(base+'/resume',headers=api.headers['root'])
    assert second.status_code==200,second.text
    assert second.json()['status']=='imported' and second.json()['imported_count']==4,second.text


def test_batch_name_and_entry_date_are_stored_and_listed(workspace):
    api=workspace;raw=arabic_workbook()
    upload=api.client.post('/api/v1/ingestion/uploads?filename=SYN-named-claims.xlsx',content=raw,
        headers={**api.headers['root'],'Content-Type':'application/octet-stream'})
    assert upload.status_code==200,upload.text
    response=api.client.post('/api/v1/claims/batches/from-document/'+upload.json()['document_id'],
        json={'display_name':'مطالبات أكتوبر','entry_date':'2026-11-01'},headers=api.headers['root'])
    assert response.status_code==201,response.text
    batch=response.json();assert batch['display_name']=='مطالبات أكتوبر' and batch['entry_date']=='2026-11-01'
    detail=api.client.get('/api/v1/claims/batches/'+batch['id'],headers=api.headers['root']).json()
    assert detail['display_name']=='مطالبات أكتوبر' and detail['entry_date']=='2026-11-01'
    listing=api.client.get('/api/v1/workspace/catalog/batches?limit=100',headers=api.headers['root']).json()
    row=next(item for item in listing['items'] if item['id']==batch['id'])
    assert row['display_name']=='مطالبات أكتوبر' and row['entry_date']=='2026-11-01'
    assert api.client.put('/api/v1/claims/batches/'+batch['id']+'/mapping',json=mapping(),headers=api.headers['root']).status_code==200
    assert api.client.post('/api/v1/claims/batches/'+batch['id']+'/resume',headers=api.headers['root']).status_code==200
    entries=api.client.get('/api/v1/claims/batches/'+batch['id']+'/entries',headers=api.headers['root']).json()['entries']
    assert entries and all(item['beneficiary_name'] and item['beneficiary_token'] and item['category_code']
        and item['provider_code'] and item['service_code'] for item in entries)

def test_wrong_template_headers_are_rejected_before_batch_creation(workspace):
    book=load_workbook(BytesIO(arabic_workbook()));book.active['A1']='رقم الفاتورة غير المعتمد'
    output=BytesIO();book.save(output)
    api=workspace;upload=api.client.post('/api/v1/ingestion/uploads?filename=SYN-wrong-template.xlsx',
        content=output.getvalue(),headers={**api.headers['root'],'Content-Type':'application/octet-stream'})
    assert upload.status_code==200,upload.text
    response=api.client.post('/api/v1/claims/batches/from-document/'+upload.json()['document_id'],headers=api.headers['root'])
    assert response.status_code==409
    detail=response.json()['detail']
    assert 'أعد تسمية العمود من «رقم الفاتورة غير المعتمد» إلى «رقم الفاتورة»' in detail
    assert 'يجب أن تكون الترويسات الـ12 مطابقة تماماً' in detail


def test_header_mismatch_message_names_the_exact_rename(workspace):
    book=load_workbook(BytesIO(arabic_workbook()));book.active['I1']='المطلوب قبل التحمل'
    output=BytesIO();book.save(output)
    api=workspace;upload=api.client.post('/api/v1/ingestion/uploads?filename=SYN-header-rename.xlsx',
        content=output.getvalue(),headers={**api.headers['root'],'Content-Type':'application/octet-stream'})
    assert upload.status_code==200,upload.text
    response=api.client.post('/api/v1/claims/batches/from-document/'+upload.json()['document_id'],headers=api.headers['root'])
    assert response.status_code==409
    assert response.json()['detail']==(
        'أعد تسمية العمود من «المطلوب قبل التحمل» إلى «المطلوب»، ثم ارفع الملف من جديد. '
        'يجب أن تكون الترويسات الـ12 مطابقة تماماً: رقم الفاتورة، الرقم التأميني، اسم المستفيد، '
        'الدرجة، مقدم الخدمة، الخدمة، تاريخ الخدمة، السعر، المطلوب، نسبة التحمل، إجمالي التحمل، الصافي.')


def test_two_files_are_disjoint_complete_and_idempotent(workspace):
    api=workspace;batch,doc,raw=imported(api);snapshots(api,batch)
    response=api.client.post('/api/v1/result-files/batches/'+batch,headers=api.headers['root'])
    assert response.status_code==201,response.text
    result=response.json();assert result['summary']['counts']=={'accepted':1,'rejected':1,'held':1,'review':1}
    assert result['summary']['total_lines']==4 and result['financially_final'] is False
    again=api.client.post('/api/v1/result-files/batches/'+batch,headers=api.headers['root'])
    assert again.json()['id']==result['id']
    download=api.client.get('/api/v1/result-files/'+result['id']+'/download',headers=api.headers['root'])
    assert download.status_code==200,download.text
    all_ids=[]
    with ZipFile(BytesIO(download.content)) as archive:
        assert set(archive.namelist())=={'accepted.xlsx','requires_review.xlsx'}
        for filename in archive.namelist():
            book=load_workbook(BytesIO(archive.read(filename)),read_only=False)
            rows=list(book.active.values)
            assert book.active.sheet_view.rightToLeft
            all_ids.extend(row[0] for row in rows[1:]);assert all(row[1]=='SYN-SAME-INVOICE' for row in rows[1:])
            assert all(cell!='SYN-BENEFICIARY' for row in rows for cell in row)
        assert len(list(load_workbook(BytesIO(archive.read('accepted.xlsx')),read_only=True).active.values))==2
        assert len(list(load_workbook(BytesIO(archive.read('requires_review.xlsx')),read_only=True).active.values))==4
    assert len(all_ids)==len(set(all_ids))==4
    assert api.client.get('/api/v1/result-files/'+result['id']+'/lines',headers=api.headers['other']).status_code==403
    with Session(api.engine) as db:
        source=db.get(SourceFile,db.get(IngestionDocument,doc).source_id)
        assert store().get(source.storage_key,source.sha256)==raw


def test_no_audit_snapshot_never_becomes_an_accepted_result(workspace):
    api=workspace;batch,_,_=imported(api)
    from tests.workspace_v2.invoice_fixtures import attach_invoices
    attach_invoices(api,batch)
    response=api.client.post('/api/v1/result-files/batches/'+batch,headers=api.headers['root'])
    assert response.status_code==201,response.text
    assert response.json()['summary']['counts']=={'accepted':0,'rejected':0,'held':4,'review':0}
    rows=api.client.get('/api/v1/result-files/'+response.json()['id']+'/lines',headers=api.headers['root']).json()['items']
    assert all(row['audited'] is False and row['reason']=='not_audited' for row in rows)


def test_partition_rejects_missing_duplicate_and_unknown_lines():
    row={'line_id':'SYN-1','currency':'LYD','claimed_net':'80','bucket':'accepted'}
    with pytest.raises(ReportBlocked):results.partition([row,row],['SYN-1'])
    with pytest.raises(ReportBlocked):results.partition([row],['SYN-1','SYN-2'])
    with pytest.raises(ReportBlocked):results.partition([{**row,'bucket':'unknown'}],['SYN-1'])


def test_multiple_audit_reasons_survive_in_red_output():
    claim=SimpleNamespace(id='SYN-LINE',claim_reference='SYN-INVOICE',provider_code='SYN-PROVIDER',
        service_code='SYN-SERVICE',service_date=date(2026,6,8),currency='LYD',claimed_amount=Decimal('80'),
        organization_id='SYN-ORG',extracted_record_id='SYN-SOURCE-ROW',source_locator={'row':2},source_values={})
    snapshot=SimpleNamespace(id='SYN-SNAPSHOT',organization_id='SYN-ORG',claim_id=claim.id,
        policy_id='SYN-POLICY',policy_fingerprint='SYN-FINGERPRINT',calculation={},verification={},
        findings={'agents':[{'agent':'invoice','status':'review','reason':'price mismatch'},
            {'agent':'duplicate','status':'review','reason':'possible duplicate'}]})
    line=results.project_line(claim,snapshot)
    from mcias.report_exports_g7 import render_result_partition
    workbook=load_workbook(BytesIO(render_result_partition({'rows':[line]},'requires_review')))
    reasons=workbook.active.cell(2,10).value
    assert line['bucket']=='review' and len(line['reasons'])==2
    assert 'price mismatch' in reasons and 'possible duplicate' in reasons


def test_outputs_require_completed_import_and_do_not_modify_batch(workspace):
    api=workspace;batch,_=upload_batch(api,arabic_workbook())
    assert api.client.post('/api/v1/result-files/batches/'+batch,headers=api.headers['root']).status_code==409
    with Session(api.engine) as db:assert db.get(ClaimBatch,batch).imported_count==0


def test_reference_checks_return_structured_incomplete_batch_detail(workspace):
    api=workspace;batch,_=upload_batch(api,arabic_workbook())
    response=api.client.post('/api/v1/claims/batches/'+batch+'/reference-checks',headers=api.headers['root'])
    assert response.status_code==409,response.text
    detail=response.json()['detail']
    assert detail['batch_id']==batch
    assert detail['status']=='staged'
    assert detail['reason']=='batch_not_imported'
    assert detail['imported_count']==0 and detail['record_count']==4
    assert detail['message']=='Complete the import before reference validation'


def test_workbook_header_mismatch_is_logged_without_leaking_source(workspace,caplog):
    api=workspace
    book=Workbook();sheet=book.active
    sheet.append(['عمود غير معتمد '+str(index) for index in range(12)])
    sheet.append(['SYN-VALUE']*12)
    raw=BytesIO();book.save(raw)
    upload=api.client.post('/api/v1/ingestion/uploads?filename=SYN-wrong-headers.xlsx',content=raw.getvalue(),
        headers={**api.headers['root'],'Content-Type':'application/octet-stream'})
    assert upload.status_code==200,upload.text
    with caplog.at_level(logging.WARNING,logger='mcias.claim_batches'):
        response=api.client.post('/api/v1/claims/batches/from-document/'+upload.json()['document_id'],
            headers=api.headers['root'])
    assert response.status_code==409,response.text
    assert 'claim_batch_header_mismatch' in caplog.text
    assert 'missing=' in caplog.text and 'unexpected=' in caplog.text
