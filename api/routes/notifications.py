"""POST /api/listings/{id}/send-approval-email — sends a low-confidence
approval request to the configured operator inbox via Resend."""
from __future__ import annotations

import html
import logging
import os
from typing import Annotated

import resend
from fastapi import APIRouter, Depends, HTTPException

import db_repo
from api.auth import Principal, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/listings", tags=["notifications"])


def _render_email_html(listing: dict, site_url: str) -> str:
    name = html.escape(str(listing.get("name") or "your business"))
    address = html.escape(str(listing.get("address") or ""))
    opening_hours = html.escape(str(listing.get("opening_hours") or "—"))
    phone = html.escape(str(listing.get("phone") or "—"))
    website = html.escape(str(listing.get("website_url") or "—"))
    review_url = f"{site_url.rstrip('/')}/listing/{listing['id']}"
    confirm_url = f"{review_url}?confirm=1"

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><title>Update your Gelbe Seiten listing</title></head>
<body style="margin:0;padding:24px;background:#f3f4f6;font-family:Georgia,'Times New Roman',serif;color:#111;">
  <div style="max-width:560px;margin:0 auto;background:#fff;">
    <div style="display:flex;justify-content:space-between;align-items:center;padding:16px 24px;border-bottom:1px solid #eee;">
      <span style="background:#fbbf24;font-weight:700;padding:6px 12px;font-size:14px;">Gelbe Seiten</span>
      <span style="color:#9ca3af;font-size:11px;letter-spacing:.08em;">AUTOMATED NOTIFICATION</span>
    </div>

    <div style="background:#fbbf24;padding:24px;">
      <div style="font-size:18px;margin-bottom:6px;">&#10047;</div>
      <h1 style="margin:0 0 4px 0;font-size:22px;line-height:1.25;">Are your details still up to date, {name}?</h1>
      <p style="margin:0;color:#1f2937;font-size:13px;">{name} &middot; {address}</p>
    </div>

    <div style="padding:24px;">
      <p style="margin:0 0 12px 0;font-size:14px;line-height:1.6;">Dear {name},</p>
      <p style="margin:0 0 16px 0;font-size:14px;line-height:1.6;">
        We regularly update listings on Gelbe Seiten to make sure your customers always find the right
        information. While reviewing your listing, we noticed some details that may no longer be accurate &mdash;
        in particular your opening hours.
      </p>

      <div style="border-left:3px solid #fbbf24;background:#f9fafb;padding:14px 16px;margin:18px 0;">
        <p style="margin:0 0 10px 0;font-size:11px;letter-spacing:.08em;color:#6b7280;">YOUR CURRENT LISTING SHOWS:</p>
        <div style="background:#fff;padding:10px 12px;font-size:13px;line-height:1.5;white-space:pre-wrap;">{opening_hours}</div>
        <p style="margin:10px 0 0 0;font-size:12px;color:#6b7280;">Phone: {phone} &middot; Website: {website}</p>
      </div>

      <p style="margin:18px 0;font-size:14px;line-height:1.6;">
        If anything has changed, you can update your listing quickly and easily yourself. It only takes a
        few minutes and makes sure your customers always find reliable information.
      </p>

      <a href="{review_url}" style="display:block;background:#fbbf24;color:#111;text-align:center;padding:16px;font-weight:700;text-decoration:none;font-size:15px;margin:24px 0;">
        Review and update your listing &rarr;
      </a>

      <div style="border:1px solid #e5e7eb;padding:16px;margin:16px 0;">
        <p style="margin:0 0 8px 0;font-size:11px;letter-spacing:.08em;color:#6b7280;">QUICK CONFIRMATION</p>
        <p style="margin:0 0 12px 0;font-size:13px;line-height:1.5;">
          If your opening hours are still correct, you can confirm that directly here &mdash; no login required.
        </p>
        <a href="{confirm_url}" style="display:block;background:#10b981;color:#fff;text-align:center;padding:12px;font-weight:600;text-decoration:none;font-size:14px;">
          &#10003; Yes, these hours are still correct
        </a>
        <p style="margin:10px 0 0 0;font-size:11px;color:#9ca3af;text-align:center;">
          One click to confirm &middot; No login required
        </p>
      </div>

      <p style="margin:18px 0 4px 0;font-size:13px;font-style:italic;color:#4b5563;">If you have any questions, we are happy to help.</p>
      <p style="margin:18px 0 4px 0;font-size:14px;">Kind regards,</p>
      <p style="margin:0;font-size:14px;font-weight:600;">Your Gelbe Seiten Team</p>
    </div>

    <div style="padding:16px 24px;border-top:1px solid #eee;font-size:11px;color:#9ca3af;line-height:1.5;">
      <span style="background:#fbbf24;color:#111;padding:4px 8px;font-weight:700;font-size:11px;">Gelbe Seiten</span>
      <p style="margin:8px 0 0 0;">This email was generated automatically. Please do not reply.</p>
      <p style="margin:4px 0 0 0;">&copy; 2026 Gelbe Seiten</p>
    </div>
  </div>
</body>
</html>"""


def _render_email_text(listing: dict, site_url: str) -> str:
    name = listing.get("name") or "your business"
    review_url = f"{site_url.rstrip('/')}/listing/{listing['id']}"
    return (
        f"Dear {name},\n\n"
        f"We noticed some details on your Gelbe Seiten listing may no longer be accurate, "
        f"in particular your opening hours.\n\n"
        f"Current opening hours: {listing.get('opening_hours') or '—'}\n\n"
        f"Review and update your listing: {review_url}\n\n"
        f"Kind regards,\nYour Gelbe Seiten Team"
    )


@router.post("/{listing_id}/send-approval-email")
def send_approval_email(
    listing_id: int,
    _: Annotated[Principal, Depends(require_auth)],
) -> dict:
    api_key = os.environ.get("RESEND_API_KEY", "")
    to_addr = os.environ.get("APPROVAL_EMAIL_TO", "")
    site_url = os.environ.get("SITE_URL", "http://localhost:5173")

    if not api_key:
        raise HTTPException(status_code=500, detail="RESEND_API_KEY not configured")
    if not to_addr:
        raise HTTPException(status_code=500, detail="APPROVAL_EMAIL_TO not configured")

    listing = db_repo.get_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")

    resend.api_key = api_key
    params: dict = {
        "from": "Gelbe Seiten <onboarding@resend.dev>",
        "to": [to_addr],
        "subject": f"Are your details still up to date, {listing.get('name') or 'your business'}?",
        "html": _render_email_html(listing, site_url),
        "text": _render_email_text(listing, site_url),
    }

    try:
        result = resend.Emails.send(params)
    except Exception as exc:
        logger.exception("resend send failed for listing %s", listing_id)
        raise HTTPException(status_code=502, detail=f"email send failed: {exc}") from exc

    message_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
    logger.info("approval email sent listing=%s to=%s message_id=%s", listing_id, to_addr, message_id)
    return {"ok": True, "to": to_addr, "message_id": message_id}
