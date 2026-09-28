import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from auth import (
    hash_password,
    password_needs_rehash,
    validate_password,
    verify_password,
)

from database import SessionLocal
from email_service import EmailDeliveryError, send_verification_email
from models import Employee, OrganizationInvitation, OrganizationMember, Organization, User
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_employees
from security_logging import log_security_event
from store_time import as_utc
from security_tokens import (
    EMAIL_VERIFICATION,
    EMAIL_VERIFICATION_LIFETIME,
    create_security_token,
)

import logging


router = APIRouter()

templates = Jinja2Templates(directory="templates")


def get_management_access(request: Request):
    """
    Return the current organization ID if the logged-in user
    has owner or manager access.
    """
    user_id = request.session.get("user_id")

    if not user_id:
        return None

    organization_id = get_current_organization_id(request)

    if organization_id is None:
        return None

    role = get_organization_role(user_id, organization_id)
    if not role_can_manage_employees(role):
        return None

    return organization_id


@router.get("/employees", response_class=HTMLResponse)
async def employees(
    request: Request,
    status: str = "active",
):
    user_id = request.session.get("user_id")

    if not user_id:
        return RedirectResponse(
            "/login",
            status_code=303,
        )

    organization_id = get_management_access(request)

    if organization_id is None:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Employee Access Denied",
                "paragraphs": [
                    "Only organization owners and managers can manage employees."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    # Only allow the two supported views.
    if status not in ("active", "all"):
        status = "active"

    with SessionLocal() as session:
        query = (
            select(Employee)
            .where(Employee.organization_id == organization_id)
            .order_by(Employee.last_name, Employee.first_name)
        )

        if status == "active":
            query = query.where(Employee.is_active.is_(True))

        employees = session.execute(query).scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="employees.html",
        context={
            "employees": employees,
            "status": status,
        },
    )


