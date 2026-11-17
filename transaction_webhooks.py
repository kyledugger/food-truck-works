"""Tenant-owned webhook configuration and bounded, repeatable registration."""
from datetime import timedelta
import os
import secrets
from urllib.parse import urlsplit
from sqlalchemy import select, update, or_
from models import OrganizationStore, PoyntConnection
from live_dashboard_models import TransactionWebhook, TransactionStoreSetting
from integrations.crypto import encrypt, decrypt
from store_time import utc_now

EVENTS = ['TRANSACTION_CAPTURED', 'TRANSACTION_UPDATED']


def registration(session, org, business):
    return session.scalar(select(TransactionWebhook).where(
        TransactionWebhook.organization_id == org, TransactionWebhook.business_id == business))


def legacy_org():
    try:
        return int(os.getenv('POYNT_TRANSACTION_PROBE_ORGANIZATION_ID', '1'))
    except ValueError:
        return None


def selected_stores(session, org, business):
    settings = session.scalars(select(TransactionStoreSetting).where(
        TransactionStoreSetting.organization_id == org,
        TransactionStoreSetting.business_id == business)).all()
    if settings or registration(session, org, business):
        return {s.store_id for s in settings if s.enabled}
    # Transitional compatibility is restricted to the original pilot tenant.
    connection = session.scalar(select(PoyntConnection).where(
        PoyntConnection.organization_id == org, PoyntConnection.business_id == business))
    if org == legacy_org() and connection:
        return {s.strip() for s in os.getenv('POYNT_TRANSACTION_TRIGGER_STORE_IDS', '').split(',') if s.strip()}
    return set()


def callback(org):
    # Deployment-level origin, never a Host header supplied by a client.
    configured = os.getenv('APP_BASE_URL') or os.getenv('POYNT_REDIRECT_URI', '')
    url = urlsplit(configured)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError('Configure the public HTTPS application URL.')
    return f'https://{url.netloc}/webhooks/poynt/transactions/{org}'


def configure(session, org, business, selected=None):
    """Call with connection row locked; commit before any provider requests.

    None preserves existing choices and imports the pilot on first setup.
    An empty set explicitly disables every store for this business.
    """
    connection = session.scalar(select(PoyntConnection).where(
        PoyntConnection.organization_id == org).with_for_update())
    if not connection or connection.business_id != business:
        raise ValueError('Reconnect GoDaddy before changing stores.')
    stores = session.scalars(select(OrganizationStore).where(
        OrganizationStore.organization_id == org, OrganizationStore.is_active.is_(True))).all()
    valid = {s.store_id for s in stores}
    if selected is not None and not set(selected) <= valid:
        raise ValueError('Choose active stores belonging to this business.')
    row = registration(session, org, business)
    if row is None:
        pilot_secret = os.getenv('POYNT_TRANSACTION_PROBE_SECRET', '')
        pilot_hook = os.getenv('POYNT_TRANSACTION_PROBE_HOOK_ID', '')
        adopt_pilot = org == legacy_org() and len(pilot_secret) >= 32 and bool(pilot_hook)
        delivery = callback(org)
        if adopt_pilot:
            delivery = delivery.replace('/transactions/', '/transaction-probe/')
        row = TransactionWebhook(organization_id=org, business_id=business,
            application_id=os.environ['POYNT_APP_ID'], delivery_url=delivery,
            secret_encrypted=encrypt(pilot_secret if adopt_pilot else secrets.token_urlsafe(48)),
            hook_id=pilot_hook if adopt_pilot else None, status='pending')
        session.add(row)
    # Reconnect/retry preserves the persisted secret, even after a POST timeout.
    if row.application_id != os.environ['POYNT_APP_ID']:
        raise ValueError('Application identity changed; contact support.')
    if selected is None:
        selected = selected_stores(session, org, business)
        # A newly created registration overrides the legacy fallback above.
        if not session.scalars(select(TransactionStoreSetting.id).where(
                TransactionStoreSetting.organization_id == org,
                TransactionStoreSetting.business_id == business)).first() and org == legacy_org():
            selected = {s.strip() for s in os.getenv('POYNT_TRANSACTION_TRIGGER_STORE_IDS', '').split(',') if s.strip()}
    existing = {s.store_id: s for s in session.scalars(select(TransactionStoreSetting).where(
        TransactionStoreSetting.organization_id == org, TransactionStoreSetting.business_id == business))}
    for store_id in valid | set(existing):
        setting = existing.get(store_id)
        if setting is None:
            setting = TransactionStoreSetting(organization_id=org,business_id=business,store_id=store_id)
            session.add(setting)
        setting.enabled = store_id in selected and store_id in valid
    row.next_check_at = utc_now()
    return row


async def reconcile(factory, org, business, client, force=False):
    """CAS lease excludes concurrent worker/OAuth/manual registration attempts.

    A failed POST is followed by LIST on retry, using the same URL and secret.
    Never edits or deletes unrelated or existing order hooks.
    """
    from transaction_trigger import enabled
    if not enabled():
        return
    now, token = utc_now(), secrets.token_hex(24)
    with factory() as session:
        row = registration(session,org,business)
        if not row or (not force and row.next_check_at > now):
            return
        claimed = session.execute(update(TransactionWebhook).where(
            TransactionWebhook.id == row.id,
            or_(TransactionWebhook.lease_until.is_(None), TransactionWebhook.lease_until <= now)
        ).values(lease_token=token,lease_until=now+timedelta(minutes=3))).rowcount
        if not claimed:
            return
        row_id, url, app, encrypted = row.id, row.delivery_url, row.application_id, row.secret_encrypted
        session.commit()
    hook_id, error = None, None
    try:
        hooks = await client.list_transaction_hooks()
        matches = [h for h in hooks if h.get('applicationId') == app
            and h.get('businessId') == business and h.get('deliveryUrl') == url]
        if len(matches) > 1:
            raise ValueError('Multiple matching registrations; contact support.')
        if matches:
            hook = matches[0]
            # A known hook cannot silently acquire a different signing secret.
            with factory() as session:
                recorded = session.get(TransactionWebhook,row_id).hook_id
            if recorded and hook.get('id') != recorded:
                raise ValueError('Registration identity changed; contact support.')
        else:
            hook = await client.create_transaction_hook(url, decrypt(encrypted), app)
        if (set(hook.get('eventTypes') or []) != set(EVENTS) or hook.get('active') is False
                or not isinstance(hook.get('id'),str) or not 1 <= len(hook['id']) <= 100):
            raise ValueError('Webhook configuration differs; contact support.')
        hook_id = hook['id']
    except Exception:
        # Exception text / provider bodies may contain credentials. Do not persist them.
        error = 'Automatic setup could not be verified. Retry setup or reconnect GoDaddy.'
    with factory() as session:
        row = session.get(TransactionWebhook,row_id)
        if row.lease_token != token:
            return
        row.status = 'error' if error else 'ready'
        row.error = error
        row.next_check_at = utc_now()+timedelta(minutes=5 if error else 60)
        if hook_id:
            row.hook_id = hook_id
            row.verified_at = utc_now()
        row.lease_token = None
        row.lease_until = None
        session.commit()
