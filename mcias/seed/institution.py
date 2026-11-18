"""Explicit laptop-only institutional admin fixture, never customer provisioning."""
import base64, os, secrets
from datetime import date
from sqlalchemy import select
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from mcias.models import AuditLog, Group, GroupPermission, Permission, User, UserGroup, MFAFactor
from mcias.seed.loader import identifier
from mcias.security import hash_password
from mcias.services import authorization_service as authority, feature_service as features
from mcias.services.rbac_seed import seed_organization, get_default_groups

USERNAME = 'SYN-laptop-institution-admin'


def ensure_admin(db, org_id, accounts, env, operational_permissions):
    from mcias.seed.laptop import guard
    guard(env)
    if db.get_bind().dialect.name != 'postgresql' or db.get_bind().url.database != 'mcias_laptop_e2e':
        raise ValueError('Dedicated laptop PostgreSQL database required')
    if any(account['username'] == USERNAME for account in accounts):
        # Repeated setup must not recreate accounts or restore revoked grants.
        ensure_intake_approval(db, org_id, accounts, env)
        return False
    if db.get(User, identifier('laptop-institution-admin')) or db.scalar(select(User.id).where(User.username == USERNAME)):
        raise ValueError('Institution admin exists without private credentials; operator recovery required')
    root = db.get(User, next(account['id'] for account in accounts if account['username'] == 'SYN-laptop-admin'))
    seed_organization(db, org_id, root.id)
    password = secrets.token_urlsafe(24)
    user = User(id=identifier('laptop-institution-admin'), organization_id=org_id, username=USERNAME,
        full_name_ar='مدير الجهة — حساب اختبار', full_name_en='Institution admin — test account',
        password_hash=hash_password(password))
    db.add(user); db.flush()
    secret = secrets.token_bytes(20); nonce = os.urandom(12)
    cipher = AESGCM(bytes.fromhex(env['MCIAS_MFA_ENCRYPTION_KEY'])).encrypt(nonce, secret, user.id.encode())
    db.add(MFAFactor(user_id=user.id, encrypted_secret=nonce + cipher, enabled=True))
    group = Group(id=identifier('laptop-institution-admin-group'), organization_id=org_id,
        code='laptop_institution_admin', name_ar='مدير الجهة', name_en='Institution administrator',
        management_scope='institution_admin', effective_from=date.today(), created_by=root.id)
    db.add(group); db.flush()
    template = next(g for g in get_default_groups() if g['code'] == 'admin')
    codes = set(template['permissions']) | {c for c in operational_permissions if authority.allowed_in_group(c, 'institution_admin')}
    from mcias.schemas.registry import SCHEMAS
    codes |= {f"registry.{feature}.read" for feature in SCHEMAS}
    codes.add('reference.intake.approve')
    for code in sorted(codes):
        permission = db.scalar(select(Permission).where(Permission.code == code))
        if permission is None:
            permission = Permission(code=code); db.add(permission); db.flush()
        db.add(GroupPermission(organization_id=org_id, group_id=group.id, permission_id=permission.id,
            effective_from=date.today(), granted_by=root.id))
    db.add(UserGroup(organization_id=org_id, user_id=user.id, group_id=group.id,
        effective_from=date.today(), added_by=root.id)); db.flush()
    # The laptop runs in super_admin authority, where group grants are frozen
    # until explicitly activated. Activate this bootstrap account's approved
    # group permissions once; later revocations are never restored by setup.
    from mcias.services import access_service
    access_service.replace_grants(db, root.id, user.id, sorted(codes),
        access_service.revision(db, user.id), 'Synthetic institution admin bootstrap')
    # Initial test entitlements only. Later revocation is never undone by setup.
    for feature in features.list_features(db):
        if feature.is_container or feature.code.startswith('system.'):
            continue
        from mcias.models_features import OrganizationFeature
        if db.scalar(select(OrganizationFeature.id).where(OrganizationFeature.organization_id == org_id,
                OrganizationFeature.feature_code == feature.code, OrganizationFeature.status == 'open')) is None:
            features.grant_feature(db, org_id, feature.code, root.id)
    accounts.append({'id':user.id, 'username':user.username, 'password':password,
        'totp_secret_base32':base64.b32encode(secret).decode().rstrip('=')})
    db.add(AuditLog(actor_id=root.id, organization_id=org_id, action='synthetic_institution_admin_bootstrap',
        resource_type='user', resource_id=user.id, details={'synthetic':True, 'group_id':group.id}))
    return True


def ensure_intake_approval(db, org_id, accounts, env):
    """Add the non-financial intake approval grant once to an existing laptop fixture."""
    from mcias.seed.laptop import guard
    guard(env)
    from mcias.services import access_service
    if not any(account['username'] == USERNAME for account in accounts):
        return False
    user = db.scalar(select(User).where(User.organization_id == org_id, User.username == USERNAME))
    root_id = next((account['id'] for account in accounts if account['username'] == 'SYN-laptop-admin'), None)
    root = db.get(User, root_id) if root_id else None
    if not user or not root:
        return False
    from mcias.schemas.registry import SCHEMAS
    code = access_service.INTAKE_APPROVAL_CODE
    wanted = {code} | {f"registry.{feature}.read" for feature in SCHEMAS}
    revoked_codes = set(db.scalars(select(AuditLog.details['code'].as_string()).where(
        AuditLog.organization_id == org_id, AuditLog.action == 'access.revoke',
        AuditLog.details['user_id'].as_string() == user.id)))
    group = db.scalar(select(Group).where(Group.organization_id == org_id, Group.code == 'laptop_institution_admin'))
    if group is not None:
        for wanted_code in sorted(wanted - revoked_codes):
            permission = db.scalar(select(Permission).where(Permission.code == wanted_code))
            if permission is None:
                permission = Permission(code=wanted_code, category='reference_intake',
                    description_ar='اعتماد المدخلات المرجعية بعد مراجعة مؤسسية' if wanted_code == code else wanted_code,
                    is_system=True)
                db.add(permission); db.flush()
            if db.scalar(select(GroupPermission.id).where(GroupPermission.organization_id == org_id,
                    GroupPermission.group_id == group.id, GroupPermission.permission_id == permission.id)) is None:
                db.add(GroupPermission(organization_id=org_id, group_id=group.id, permission_id=permission.id,
                    effective_from=date.today(), granted_by=root_id))
        db.flush()
    current = {p.code for p in access_service.permission_records(db, user)}
    missing = (wanted - current) - revoked_codes
    if not missing:
        return False
    access_service.replace_grants(db, root, user.id, sorted(current | missing),
        access_service.revision(db, user), 'Reference intake approval bootstrap')
    return True