@router.get("/employees/new", response_class=HTMLResponse)
async def new_employee(request: Request):
    organization_id = get_management_access(request)

    if organization_id is None:
        if not request.session.get("user_id"):
            return RedirectResponse(
                "/login",
                status_code=303,
            )

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Access Denied",
                "paragraphs": [
                    "You do not have permission to add employees."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    return templates.TemplateResponse(
        request=request,
        name="employee_form.html",
        context={
            "first_name": "",
            "last_name": "",
            "email": "",
            "error": None,
        },
    )


@router.post("/employees")
async def create_employee(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
):
    organization_id = get_management_access(request)

    if organization_id is None:
        if not request.session.get("user_id"):
            return RedirectResponse(
                "/login",
                status_code=303,
            )

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Access Denied",
                "paragraphs": [
                    "You do not have permission to add employees."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip().lower()

    if not first_name or not last_name or not email:
        return templates.TemplateResponse(
            request=request,
            name="employee_form.html",
            context={
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "error": "First name, last name, and email are required.",
            },
            status_code=400,
        )

    with SessionLocal() as session:
        existing_employee = session.execute(
            select(Employee).where(
                Employee.organization_id == organization_id,
                Employee.email == email,
            )
        ).scalar_one_or_none()

        if existing_employee:
            return templates.TemplateResponse(
                request=request,
                name="employee_form.html",
                context={
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "error": (
                        "An employee with that email address "
                        "already exists."
                    ),
                },
                status_code=400,
            )

        employee = Employee(
            organization_id=organization_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            role="employee",
        )

        session.add(employee)
        session.commit()

    return RedirectResponse(
        "/employees",
        status_code=303,
    )


@router.get("/employees/{employee_id}/edit", response_class=HTMLResponse)
async def edit_employee(
    request: Request,
    employee_id: int,
):
    organization_id = get_management_access(request)

    if organization_id is None:
        if not request.session.get("user_id"):
            return RedirectResponse("/login", status_code=303)

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Access Denied",
                "paragraphs": [
                    "You do not have permission to edit employees."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    with SessionLocal() as session:
        employee = session.execute(
            select(Employee).where(
                Employee.id == employee_id,
                Employee.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if employee is None:
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Employee Not Found",
                    "paragraphs": [
                        "The requested employee could not be found."
                    ],
                    "show_dashboard_link": True,
                },
                status_code=404,
            )

        return templates.TemplateResponse(
            request=request,
            name="employee_edit.html",
            context={
                "employee": employee,
                "error": None,
            },
        )

@router.post("/employees/{employee_id}")
async def update_employee(
    request: Request,
    employee_id: int,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    is_active: str | None = Form(None),
):
    organization_id = get_management_access(request)

    if organization_id is None:
        if not request.session.get("user_id"):
            return RedirectResponse("/login", status_code=303)

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Access Denied",
                "paragraphs": [
                    "You do not have permission to edit employees."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip().lower()
    active = is_active == "on"

    with SessionLocal() as session:
        employee = session.execute(
            select(Employee).where(
                Employee.id == employee_id,
                Employee.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if employee is None:
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Employee Not Found",
                    "paragraphs": [
                        "The requested employee could not be found."
                    ],
                    "show_dashboard_link": True,
                },
                status_code=404,
            )

        if not first_name or not last_name or not email:
            return templates.TemplateResponse(
                request=request,
                name="employee_edit.html",
                context={
                    "employee": employee,
                    "error": (
                        "First name, last name, and email are required."
                    ),
                },
                status_code=400,
            )

        duplicate = session.execute(
            select(Employee).where(
                Employee.organization_id == organization_id,
                Employee.email == email,
                Employee.id != employee_id,
            )
        ).scalar_one_or_none()

        if duplicate:
            return templates.TemplateResponse(
                request=request,
                name="employee_edit.html",
                context={
                    "employee": employee,
                    "error": (
                        "Another employee with that email address "
                        "already exists."
                    ),
                },
                status_code=400,
            )

        employee.first_name = first_name
        employee.last_name = last_name
        employee.email = email
        employee.is_active = active

        session.commit()

    return RedirectResponse(
        "/employees",
        status_code=303,
    )    

@router.post("/employees/{employee_id}/invite")
async def invite_employee(
    request: Request,
    employee_id: int,
):
    organization_id = get_management_access(request)

    if organization_id is None:
        if not request.session.get("user_id"):
            return RedirectResponse(
                "/login",
                status_code=303,
            )

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Access Denied",
                "paragraphs": [
                    "You do not have permission to invite employees."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    user_id = request.session.get("user_id")

    with SessionLocal() as session:
        # Find the employee, but ONLY inside the current organization.
        employee = session.execute(
            select(Employee).where(
                Employee.id == employee_id,
                Employee.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if employee is None:
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Employee Not Found",
                    "paragraphs": [
                        "The requested employee could not be found."
                    ],
                    "show_dashboard_link": True,
                },
                status_code=404,
            )

        # Inactive employees cannot be invited.
        if not employee.is_active:
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Employee Inactive",
                    "paragraphs": [
                        (
                            f"{employee.first_name} {employee.last_name} "
                            "is currently inactive."
                        ),
                        "Activate the employee before sending an invitation.",
                    ],
                    "show_dashboard_link": True,
                },
                status_code=400,
            )

        # An employee already linked to a Food Truck Works user does not
        # need another account invitation.
        if employee.user_id is not None:
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Employee Already Has Access",
                    "paragraphs": [
                        (
                            f"{employee.first_name} {employee.last_name} "
                            "is already linked to a Food Truck Works account."
                        ),
                    ],
                    "show_dashboard_link": True,
                },
                status_code=400,
            )

        # Expire any previous unused invitations for this employee.
        now = datetime.now(timezone.utc)

        previous_invitations = session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.employee_id == employee.id,
                OrganizationInvitation.organization_id == organization_id,
                OrganizationInvitation.accepted_at.is_(None),
                OrganizationInvitation.expires_at > now,
            )
        ).scalars().all()

        for invitation in previous_invitations:
            invitation.expires_at = now

        # Generate the raw token.
        raw_token = secrets.token_urlsafe(32)

        # Store ONLY the hash in the database.
        token_hash = hashlib.sha256(
            raw_token.encode("utf-8")
        ).hexdigest()

        expires_at = now + timedelta(days=7)

        invitation = OrganizationInvitation(
            organization_id=organization_id,
            employee_id=employee.id,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by_user_id=user_id,
        )

        session.add(invitation)
        session.commit()
        session.refresh(invitation)

        # Build the invitation URL from the current request.
        base_url = str(request.base_url).rstrip("/")
        invitation_url = f"{base_url}/invite/{raw_token}"

        return templates.TemplateResponse(
            request=request,
            name="invitation_created.html",
            context={
                "employee": employee,
                "invitation_url": invitation_url,
                "expires_at": expires_at,
            },
        )

