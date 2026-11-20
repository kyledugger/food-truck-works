"""Development-only kitchen tools use the existing FTW administrator allowlist."""
import os
from fastapi import HTTPException


def can_test_kitchen(request):
    allowed = {int(value.strip()) for value in os.getenv("LAUNCH_ADMIN_USER_IDS", "").split(",")
               if value.strip().isdigit()}
    return request.session.get("user_id") in allowed


def require_kitchen_admin(request):
    if not can_test_kitchen(request):
        raise HTTPException(403, "Kitchen test mode is available only to FTW administrators.")

#comment