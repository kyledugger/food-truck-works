"""Manager provisioning for dedicated single-store display logins."""
import secrets
import re
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from auth import hash_password, validate_password
from database import SessionLocal
from models import User, Organization, OrganizationMember, OrganizationStore, StoreAssignment
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_organization
from security_logging import log_security_event
from store_time import utc_now

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def manager_org(request):
    user_id = request.session.get("user_id")
    organization_id = get_current_organization_id(request) if user_id else None
    with SessionLocal() as session:
        user = session.get(User, user_id) if user_id else None
        if not user or not user.is_active or user.account_type != "person" or not organization_id or not role_can_manage_organization(get_organization_role(user_id, organization_id)):
            raise HTTPException(403, "Owner or manager permission is required.")
    return organization_id


def check_csrf(request, token):
    expected = request.session.get("store_display_csrf")
    if not expected or len(token) > 128 or not secrets.compare_digest(expected.encode(), token.encode()):
        raise HTTPException(403, "Refresh Store Displays and try again.")


@router.get("/settings/store-displays")
def settings(request: Request):
    organization_id = manager_org(request)
    token = request.session.setdefault("store_display_csrf", secrets.token_urlsafe(32))
    with SessionLocal() as session:
        organization = session.scalar(select(Organization).where(Organization.id == organization_id).with_for_update())
        if not organization.display_login_code:
            organization.display_login_code = f"ftw-{organization.id}"
            session.commit()
        login_code = organization.display_login_code
        stores = session.scalars(select(OrganizationStore).where(OrganizationStore.organization_id == organization_id).order_by(OrganizationStore.id)).all()
        accounts = session.execute(select(StoreAssignment, User, OrganizationStore, OrganizationMember)
            .join(OrganizationMember, StoreAssignment.organization_member_id == OrganizationMember.id)
            .join(User, OrganizationMember.user_id == User.id)
            .join(OrganizationStore, StoreAssignment.organization_store_id == OrganizationStore.id)
            .where(OrganizationMember.organization_id == organization_id, OrganizationStore.organization_id == organization_id,
                   User.account_type == "store_display", StoreAssignment.role == "store_display")
            .order_by(StoreAssignment.id)).all()
        return templates.TemplateResponse(request=request, name="store_displays.html",
            context={"stores": stores, "accounts": accounts, "csrf_token": token, "login_code": login_code}, headers={"Cache-Control": "no-store"})


@router.post("/settings/store-displays")
def create(request: Request, csrf_token: str = Form(...), store_id: int = Form(...),
           label: str = Form(...), username: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    organization_id = manager_org(request)
    check_csrf(request, csrf_token)
    label = label.strip()
    if not label or len(label) > 100:
        raise HTTPException(400, "Enter a display name up to 100 characters.")
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,49}", username):
        raise HTTPException(400, "Use 3–50 letters, numbers, underscores or hyphens for the username.")
    if password != confirm_password or validate_password(password):
        raise HTTPException(400, validate_password(password) or "Passwords must match.")
    with SessionLocal() as session:
        store = session.scalar(select(OrganizationStore).where(OrganizationStore.id == store_id,
            OrganizationStore.organization_id == organization_id, OrganizationStore.is_active.is_(True)))
        if not store:
            raise HTTPException(404, "Choose an active store in this organization.")
        if session.scalar(select(OrganizationMember.id).where(OrganizationMember.organization_id == organization_id, OrganizationMember.display_username == username)):
            raise HTTPException(400, "That display username is already in use in this organization.")
        # Managers provision credentials directly; these are not self-service accounts.
        user = User(email=None, first_name=label, password_hash=hash_password(password), account_type="store_display")
        session.add(user)
        try:
            session.flush()
            member = OrganizationMember(user_id=user.id, organization_id=organization_id, role="member", display_username=username)
            session.add(member);session.flush()
            session.add(StoreAssignment(organization_member_id=member.id, organization_store_id=store.id, role="store_display"))
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "That display username is already in use.")
        log_security_event(request, "store_display", "created", organization_id=organization_id, display_user_id=user.id, store_id=store.id)
    return RedirectResponse("/settings/store-displays", status_code=303)


@router.post("/settings/store-displays/{assignment_id}")
def update(request: Request, assignment_id: int, csrf_token: str = Form(...), store_id: int = Form(...),
           is_active: bool = Form(False), password: str = Form(""), confirm_password: str = Form("")):
    organization_id = manager_org(request)
    check_csrf(request, csrf_token)
    if password and (password != confirm_password or validate_password(password)):
        raise HTTPException(400, validate_password(password) or "Passwords must match.")
    if not password and confirm_password:
        raise HTTPException(400, "Enter both password fields.")
    with SessionLocal() as session:
        result = session.execute(select(StoreAssignment, User).join(OrganizationMember,
            StoreAssignment.organization_member_id == OrganizationMember.id).join(User, OrganizationMember.user_id == User.id)
            .where(StoreAssignment.id == assignment_id, OrganizationMember.organization_id == organization_id,
                   User.account_type == "store_display", StoreAssignment.role == "store_display").with_for_update()).first()
        if not result:
            raise HTTPException(404, "Display account not found.")
        assignment, user = result
        store = session.scalar(select(OrganizationStore).where(OrganizationStore.id == store_id, OrganizationStore.organization_id == organization_id))
        if not store or (is_active and not store.is_active):
            raise HTTPException(400, "Enabled displays need an active store in this organization.")
        assignment.organization_store_id = store.id
        assignment.is_active = is_active
        assignment.session_version += 1
        user.is_active = is_active
        if password:
            user.password_hash = hash_password(password)
        session.commit()
        log_security_event(request, "store_display", "updated", organization_id=organization_id,
            display_user_id=user.id, store_id=store.id, enabled=is_active, password_changed=bool(password))
    return RedirectResponse("/settings/store-displays", status_code=303)
