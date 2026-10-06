"""Server-side store authority shared by claim setup and submission."""
from fastapi import HTTPException
from sqlalchemy import select
from models import User, OrganizationMember, OrganizationStore, StoreAssignment
from permissions import role_can_view_payroll_reports
from store_display_access import display_assignment, valid_display_session


def claim_stores(session, request, organization_id):
    user = session.get(User, request.session.get("user_id"))
    if user is None or not user.is_active:
        raise HTTPException(401, "Sign in first.")
    member = session.scalar(select(OrganizationMember).where(
        OrganizationMember.user_id == user.id,
        OrganizationMember.organization_id == organization_id))
    if member is None:
        raise HTTPException(403, "Business access is required.")
    stores = session.scalars(select(OrganizationStore).where(
        OrganizationStore.organization_id == organization_id,
        OrganizationStore.is_active.is_(True)).order_by(OrganizationStore.id)).all()
    if user.account_type == "store_display":
        display = display_assignment(session, user.id)
        if not display or not valid_display_session(request, *display):
            raise HTTPException(403, "Store Display access changed. Sign in again.")
        return [display[1]], False, True
    manager = role_can_view_payroll_reports(member.role)
    if not manager:
        ids = set(session.scalars(select(StoreAssignment.organization_store_id).where(
            StoreAssignment.organization_member_id == member.id,
            StoreAssignment.is_active.is_(True))))
        stores = [store for store in stores if store.id in ids]
    return stores, manager, False


def require_claim_store(session, request, organization_id, store_id):
    stores, manager, display = claim_stores(session, request, organization_id)
    store = next((s for s in stores if s.store_id.lower() == store_id.lower()), None)
    if store is None:
        raise HTTPException(403, "You cannot claim tips for this store. Ask a manager to check your store assignment.")
    if not store.timezone_name:
        raise HTTPException(409, "Configure this store's timezone first.")
    return store, manager, display


def latest_claim_end(session, organization_id, store_id, upper):
    from tip_submission_model import TipSubmission
    from store_time import as_utc
    # Existing records may have second-resolution boundaries. The setup rounds up.
    ends = session.scalars(select(TipSubmission.report_end_at).where(
        TipSubmission.organization_id == organization_id,
        TipSubmission.store_id == store_id,
        TipSubmission.processing_status != "rejected",
        TipSubmission.report_end_at <= upper)).all()
    return max((as_utc(value) for value in ends), default=None)
