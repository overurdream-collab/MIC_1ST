"""Super-admin organization administration without financial authority escalation."""
from datetime import date

from sqlalchemy import or_, select

from mcias.models import AuditLog, Group, Organization, User, UserGroup
from mcias.services import access_service, authorization_service as authority
from mcias.services import rbac_seed


def _require_technical(db, actor):
    if not authority.technical_admin(db, actor):
        raise PermissionError("MFA-enabled super-admin required")


def _organization(db, organization_id, for_update=False):
    query = select(Organization).where(Organization.id == str(organization_id))
    if for_update:
        query = query.with_for_update()
    organization = db.scalar(query)
    if organization is None:
        raise LookupError("organization not found")
    return organization


def _require_reason(reason):
    value = (reason or "").strip()
    if not value:
        raise ValueError("Administrative action reason required")
    return value


def _admin_groups(db, organization_id):
    return list(db.scalars(select(Group).where(
        Group.organization_id == str(organization_id),
        Group.management_scope == "institution_admin",
        *authority.live(Group, date.today()),
    ).order_by(Group.code, Group.id)))


def _admin_members(db, organization_id, groups):
    if not groups:
        return []
    today = date.today()
    return list(db.scalars(select(User).join(
        UserGroup, UserGroup.user_id == User.id,
    ).where(
        User.organization_id == str(organization_id),
        UserGroup.organization_id == str(organization_id),
        UserGroup.group_id.in_([group.id for group in groups]),
        User.active.is_(True),
        User.status == "open",
        User.effective_from <= today,
        or_(User.effective_to.is_(None), User.effective_to > today),
        *authority.live(UserGroup, today),
    ).distinct().order_by(User.username, User.id)))


def admin_permission_codes():
    template = next(group for group in rbac_seed.get_default_groups() if group["code"] == "admin")
    codes = set(template["permissions"])
    codes.update(access_service.registry_codes())
    codes.add(access_service.INTAKE_APPROVAL_CODE)
    return sorted(code for code in codes if authority.allowed_in_group(code, "institution_admin"))


def _summary(organization, groups, users):
    return {
        "id": organization.id,
        "name": organization.name,
        "admin_groups": [{
            "id": group.id, "code": group.code,
            "name_ar": group.name_ar, "name_en": group.name_en,
        } for group in groups],
        "admin_members": [{
            "id": user.id, "username": user.username,
            "full_name_ar": user.full_name_ar, "full_name_en": user.full_name_en,
        } for user in users],
        "permission_count": len(admin_permission_codes()),
    }


def state(db, actor, organization_id):
    _require_technical(db, actor)
    organization = _organization(db, organization_id)
    groups = _admin_groups(db, organization_id)
    users = _admin_members(db, organization_id, groups)
    return _summary(organization, groups, users)


def rename(db, actor, organization_id, name, reason):
    _require_technical(db, actor)
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Organization name is required")
    if len(clean_name) > 200:
        raise ValueError("Organization name must be at most 200 characters")
    clean_reason = _require_reason(reason)
    organization = _organization(db, organization_id, for_update=True)
    before = organization.name
    if before == clean_name:
        return {"id": organization.id, "name": organization.name, "changed": False}
    organization.name = clean_name
    db.flush()
    db.add(AuditLog(actor_id=actor.id, organization_id=organization.id,
        action="organization.rename", category="access_control",
        resource_type="organization", resource_id=organization.id,
        details={"before": before, "after": clean_name, "reason": clean_reason}))
    db.flush()
    return {"id": organization.id, "name": organization.name, "changed": True}


def grant_admin_permissions(db, actor, organization_id, reason):
    _require_technical(db, actor)
    if authority.authority_mode() != "super_admin":
        raise ValueError("Organization admin grants require super_admin authority mode")
    clean_reason = _require_reason(reason)
    organization = _organization(db, organization_id, for_update=True)
    groups = _admin_groups(db, organization_id)
    users = _admin_members(db, organization_id, groups)
    if not groups:
        raise ValueError("No active institution-admin group for this organization")
    if not users:
        raise ValueError("No active members in the institution-admin group")
    codes = set(admin_permission_codes())
    granted, unchanged, skipped = [], [], []
    for user in users:
        if authority.technical_identity(db, user):
            skipped.append(user.username)
            continue
        current = {permission.code for permission in access_service.permission_records(db, user)}
        wanted = current | codes
        if wanted == current:
            unchanged.append(user.username)
            continue
        access_service.replace_grants(db, actor, user.id, sorted(wanted),
            access_service.revision(db, user), clean_reason)
        granted.append({"id": user.id, "username": user.username,
            "added": len(wanted - current)})
    db.add(AuditLog(actor_id=actor.id, organization_id=organization.id,
        action="organization.admin_grant", category="access_control",
        resource_type="organization", resource_id=organization.id,
        details={"reason": clean_reason,
            "group_ids": [group.id for group in groups],
            "granted_user_ids": [item["id"] for item in granted],
            "unchanged_usernames": unchanged,
            "skipped_usernames": skipped,
            "permission_count": len(codes)}))
    db.flush()
    return {"id": organization.id, "name": organization.name,
        "groups": [group.code for group in groups],
        "granted": granted, "unchanged": unchanged, "skipped": skipped,
        "permission_count": len(codes)}
