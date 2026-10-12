"""Authenticate a split approver; never store their password."""
import hashlib
import json
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from auth import verify_password
from models import User, OrganizationMember
from permissions import role_can_manage_organization
from tip_submission_model import TipApprovalThrottle
from store_time import utc_now


def throttle(factory, org, actor):
    key=hashlib.sha256(f"{org}:{actor}".encode()).hexdigest();now=utc_now()
    with factory() as session:
        row=session.scalar(select(TipApprovalThrottle).where(TipApprovalThrottle.key==key).with_for_update())
        if row is None:
            try:
                with session.begin_nested():
                    row=TipApprovalThrottle(key=key,window_at=now,attempts=0);session.add(row);session.flush()
            except IntegrityError:
                row=session.scalar(select(TipApprovalThrottle).where(TipApprovalThrottle.key==key).with_for_update())
        if row.window_at<now-timedelta(minutes=5):row.window_at=now;row.attempts=0
        if row.attempts>=6:raise HTTPException(429,"Too many approval attempts. Wait five minutes.")
        row.attempts+=1;session.commit()


def verify_approver(session, org, setting, email, password):
    if len(email)>254 or len(password)>1024:raise HTTPException(400,"Approval credentials are too long.")
    user=session.scalar(select(User).where(User.email==email.strip().lower(),User.is_active.is_(True)))
    valid=verify_password(password,user.password_hash if user else None)
    membership=session.scalar(select(OrganizationMember).where(OrganizationMember.organization_id==org,
        OrganizationMember.user_id==user.id)) if user else None
    from tip_history import can_process_tips
    store_manager=bool(user and membership and membership.role not in {"payroll"} and setting and
        can_process_tips(session,user.id,org,setting.store_id,membership.role))
    permitted=bool(user and user.account_type!="store_display" and membership and
        (role_can_manage_organization(membership.role) or store_manager or setting and setting.cash_confirmer_user_id==user.id))
    if not valid or not permitted:raise HTTPException(403,"Approval credentials are invalid or this person cannot approve this store's split.")
    return user.id


def split_digest(store_id,start,end,ranges,allocations,methods,cash_paid):
    data={"store":store_id,"start":start,"end":end,"ranges":ranges,"allocations":allocations,"methods":methods,"cash_paid":cash_paid}
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":")).encode()).hexdigest()
