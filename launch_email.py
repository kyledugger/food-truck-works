"""Separate launch confirmation from future Broadcast campaigns."""
import html
import os
from email_service import _send_email


def send_launch_confirmation(email, token):
    base = os.environ["APP_BASE_URL"].rstrip("/")
    url = f"{base}/launch-updates/confirm/{token}"
    _send_email(email, "Confirm your Food Truck Works launch updates",
        "You requested Food Truck Works launch updates and early-access news. "
        f"Confirm your subscription here:\n{url}\n\n"
        "This link expires in 24 hours. If you didn't request this, ignore this email.",
        '<p>You requested Food Truck Works launch updates and early-access news.</p>'
        f'<p><a href="{html.escape(url, quote=True)}">Confirm my subscription</a></p>'
        '<p>This link expires in 24 hours. If you did not request this, ignore this email.</p>')


def build_broadcast_message(email, subject, text_body, html_body):
    """Build a future /email/batch item; never silently fall back to outbound."""
    stream = os.getenv("POSTMARK_BROADCAST_STREAM", "").strip()
    if not stream or stream == "outbound":
        raise RuntimeError("Configure POSTMARK_BROADCAST_STREAM with a Broadcast stream ID")
    sender = os.environ["LAUNCH_EMAIL_FROM"]
    return {"From": sender, "To": email, "Subject": subject,
        "MessageStream": stream, "Tag": "launch-updates",
        "TextBody": text_body + "\n\nUnsubscribe: {{pm:unsubscribe}}",
        "HtmlBody": html_body + '<p><a href="{{pm:unsubscribe}}">Unsubscribe</a></p>'}
