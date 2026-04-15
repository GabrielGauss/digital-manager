import httpx
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.models import MLToken
from config import ML_APP_ID, ML_CLIENT_SECRET, ML_REDIRECT_URI, ML_SITE_ID

ML_BASE_URL = "https://api.mercadolibre.com"
ML_AUTH_URL = "https://auth.mercadolibre.com.ar/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"


def get_auth_url() -> str:
    return (
        f"{ML_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={ML_APP_ID}"
        f"&redirect_uri={ML_REDIRECT_URI}"
        f"&scope=offline_access+read+write"
    )


async def exchange_code_for_token(code: str, db: AsyncSession) -> dict:
    import logging
    logger = logging.getLogger(__name__)
    async with httpx.AsyncClient() as client:
        payload = {
            "grant_type": "authorization_code",
            "client_id": ML_APP_ID,
            "client_secret": ML_CLIENT_SECRET,
            "code": code,
            "redirect_uri": ML_REDIRECT_URI,
        }
        logger.info(f"[ML auth] Exchanging code, redirect_uri={ML_REDIRECT_URI}")
        response = await client.post(ML_TOKEN_URL, data=payload)
        logger.info(f"[ML auth] Token response {response.status_code}: {response.text}")
        response.raise_for_status()
        data = response.json()

    await _save_token(db, data)
    return data


async def refresh_access_token(db: AsyncSession) -> str:
    token = await _get_stored_token(db)
    if not token or not token.refresh_token:
        raise ValueError("No refresh token stored. Re-authenticate via /auth/ml")

    async with httpx.AsyncClient() as client:
        response = await client.post(ML_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": ML_APP_ID,
            "client_secret": ML_CLIENT_SECRET,
            "refresh_token": token.refresh_token,
        })
        response.raise_for_status()
        data = response.json()

    await _save_token(db, data)
    return data["access_token"]


async def get_valid_token(db: AsyncSession) -> str:
    token = await _get_stored_token(db)
    if not token:
        raise ValueError("Not authenticated with MercadoLibre. Go to /auth/ml")

    if token.expires_at and datetime.utcnow() >= token.expires_at - timedelta(minutes=5):
        return await refresh_access_token(db)

    return token.access_token


async def _get_stored_token(db: AsyncSession) -> MLToken | None:
    result = await db.execute(select(MLToken).order_by(MLToken.id.desc()).limit(1))
    return result.scalar_one_or_none()


async def _save_token(db: AsyncSession, data: dict):
    existing = await _get_stored_token(db)
    expires_at = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 21600))

    if existing:
        existing.access_token = data["access_token"]
        existing.refresh_token = data.get("refresh_token", existing.refresh_token)
        existing.expires_at = expires_at
        existing.user_id = str(data.get("user_id", existing.user_id))
        existing.updated_at = datetime.utcnow()
    else:
        token = MLToken(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            user_id=str(data.get("user_id", "")),
        )
        db.add(token)

    await db.commit()


# ── Listings ──────────────────────────────────────────────────────────────────

async def create_listing(bundle: dict, access_token: str) -> dict:
    """
    Publish a bundle as a digital product on ML.
    Uses category MLA1635 (Cuadros, Pinturas y Arte) matching existing seller listings.
    bundle = {name, description, price, image_count, cover_image_url}
    """
    image_count = bundle.get("image_count", 1) or 1
    clean_name = bundle["name"]

    payload = {
        "family_name": clean_name,
        "category_id": "MLA1635",
        "price": bundle["price"],
        "currency_id": "ARS",
        "available_quantity": 9999,
        "buying_mode": "buy_it_now",
        "condition": "new",
        "listing_type_id": "gold_pro",
        # immediate_payment disables interest-free installments (cuotas sin interés).
        # This eliminates the ~13% installment fee charged to the seller.
        # Buyers still pay normally; they just can't split in cuotas at our cost.
        "tags": ["immediate_payment"],
        "shipping": {
            "mode": "custom",
            "local_pick_up": False,
            "free_shipping": False,
            "methods": [],
        },
        "sale_terms": [
            {"id": "WARRANTY_TYPE", "value_id": "6150835"},
        ],
        "attributes": [
            {"id": "BRAND", "value_name": "Nudo.decor"},
            {"id": "EMPTY_GTIN_REASON", "value_id": "17055161"},
            {"id": "SALE_FORMAT", "value_id": "1359392"},
            {"id": "UNITS_PER_PACK", "value_name": str(image_count)},
            {"id": "INCLUDES_FRAME", "value_id": "242084"},
            {"id": "MODEL", "value_name": "descarga digital"},
        ],
        "description": {"plain_text": bundle["description"]},
    }

    # Attach picture if provided
    picture_id = bundle.get("ml_picture_id")
    if picture_id:
        payload["pictures"] = [{"id": picture_id}]

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{ML_BASE_URL}/items",
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


async def update_listing(ml_item_id: str, updates: dict, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{ML_BASE_URL}/items/{ml_item_id}",
            json=updates,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


async def fix_shipping(ml_item_id: str, access_token: str) -> dict:
    """Set shipping to custom/acuerdo con vendedor (digital product — no physical delivery)."""
    return await update_listing(ml_item_id, {
        "shipping": {
            "mode": "custom",
            "local_pick_up": False,
            "free_shipping": False,
            "methods": [],
        }
    }, access_token)


async def set_promo_price(
    ml_item_id: str,
    promo_price: float,
    original_price: float,
    access_token: str,
) -> dict:
    """Apply a discounted price on the ML listing."""
    return await update_listing(
        ml_item_id,
        {"price": promo_price},
        access_token,
    )


async def clear_promo_price(
    ml_item_id: str,
    regular_price: float,
    access_token: str,
) -> dict:
    """Restore regular price on ML listing."""
    return await update_listing(
        ml_item_id,
        {"price": regular_price},
        access_token,
    )


async def pause_listing(ml_item_id: str, access_token: str) -> dict:
    return await update_listing(ml_item_id, {"status": "paused"}, access_token)


async def get_order(ml_order_id: str, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{ML_BASE_URL}/orders/{ml_order_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


async def get_buyer_email(buyer_id: str, access_token: str) -> str | None:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{ML_BASE_URL}/users/{buyer_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("email")
    return None


async def apply_immediate_payment(ml_item_id: str, access_token: str) -> bool:
    """Add immediate_payment tag to an existing listing to disable cuotas fee."""
    try:
        await update_listing(ml_item_id, {"tags": ["immediate_payment"]}, access_token)
        return True
    except Exception as e:
        print(f"[ml] Failed to apply immediate_payment to {ml_item_id}: {e}")
        return False


async def upload_picture_bytes(image_bytes: bytes, mime_type: str, access_token: str) -> str | None:
    """Upload raw image bytes to ML and return the picture ID."""
    ext = mime_type.split("/")[-1] if "/" in mime_type else "jpg"
    filename = f"cover.{ext}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{ML_BASE_URL}/pictures/items/upload",
            files={"file": (filename, image_bytes, mime_type)},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code in (200, 201):
            return response.json().get("id")
        print(f"[ml] Picture upload failed: {response.status_code} {response.text[:200]}")
    return None


async def upload_picture(image_url: str, access_token: str) -> str | None:
    """Upload an image from a URL to ML and return the picture ID."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{ML_BASE_URL}/pictures/items/upload",
            json={"source": image_url},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 200:
            return response.json().get("id")
    return None