@router.get("/invite/{token}", response_class=HTMLResponse)
async def validate_invitation(
    request: Request,
    token: str,
):
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        result = session.execute(
            select(
                OrganizationInvitation,
                Employee,
                Organization,
            )
            .join(
                Employee,
                Employee.id == OrganizationInvitation.employee_id,
            )
            .join(
                Organization,
                Organization.id == OrganizationInvitation.organization_id,
            )
            .where(
                OrganizationInvitation.token_hash == token_hash
            )
        ).first()

        if result is None:
            log_security_event(
                request,
                "invitation_acceptance",
                "denied",
                level=logging.WARNING,
                reason="invalid_invitation",
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invalid Invitation",
                    "message": (
                        "This invitation link is not valid."
                    ),
                },
                status_code=404,
            )

        invitation, employee, organization = result

        # Defense-in-depth: make sure the invitation itself is
        # internally consistent with the employee and organization.
        if employee.organization_id != invitation.organization_id:
            log_security_event(
                request,
                "invitation_acceptance",
                "denied",
                level=logging.ERROR,
                organization_id=invitation.organization_id,
                reason="organization_mismatch",
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invalid Invitation",
                    "message": (
                        "This invitation link is not valid."
                    ),
                },
                status_code=404,
            )

        # Invitations are single-use.
        if invitation.accepted_at is not None:
            log_security_event(
                request,
                "invitation_acceptance",
                "denied",
                level=logging.WARNING,
                organization_id=organization.id,
                reason="invitation_already_used",
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Already Used",
                    "message": (
                        "This invitation has already been accepted "
                        "and can no longer be used."
                    ),
                },
                status_code=410,
            )

        # Invitations expire.
        if as_utc(invitation.expires_at) <= now:
            log_security_event(
                request,
                "invitation_acceptance",
                "denied",
                level=logging.WARNING,
                organization_id=organization.id,
                reason="invitation_expired",
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Expired",
                    "message": (
                        "This invitation has expired. "
                        "Please contact your manager for a new invitation."
                    ),
                },
                status_code=410,
            )

        # The employee must still be active.
        if not employee.is_active:
            log_security_event(
                request,
                "invitation_acceptance",
                "denied",
                level=logging.WARNING,
                organization_id=organization.id,
                reason="employee_inactive",
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Unavailable",
                    "message": (
                        "This invitation is no longer available. "
                        "Please contact your manager."
                    ),
                },
                status_code=410,
            )

        return templates.TemplateResponse(
            request=request,
            name="invitation.html",
            context={
                "employee": employee,
                "organization": organization,
                "invitation": invitation,
                "token": token,
            },
        )   


@router.get("/invite/{token}/create-account", response_class=HTMLResponse)
async def create_account_page(
    request: Request,
    token: str,
):
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        result = session.execute(
            select(
                OrganizationInvitation,
                Employee,
                Organization,
            )
            .join(
                Employee,
                Employee.id == OrganizationInvitation.employee_id,
            )
            .join(
                Organization,
                Organization.id == OrganizationInvitation.organization_id,
            )
            .where(
                OrganizationInvitation.token_hash == token_hash
            )
        ).first()

        if result is None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invalid Invitation",
                    "message": "This invitation link is not valid.",
                },
                status_code=404,
            )

        invitation, employee, organization = result

        if employee.organization_id != invitation.organization_id:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invalid Invitation",
                    "message": "This invitation link is not valid.",
                },
                status_code=404,
            )

        if invitation.accepted_at is not None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Already Used",
                    "message": (
                        "This invitation has already been accepted "
                        "and can no longer be used."
                    ),
                },
                status_code=410,
            )

        if as_utc(invitation.expires_at) <= now:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Expired",
                    "message": (
                        "This invitation has expired. "
                        "Please contact your manager for a new invitation."
                    ),
                },
                status_code=410,
            )

        if not employee.is_active:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Unavailable",
                    "message": (
                        "This invitation is no longer available. "
                        "Please contact your manager."
                    ),
                },
                status_code=410,
            )

        # The invitation email is authoritative.
        email = employee.email.strip().lower()

        existing_user = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        if existing_user is not None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_existing_account.html",
                context={
                    "employee": employee,
                    "organization": organization,
                    "token": token,
                    "error": None,
                },
            )

        return templates.TemplateResponse(
            request=request,
            name="invitation_create_account.html",
            context={
                "employee": employee,
                "organization": organization,
                "invitation": invitation,
                "token": token,
            },
        )     


