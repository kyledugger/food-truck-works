"""Fail-closed access for dedicated display accounts, including existing sessions."""
from sqlalchemy import select
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from database import SessionLocal
from models import User, OrganizationMember, OrganizationStore, StoreAssignment


def display_assignment(session, user_id):
    user = session.get(User, user_id)
    if not user or not user.is_active or user.account_type != "store_display":
        return None
    members = session.scalars(select(OrganizationMember).where(OrganizationMember.user_id == user_id)).all()
    if len(members) != 1 or members[0].role != "member" or not members[0].display_username:
        return None
    assignments = session.scalars(select(StoreAssignment).where(StoreAssignment.organization_member_id == members[0].id)).all()
    if len(assignments) != 1:
        return None
    assignment = assignments[0]
    store = session.get(OrganizationStore, assignment.organization_store_id)
    if assignment.role != "store_display" or not assignment.is_active or not store or not store.is_active or store.organization_id != members[0].organization_id:
        return None
    return assignment, store


def valid_display_session(request, assignment, store):
    return (request.session.get("organization_id") == store.organization_id
            and request.session.get("store_assignment_id") == assignment.id
            and request.session.get("store_session_version") == assignment.session_version)


class StoreDisplayMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        user_id = request.session.get("user_id")
        # Login/logout and static assets stay available for switching accounts.
        if not user_id or request.url.path in {"/login", "/login/store-display", "/logout"} or request.url.path.startswith("/static/"):
            return await call_next(request)
        with SessionLocal() as session:
            user = session.get(User, user_id)
            is_display = bool(user and user.account_type == "store_display")
            result = display_assignment(session, user_id) if is_display else None
            valid = bool(result and valid_display_session(request, *result))
            store_id = result[1].id if result else None
        # Release the database connection before running another request handler.
        if not is_display:
            return await call_next(request)
        if not valid:
            request.session.clear()
            if request.url.path == "/dashboard/data":
                return JSONResponse({"detail": "Display access changed. Sign in again."}, status_code=401, headers={"Cache-Control": "no-store"})
            return RedirectResponse("/login", status_code=303)
        store_path = f"/dashboard/stores/{store_id}"
        if request.method == "GET" and request.url.path in {"/", "/dashboard"}:
            return RedirectResponse(store_path, status_code=303)
        if request.method == "GET" and request.url.path == store_path:
            return await call_next(request)
        if request.method == "GET" and request.url.path == "/dashboard/data":
            if (request.query_params.getlist("store_id") == [str(store_id)]
                    and request.query_params.getlist("date") in ([], ["today"])):
                return await call_next(request)
        # Claim routes repeat assignment, store, date and signed-window checks.
        if ((request.method == "GET" and request.url.path == "/poynt/claim-tips")
                or (request.method == "POST" and request.url.path in {
                    "/poynt/claim-tips/load", "/poynt/tip-submissions"})):
            return await call_next(request)
        return JSONResponse({"detail": "Store Display accounts can only view their assigned store today."},
                            status_code=403, headers={"Cache-Control": "no-store"})
