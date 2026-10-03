"""Manager-facing first pass at event reservations."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from database import SessionLocal
from event_reservations import prepare_event_times, reserve_event
from models import BookingResource, Event, OrganizationStore
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_organization

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _manager_org(request: Request) -> int:
    user_id = request.session.get("user_id")
    organization_id = get_current_organization_id(request) if user_id else None
    if organization_id is None or not role_can_manage_organization(
        get_organization_role(user_id, organization_id)
    ):
        raise HTTPException(403, "Manager permission is required.")
    return organization_id


def _resources(session, organization_id):
    return session.execute(
        select(BookingResource, OrganizationStore)
        .join(OrganizationStore, BookingResource.organization_store_id == OrganizationStore.id)
        .where(OrganizationStore.organization_id == organization_id,
               OrganizationStore.is_active.is_(True), BookingResource.is_enabled.is_(True))
        .order_by(OrganizationStore.id)
    ).all()


@router.get("/events", response_class=HTMLResponse)
async def events_page(request: Request):
    organization_id = _manager_org(request)
    with SessionLocal() as session:
        available = [{"id": resource.id, "name": resource.name,
                      "capacity": resource.capacity, "timezone": store.timezone_name}
                     for resource, store in _resources(session, organization_id)]
        rows = session.execute(select(Event, BookingResource.name).join(
            BookingResource, Event.booking_resource_id == BookingResource.id
        ).where(Event.organization_id == organization_id).order_by(
            Event.service_start_at.desc(), Event.id.desc()
        ).limit(100)).all()
        events = [{"id": event.id, "title": event.title, "status": event.status,
                   "resource_name": resource_name,
                   "start": event.service_start_local.strftime("%b %d, %Y %I:%M %p"),
                   "end": event.service_end_local.strftime("%b %d, %Y %I:%M %p"),
                   "timezone": event.venue_timezone_name,
                   "setup": event.setup_minutes, "cleanup": event.cleanup_minutes}
                  for event, resource_name in rows]
    return templates.TemplateResponse(request=request, name="events.html", context={
        "resources": available, "events": events,
    })


@router.get("/events/new", response_class=HTMLResponse)
async def new_event_page(request: Request, resource_id: int):
    organization_id = _manager_org(request)
    with SessionLocal() as session:
        resource = next(({"id": item.id, "name": item.name,
                          "timezone": store.timezone_name}
                         for item, store in _resources(session, organization_id)
                         if item.id == resource_id), None)
    if resource is None:
        raise HTTPException(404, "Booking resource not found.")
    return templates.TemplateResponse(request=request, name="event_new.html", context={
        "resource": resource, "values": {},
    })


@router.post("/events")
async def create_event(
    request: Request,
    resource_id: int = Form(...), title: str = Form(...),
    service_start_local: str = Form(...), service_end_local: str = Form(...),
    venue_timezone_name: str = Form(...), setup_minutes: int = Form(0),
    cleanup_minutes: int = Form(0),
):
    organization_id = _manager_org(request)
    try:
        times = prepare_event_times(service_start_local, service_end_local,
                                    venue_timezone_name, setup_minutes, cleanup_minutes)
        with SessionLocal() as session:
            reserve_event(session, organization_id, resource_id, title, times)
            session.commit()
    except ValueError as exc:
        with SessionLocal() as session:
            resource = next(({"id": item.id, "name": item.name,
                              "timezone": store.timezone_name}
                             for item, store in _resources(session, organization_id)
                             if item.id == resource_id), None)
        if resource is None:
            raise HTTPException(404, "Booking resource not found.") from exc
        return templates.TemplateResponse(request=request, name="event_new.html", status_code=400, context={
            "resource": resource, "error": str(exc),
            "values": {"title": title, "service_start_local": service_start_local,
                       "service_end_local": service_end_local,
                       "venue_timezone_name": venue_timezone_name,
                       "setup_minutes": setup_minutes, "cleanup_minutes": cleanup_minutes},
        })
    return RedirectResponse("/events", status_code=303)


@router.post("/events/{event_id}/cancel")
async def cancel_event(request: Request, event_id: int):
    organization_id = _manager_org(request)
    with SessionLocal() as session:
        event = session.execute(select(Event).where(
            Event.id == event_id, Event.organization_id == organization_id
        )).scalar_one_or_none()
        if event is None:
            raise HTTPException(404, "Event not found.")
        event.status = "cancelled"
        session.commit()
    return RedirectResponse("/events", status_code=303)