@router.post("/invite/{token}/create-account")
async def create_account(
    request: Request,
    token: str,
    password: str = Form(...),
    confirm_password: str = Form(...),
):

    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        result = session.execute(
            select(
                OrganizationInvitation,
                Employee,
                Organization,
            )
            .join(
                Employee,
                Employee.id == OrganizationInvitation.employee_id,
            )
            .join(
                Organization,
                Organization.id == OrganizationInvitation.organization_id,
            )
            .where(
                OrganizationInvitation.token_hash == token_hash
            )
        ).first()

        if result is None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invalid Invitation",
                    "message": "This invitation link is not valid.",
                },
                status_code=404,
            )

        invitation, employee, organization = result

        if employee.organization_id != invitation.organization_id:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invalid Invitation",
                    "message": "This invitation link is not valid.",
                },
                status_code=404,
            )

        if invitation.accepted_at is not None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Already Used",
                    "message": (
                        "This invitation has already been accepted "
                        "and can no longer be used."
                    ),
                },
                status_code=410,
            )

        if as_utc(invitation.expires_at) <= now:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Expired",
                    "message": (
                        "This invitation has expired. "
                        "Please contact your manager for a new invitation."
                    ),
                },
                status_code=410,
            )

        if not employee.is_active:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Unavailable",
                    "message": (
                        "This invitation is no longer available. "
                        "Please contact your manager."
                    ),
                },
                status_code=410,
            )

        if password != confirm_password:
            return templates.TemplateResponse(
                request=request,
                name="invitation_create_account.html",
                context={
                    "organization": organization,
                    "employee": employee,
                    "invitation": invitation,
                    "token": token,
                    "error": "Passwords do not match.",
                },
                status_code=400,
            )

        password_error = validate_password(password)
        if password_error:
            return templates.TemplateResponse(
                request=request,
                name="invitation_create_account.html",
                context={
                    "organization": organization,
                    "employee": employee,
                    "invitation": invitation,
                    "token": token,
                    "error": password_error,
                },
                status_code=400,
            )

        if employee.user_id is not None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Unavailable",
                    "message": (
                        "This employee is already linked to a Food Truck Works "
                        "account."
                    ),
                },
                status_code=410,
            )

        email = employee.email.strip().lower()

        existing_user = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        if existing_user is not None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_existing_account.html",
                context={
                    "employee": employee,
                    "organization": organization,
                    "token": token,
                    "error": None,
                },
            )

        user = User(
            first_name=employee.first_name,
            last_name=employee.last_name,
            email=email,
            password_hash=hash_password(password),
            is_active=True,
        )

        session.add(user)
        session.flush()
        
        employee.user_id = user.id

        membership = OrganizationMember(
            organization_id=organization.id,
            user_id=user.id,
            role="member",
        )

        session.add(membership)

        invitation.accepted_at = now

        raw_verification_token = create_security_token(
            session,
            user.id,
            EMAIL_VERIFICATION,
            EMAIL_VERIFICATION_LIFETIME,
        )
        try:
            send_verification_email(user.email, raw_verification_token)
            session.commit()
        except EmailDeliveryError:
            session.rollback()
            logger.exception("Invitation verification email delivery failed")
            log_security_event(
                request,
                "invitation_acceptance",
                "failed",
                level=logging.ERROR,
                organization_id=organization.id,
                reason="email_delivery_failed",
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_create_account.html",
                context={
                    "organization": organization,
                    "employee": employee,
                    "invitation": invitation,
                    "token": token,
                    "error": (
                        "We could not send a verification email. "
                        "Please try again shortly."
                    ),
                },
                status_code=503,
            )

        log_security_event(
            request,
            "invitation_acceptance",
            "succeeded",
            user_id=user.id,
            organization_id=organization.id,
            account_type="new",
        )

        request.session.clear()

    return templates.TemplateResponse(
        request=request,
        name="message.html",
        context={
            "title": "Check Your Email",
            "paragraphs": [
                "We sent a verification link to your email address. "
                "Verify your email before signing in."
            ],
            "login_link": True,
        },
    )    

