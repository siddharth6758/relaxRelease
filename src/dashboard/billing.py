"""
billing.py — Lemon Squeezy integration for RelaxRelease.

Handles:
  - Generating hosted checkout URLs
  - Verifying + processing LS webhooks
  - Mapping variant IDs → plan names
"""

import os
import hmac
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import requests as req
from dotenv import load_dotenv
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

from .database import get_subscription, upsert_subscription, cancel_subscription, get_user_plan, PLAN_LIMITS
from .auth import get_current_user

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

router = APIRouter(prefix="/billing", tags=["billing"])

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LS_API_KEY = os.environ.get("LEMONSQUEEZY_API_KEY", "")
LS_STORE_ID = os.environ.get("LEMONSQUEEZY_STORE_ID", "406626")
LS_WEBHOOK_SECRET = os.environ.get("LEMONSQUEEZY_WEBHOOK_SECRET", "")

LS_PRO_VARIANT_ID = os.environ.get("LEMONSQUEEZY_PRO_VARIANT_ID", "1790142")
LS_MAX_VARIANT_ID = os.environ.get("LEMONSQUEEZY_MAX_VARIANT_ID", "1790168")

VARIANT_TO_PLAN = {
    LS_PRO_VARIANT_ID: "pro",
    LS_MAX_VARIANT_ID: "max",
}

LS_HEADERS = {
    "Accept": "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
    "Authorization": f"Bearer {LS_API_KEY}",
}

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan_from_variant(variant_id: str) -> str:
    print(f"DEBUG variant_id from payload _plan: {variant_id}")
    return VARIANT_TO_PLAN.get(str(variant_id), "free")


def _verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify the X-Signature header from Lemon Squeezy."""
    if not LS_WEBHOOK_SECRET:
        return False
    mac = hmac.new(LS_WEBHOOK_SECRET.encode(), msg=body, digestmod=hashlib.sha256)
    expected = mac.hexdigest()
    return hmac.compare_digest(expected, signature)


def create_checkout_url(variant_id: str, user_email: str, user_id: str) -> str:
    """
    Creates a Lemon Squeezy hosted checkout URL for the given variant.
    Embeds user_id in custom_data so we can link the subscription back.
    """
    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email": user_email,
                    "custom": {
                        "user_id": user_id,
                    },
                },
                "product_options": {
                    "redirect_url": f"{APP_URL}/billing/success",
                },
            },
            "relationships": {
                "store": {
                    "data": {"type": "stores", "id": str(LS_STORE_ID)}
                },
                "variant": {
                    "data": {"type": "variants", "id": str(variant_id)}
                },
            },
        }
    }

    response = req.post(
        "https://api.lemonsqueezy.com/v1/checkouts",
        headers=LS_HEADERS,
        json=payload,
        timeout=15,
    )

    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Lemon Squeezy checkout failed: {response.text}"
        )

    data = response.json()
    return data["data"]["attributes"]["url"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/checkout/{plan}")
async def checkout(plan: str, request: Request):
    """Redirect user to Lemon Squeezy checkout for the chosen plan."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    variant_map = {
        "pro": LS_PRO_VARIANT_ID,
        "max": LS_MAX_VARIANT_ID,
    }

    if plan not in variant_map:
        raise HTTPException(status_code=400, detail="Invalid plan. Choose 'pro' or 'max'.")

    checkout_url = create_checkout_url(
        variant_id=variant_map[plan],
        user_email=user["email"],
        user_id=user["id"],
    )
    return RedirectResponse(checkout_url, status_code=302)


@router.get("/success")
async def billing_success(request: Request):
    """Landing page after successful checkout."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    # Redirect to billing page — subscription will appear once webhook fires
    return RedirectResponse("/billing/plan?upgraded=1", status_code=302)


@router.get("/plan")
async def billing_plan(request: Request, upgraded: int = 0):
    """Show current plan + upgrade options."""
    from fastapi.responses import HTMLResponse
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    plan = get_user_plan(user["id"])
    limits = PLAN_LIMITS[plan]

    sub = get_subscription(user["id"])
    return templates.TemplateResponse("billing.html", {
        "request": request,
        "user": user,
        "plan": plan,
        "limits": limits,
        "upgraded": upgraded,
        "plans": PLAN_LIMITS,
        "expires_at": sub.expires_at if sub else None,
    })


@router.post("/webhook")
async def lemon_squeezy_webhook(request: Request):
    """
    Receives Lemon Squeezy webhook events and updates subscriptions table.

    Events handled:
      - subscription_created
      - subscription_updated
      - subscription_cancelled
      - subscription_expired
      - subscription_paused
      - subscription_resumed
    """
    body = await request.body()

    # Verify signature
    signature = request.headers.get("X-Signature", "")
    if not _verify_webhook_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    event_name = payload.get("meta", {}).get("event_name", "")
    data = payload.get("data", {})
    attributes = data.get("attributes", {})

    print(f"📦 LS Webhook received: {event_name}")

    # Extract fields
    ls_subscription_id = str(data.get("id", ""))
    ls_customer_id = str(attributes.get("customer_id", ""))
    ls_variant_id = str(attributes.get("variant_id", ""))
    status = attributes.get("status", "active")
    user_id = payload.get("meta", {}).get("custom_data", {}).get("user_id", "")
    user_email = attributes.get("user_email", "")

    # Parse period end
    period_end_str = attributes.get("renews_at") or attributes.get("ends_at")
    current_period_end = None
    if period_end_str:
        try:
            current_period_end = datetime.fromisoformat(
                period_end_str.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            pass

    # Fallback: look up user_id by email if custom_data was empty
    if not user_id and user_email:
        print(f"⚠️  No user_id in custom_data, falling back to email lookup: {user_email}")
        # We store email in future; for now log and skip
        return JSONResponse({"ok": False, "reason": "missing user_id"})

    if not user_id:
        print("⚠️  Webhook missing user_id — cannot link subscription.")
        return JSONResponse({"ok": False, "reason": "missing user_id"})

    plan = _plan_from_variant(ls_variant_id)

    from datetime import timedelta

    if event_name == "order_created":
        expires_at = datetime.utcnow() + timedelta(days=30)
        upsert_subscription(
            user_id=user_id,
            plan=plan,
            ls_subscription_id=ls_subscription_id,
            ls_customer_id=ls_customer_id,
            ls_variant_id=ls_variant_id,
            status="active",
            expires_at=expires_at,
        )
        print(f"DEBUG variant_id from payload: {ls_variant_id}")
        print(f"DEBUG PRO variant from env: {LS_PRO_VARIANT_ID}")
        print(f"DEBUG MAX variant from env: {LS_MAX_VARIANT_ID}")
        print(f"✅ One-time payment: user={user_id} plan={plan} expires={expires_at}")

    elif event_name in ("subscription_cancelled", "subscription_expired"):
        cancel_subscription(ls_subscription_id)
        print(f"❌ Cancelled: {ls_subscription_id}")

    return JSONResponse({"ok": True, "event": event_name})