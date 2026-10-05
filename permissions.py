from enum import StrEnum

from sqlalchemy import select

from database import SessionLocal
from models import OrganizationMember


class OrganizationRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    PAYROLL = "payroll"
    MEMBER = "member"


class StoreRole(StrEnum):
    STORE_DISPLAY = "store_display"
    STORE_MANAGER = "store_manager"
    STAFF = "staff"


MANAGE_ORGANIZATION_ROLES = {
    OrganizationRole.OWNER,
    OrganizationRole.MANAGER,
}

MANAGE_EMPLOYEE_ROLES = {
    OrganizationRole.OWNER,
    OrganizationRole.MANAGER,
}

MANAGE_INTEGRATION_ROLES = {
    OrganizationRole.OWNER,
    OrganizationRole.MANAGER,
}

PAYROLL_REPORT_ROLES = {
    OrganizationRole.OWNER,
    OrganizationRole.MANAGER,
    OrganizationRole.PAYROLL,
}


def get_organization_membership(
    user_id: int,
    organization_id: int,
) -> OrganizationMember | None:
    with SessionLocal() as session:
        return session.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == organization_id,
            )
        ).scalar_one_or_none()


def get_organization_role(
    user_id: int,
    organization_id: int,
) -> str | None:
    membership = get_organization_membership(user_id, organization_id)
    return membership.role if membership else None


def role_can_manage_organization(role: str | None) -> bool:
    return role in MANAGE_ORGANIZATION_ROLES


def role_can_manage_employees(role: str | None) -> bool:
    return role in MANAGE_EMPLOYEE_ROLES


def role_can_manage_integrations(role: str | None) -> bool:
    return role in MANAGE_INTEGRATION_ROLES


def role_can_view_payroll_reports(role: str | None) -> bool:
    return role in PAYROLL_REPORT_ROLES


def role_can_view_dashboard_sales(role: str | None) -> bool:
    return role in {OrganizationRole.OWNER, OrganizationRole.MANAGER}