@router.post("/invite/{token}/existing-account")
async def accept_invitation_with_existing_account(
    request: Request,
    token: str,
    password: str = Form(...),
):
    """Accept an employee invitation using the account for the invited email."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        result = session.execute(
            select(OrganizationInvitation, Employee, Organization)
            .join(Employee, Employee.id == OrganizationInvitation.employee_id)
            .join(Organization, Organization.id == OrganizationInvitation.organization_id)
            .where(OrganizationInvitation.token_hash == token_hash)
        ).first()

        if result is None:
            log_security_event(
                request,
                "invitation_acceptance",
                "denied",
                level=logging.WARNING,
                reason="invalid_invitation",
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={"title": "Invalid Invitation", "message": "This invitation link is not valid."},
                status_code=404,
            )

        invitation, employee, organization = result

        if employee.organization_id != invitation.organization_id:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={"title": "Invalid Invitation", "message": "This invitation link is not valid."},
                status_code=404,
            )

        if invitation.accepted_at is not None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Already Used",
                    "message": "This invitation has already been accepted and can no longer be used.",
                },
                status_code=410,
            )

        if as_utc(invitation.expires_at) <= now or not employee.is_active:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Unavailable",
                    "message": "This invitation is no longer available. Please contact your manager.",
                },
                status_code=410,
            )

        if employee.user_id is not None:
            return templates.TemplateResponse(
                request=request,
                name="invitation_invalid.html",
                context={
                    "title": "Invitation Unavailable",
                    "message": "This employee is already linked to a Food Truck Works account.",
                },
                status_code=410,
            )

        email = employee.email.strip().lower()
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()

        password_valid = verify_password(
            password,
            user.password_hash if user else None,
        )

        if user is None or not user.is_active or not password_valid:
            if user is None:
                failure_reason = "account_not_found"
            elif not user.is_active:
                failure_reason = "inactive_account"
            else:
                failure_reason = "invalid_password"

            log_security_event(
                request,
                "invitation_authentication",
                "failed",
                level=logging.WARNING,
                user_id=user.id if user else None,
                organization_id=organization.id,
                reason=failure_reason,
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_existing_account.html",
                context={
                    "employee": employee,
                    "organization": organization,
                    "token": token,
                    "error": "Unable to verify the account credentials.",
                },
                status_code=401,
            )

        if user.email_verified_at is None:
            log_security_event(
                request,
                "invitation_authentication",
                "denied",
                level=logging.WARNING,
                user_id=user.id,
                organization_id=organization.id,
                reason="email_not_verified",
            )
            return templates.TemplateResponse(
                request=request,
                name="invitation_existing_account.html",
                context={
                    "employee": employee,
                    "organization": organization,
                    "token": token,
                    "error": "Verify your email before accepting this invitation.",
                },
                status_code=403,
            )

        membership = session.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization.id,
                OrganizationMember.user_id == user.id,
            )
        ).scalar_one_or_none()

        # Preserve an existing organization role. Only create a membership
        # when this user does not already belong to the organization.
        if membership is None:
            session.add(
                OrganizationMember(
                    organization_id=organization.id,
                    user_id=user.id,
                    role="member",
                )
            )

        employee.user_id = user.id
        invitation.accepted_at = now

        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        # Fill legacy account names from the employee record without
        # overwriting an identity the user has already established.
        if not user.first_name:
            user.first_name = employee.first_name
        if not user.last_name:
            user.last_name = employee.last_name

        session.commit()

        log_security_event(
            request,
            "invitation_acceptance",
            "succeeded",
            user_id=user.id,
            organization_id=organization.id,
            account_type="existing",
        )

        request.session.clear()
        request.session["user_id"] = user.id
        request.session["organization_id"] = organization.id

    return RedirectResponse("/dashboard", status_code=303)
