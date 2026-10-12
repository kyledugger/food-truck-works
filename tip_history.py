"""Role-specific tip history; dates are interpreted in each store's timezone."""
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, and_, or_
from models import User, Employee, OrganizationStore, OrganizationMember, StoreAssignment
from tip_submission_model import TipSubmission, TipEmployeePayout
from store_display_access import display_assignment, valid_display_session
from permissions import role_can_view_payroll_reports


def managed_store_ids(session,user_id,org):
    return set(session.scalars(select(StoreAssignment.organization_store_id).join(OrganizationMember,
        OrganizationMember.id==StoreAssignment.organization_member_id).where(
        OrganizationMember.organization_id==org,OrganizationMember.user_id==user_id,
        StoreAssignment.is_active.is_(True),StoreAssignment.role=="store_manager")))


def can_process_tips(session,user_id,org,store_id,role):
    user=session.get(User,user_id)
    if not user or not user.is_active or user.account_type=="store_display":return False
    if role_can_view_payroll_reports(role):return True
    return bool(session.scalar(select(OrganizationStore.id).where(OrganizationStore.organization_id==org,
        OrganizationStore.store_id==store_id,OrganizationStore.id.in_(managed_store_ids(session,user_id,org)))))


def employee_shifts(shown, own_ids):
    result=[]
    for shift in shown["ranges"]:
        people=shift.get("employees",[]) if isinstance(shift,dict) else []
        if not any(p.get("id") in own_ids for p in people):continue
        total=max(0,int(shift.get("total_tip_cents",0)));share,remainder=divmod(total,len(people))
        if not shown["payouts"]:
            share=int(shift.get("per_employee_cents",share));remainder=0
        result.append({"start":shift.get("start"),"end":shift.get("end"),
            "participants":[{"name":p.get("name","Employee"),"amount_cents":share+(i<remainder),
                "own":p.get("id") in own_ids} for i,p in enumerate(people)]})
    return result


def history_context(session, request, org, role, start, end, store_id, payment, status, helpers):
    bounds_fn,display_fn,totals_fn=helpers
    user=session.get(User,request.session["user_id"])
    if not user or not user.is_active or role is None:raise HTTPException(403,"Business access is required.")
    terminal=user.account_type=="store_display"
    manage=not terminal and role_can_view_payroll_reports(role)
    scoped_managers=managed_store_ids(session,user.id,org) if not terminal and not manage else set()
    stores=session.scalars(select(OrganizationStore).where(OrganizationStore.organization_id==org)).all()
    stores={s.store_id:s for s in stores if s.timezone_name}
    if scoped_managers:
        stores={key:store for key,store in stores.items() if store.id in scoped_managers};manage=True
    assigned=None
    if terminal:
        assignment=display_assignment(session,user.id)
        if not assignment or not valid_display_session(request,*assignment):raise HTTPException(403,"Store Display access changed.")
        assigned=assignment[1]
        if store_id and store_id.lower()!=assigned.store_id.lower():raise HTTPException(403,"History is limited to this store.")
        stores={assigned.store_id:assigned};store_id=assigned.store_id
    if not stores:raise HTTPException(409,"Configure store timezones first.")
    selected=store_id.lower() if store_id and store_id!="all" else "all"
    if selected!="all" and selected not in stores:raise HTTPException(403,"Store is not available in this organization.")
    selected_stores=list(stores.values()) if selected=="all" else [stores[selected]]
    today=datetime.now(ZoneInfo(selected_stores[0].timezone_name)).date().isoformat()
    if terminal:
        if (start and start!=today) or (end and end!=today):raise HTTPException(403,"Store terminals show today's shifts only.")
        start=end=today
    else:start,end=start or today,end or today
    windows=[]
    for store in selected_stores:
        bounds=bounds_fn(start,end,store.timezone_name)
        if bounds is None:raise HTTPException(400,"Select a valid date range.")
        windows.append(and_(TipSubmission.store_id==store.store_id,TipSubmission.report_start_at>=bounds[0],TipSubmission.report_start_at<bounds[1]))
    own_ids=set(session.scalars(select(Employee.id).where(Employee.organization_id==org,Employee.user_id==user.id)))
    query=select(TipSubmission).where(TipSubmission.organization_id==org,or_(*windows)).order_by(TipSubmission.report_start_at.desc(),TipSubmission.id.desc())
    if not manage and not terminal:
        own=select(TipEmployeePayout.submission_id).where(TipEmployeePayout.employee_id.in_(own_ids))
        modern=select(TipEmployeePayout.id).where(TipEmployeePayout.submission_id==TipSubmission.id).exists()
        query=query.where(or_(TipSubmission.id.in_(own),~modern)) if own_ids else query.where(TipSubmission.id<0)
    submissions=session.scalars(query).all();display=[]
    payout_map={}
    if submissions:
        for payout in session.scalars(select(TipEmployeePayout).where(TipEmployeePayout.submission_id.in_([s.id for s in submissions]))):
            payout_map.setdefault(payout.submission_id,[]).append(payout)
    selected_payment=payment if payment in {"cash","paycheck"} else ""
    selected_status=status if status in {"pending","paid","rejected"} else ""
    for submission in submissions:
        payouts=payout_map.get(submission.id,[])
        shown=display_fn(submission,payouts,{p.id for p in payouts} if manage else set(),stores[submission.store_id].timezone_name)
        if not manage and not terminal:
            shown["shifts"]=employee_shifts(shown,own_ids)
            shown["payouts"]=[p for p in shown["payouts"] if p["employee_id"] in own_ids]
            shown["employee_totals"]=[p for p in shown["employee_totals"] if p["id"] in own_ids]
            if not shown["payouts"] and not shown["employee_totals"]:continue
            shown["ranges"]=[];shown["employee_names"]=[]
        if selected_payment or selected_status:
            if shown["payouts"]:
                shown["payouts"]=[p for p in shown["payouts"] if (not selected_payment or p["method"]==selected_payment) and (not selected_status or p["status"]==selected_status)]
                if not shown["payouts"]:continue
            elif (selected_payment and submission.payout_method!=selected_payment) or (selected_status and submission.processing_status!=selected_status):continue
        if not manage and not terminal:
            shown["total_tip_cents"]=sum(p["amount_cents"] for p in shown["payouts"]) if shown["payouts"] else sum(p["total_tip_cents"] for p in shown["employee_totals"])
        display.append(shown)
    totals=totals_fn(display,None if manage or terminal else own_ids)
    return {"submissions":display,"tip_range_totals":totals,"tip_report_start_date":start,"tip_report_end_date":end,
        "tip_report_can_select_date_range":not terminal,"tip_report_validation_message":"",
        "tip_report_payment":selected_payment,"tip_report_status":selected_status,"tip_report_can_process":manage,
        "tip_report_role":role if role_can_view_payroll_reports(role) and manage else "manager" if manage else "member","tip_report_store_id":selected,
        "tip_report_store_timezone":selected_stores[0].timezone_name,"tip_report_stores":[{"id":s.store_id,"name":s.display_name or s.poynt_name} for s in stores.values()],
        "tip_report_terminal":terminal,"tip_report_employee":not terminal and not manage,
        "tip_report_store_name":assigned.display_name or assigned.poynt_name if assigned else "",
        "tip_report_home_id":assigned.id if assigned else None,
        "tip_personal_total_cents":sum(t["cash_cents"]+t["paycheck_cents"] for t in totals)}
