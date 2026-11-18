"""Idempotent temporary reference proposals in the dedicated synthetic laptop DB.

No accounts receive functional grants. No source is overwritten and no financial
policy, contract approval, diagnosis or production acceptance is manufactured.
"""
from datetime import date
import os
from sqlalchemy import select
from mcias.models import Organization
from mcias.schemas.registry import SCHEMAS, RecordCreate
from mcias.services import registry_service as registry, feature_service as features


def seed_reference_inputs(db, actor):
    organization=db.get(Organization,actor.organization_id)
    if (os.getenv('MCIAS_ENVIRONMENT','development')!='development'
        or os.getenv('MCIAS_LAPTOP_TEST_MODE')!='synthetic_localhost'
        or not organization or not organization.name.startswith('SYN-')
        or not features.is_super_admin(db,actor)):
        raise ValueError('Explicit synthetic organization and MFA super-admin required')
    created=0;today=date.today()
    titles={
        'inputs.contracts':'عقد مقدم خدمة السلام', 'inputs.insurers':'شركة التأمين الوطنية',
        'inputs.regulations':'لائحة مالية عامة', 'inputs.exceptions':'استثناء موثق عام',
        'inputs.approvals':'موافقة سابقة عامة', 'inputs.caps':'سقف تغطية عام',
        'inputs.coverage':'تغطية عامة', 'beneficiaries.names':'خالد مصطفى',
        'beneficiaries.categories':'الفئة العامة', 'beneficiaries.cost_sharing':'تحمل الفئة العامة',
        'providers.hospitals':'مستشفى السلام', 'providers.clinics':'عيادة السلام',
        'providers.labs':'مختبر السلام', 'providers.radiology':'مركز أشعة السلام',
        'providers.pharmacies':'صيدلية السلام', 'providers.services':'استشارة عامة',
        'providers.prices':'تسعيرة استشارة عامة',
    }
    for feature,fields in SCHEMAS.items():
        if not features.check_feature(db,actor.organization_id,feature):
            features.grant_feature(db,actor.organization_id,feature,actor.id)
        values={key:'مرجع' for key in fields}
        values['record_code']='SYN-WORKSPACE-'+feature
        for key,value in {'provider_code':'مستشفى السلام','contract_number':'عقد-السلام-2026',
            'insurer_code':'شركة التأمين الوطنية','beneficiary_token':'1999999999','category_code':'فئة عامة',
            'service_code':'استشارة عامة','coding_system':'LOCAL','currency':'LYD','amount':'100.00',
            'percent':'20','scope':'نطاق عام','document_number':'لائحة-2026-009'}.items():
            if key in values:values[key]=value
        if feature.startswith('providers.') and feature.split('.')[1] in ('clinics','labs','radiology','pharmacies'):
            values['provider_code']={'clinics':'عيادة السلام','labs':'مختبر السلام',
                'radiology':'مركز أشعة السلام','pharmacies':'صيدلية السلام'}[feature.split('.')[1]]
        existing=registry.list_records(db,actor,feature,limit=10000)['items']
        if any(r['values']['record_code']==values['record_code'] for r in existing):continue
        registry.create_record(db,actor,feature,RecordCreate(title=titles[feature],effective_from=today,
            values=values,body='مقترح مرجعي تجريبي مؤقت؛ ليس اعتماداً مؤسسياً.'))
        created+=1
    from mcias.seed.linked_references import seed_linked_references
    linked=seed_linked_references(db,actor)
    return {'synthetic':True,'records_created':created,'linked_records_created':linked['records_created'],
        'financially_active':False,'permissions_granted':0}
