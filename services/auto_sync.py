"""
Auto-sync engine: Drive folders → bundles → MercadoLibre listings.
Runs on schedule or on demand. Zero manual intervention needed.
"""

import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.db import SessionLocal, init_db
from database.models import Bundle
from services.drive import scan_root_folder, get_folder_share_url
from services import mercadolibre as ml

logger = logging.getLogger(__name__)

# ── Pricing rules (ARS) by image count ────────────────────────────────────────
# Base: 50 imgs = $10.000 | 100 imgs = $15.000 | Minimum ever: $8.000
PRICE_TIERS = [
    (10,   8000),
    (20,   9000),
    (50,  10000),
    (75,  12500),
    (100, 15000),
    (999, 18000),
]

# Hard floor — no bundle can ever be listed below this price
MIN_PRICE = 8000.0

# Promo pricing: discount % applied when publishing as promo
PROMO_DISCOUNT = 0.20   # 20% off
LAUNCH_DISCOUNT = 0.15  # 15% off (reduced so we never dip below $8k floor)


def _apply_floor(price: float) -> float:
    return float(max(price, MIN_PRICE))


def auto_price(image_count: int, promo: bool = False, launch: bool = False) -> float:
    base = next(price for threshold, price in PRICE_TIERS if image_count <= threshold)
    if launch:
        return _apply_floor(round(base * (1 - LAUNCH_DISCOUNT) / 100) * 100)
    if promo:
        return _apply_floor(round(base * (1 - PROMO_DISCOUNT) / 100) * 100)
    return _apply_floor(base)


def promo_original_price(image_count: int) -> float:
    """Return the base (non-discounted) price for a given image count."""
    return _apply_floor(next(price for threshold, price in PRICE_TIERS if image_count <= threshold))


# ── Description templates ─────────────────────────────────────────────────────
def auto_description(folder_name: str, image_count: int) -> str:
    clean = folder_name.lstrip("0123456789- ").strip()
    return f"""🖼️ PACK DIGITAL - {clean.upper()} | {image_count} IMÁGENES PARA IMPRIMIR

✅ ¿QUÉ INCLUYE?
• {image_count} imágenes de alta calidad en formato JPG/PNG
• Listas para imprimir en cualquier tamaño (A4, A3, 30x40, 50x70 y más)
• Descarga instantánea vía Google Drive

🎨 ESTILO
Pack temático de {clean} seleccionado a mano. Ideal para decoración del hogar, regalo, oficinas y espacios modernos.

📦 CÓMO FUNCIONA
1. Realizá tu compra
2. Recibís un email con el link de descarga en minutos
3. Descargás las imágenes desde Google Drive
4. ¡Imprimís donde quieras!

🖨️ FORMATOS RECOMENDADOS PARA IMPRIMIR
• A4 (21×29.7 cm) — cualquier imprenta o en casa
• A3 (30×42 cm) — para mayor impacto
• 30×40 / 40×60 cm — marcos estándar

⚡ ENTREGA INMEDIATA — sin espera, sin envío físico
💬 Ante cualquier consulta, respondemos rápido.

Palabras clave: impresión digital, arte para imprimir, poster digital, decoración, {clean.lower()}, lámina digital, cuadro digital"""


# ── ML listing title ──────────────────────────────────────────────────────────
def auto_title(folder_name: str, image_count: int) -> str:
    clean = folder_name.lstrip("0123456789- ").strip()
    # ML title max 60 chars
    title = f"Pack Digital {clean} {image_count} Imágenes Para Imprimir"
    if len(title) > 60:
        title = f"Pack {clean} {image_count} Imgs Imprimir"
    return title[:60]


# ── Core sync ─────────────────────────────────────────────────────────────────

async def sync_drive_to_bundles(db: AsyncSession, auto_publish: bool = True) -> dict:
    """
    Scan Drive root folder and create a bundle for every subfolder
    that doesn't have one yet. Optionally publishes to ML immediately.
    """
    logger.info("[sync] Starting Drive → bundles sync")
    stats = {"created": 0, "published": 0, "skipped": 0, "errors": []}

    try:
        folders = scan_root_folder()
    except Exception as e:
        logger.error(f"[sync] Failed to scan Drive: {e}")
        stats["errors"].append(f"Drive scan failed: {e}")
        return stats

    # Get existing folder IDs
    existing = await db.execute(select(Bundle.drive_folder_id))
    existing_ids = {row[0] for row in existing.fetchall()}

    for folder in folders:
        folder_id = folder["id"]
        image_count = folder.get("image_count", 0)

        if folder_id in existing_ids:
            stats["skipped"] += 1
            logger.info(f"[sync] Skipping existing: {folder['name']}")
            continue

        if image_count == 0:
            logger.info(f"[sync] Skipping empty folder: {folder['name']}")
            stats["skipped"] += 1
            continue

        folder_name = folder["name"]
        price = auto_price(image_count, launch=True)  # 30% off launch price
        title = auto_title(folder_name, image_count)
        description = auto_description(folder_name, image_count)
        drive_url = folder.get("url") or get_folder_share_url(folder_id)

        bundle = Bundle(
            name=title,
            description=description,
            price=price,
            drive_folder_id=folder_id,
            drive_folder_url=drive_url,
            image_count=image_count,
            cover_image_url=folder.get("thumbnail"),
            ml_status="draft",
            tags=folder_name.lstrip("0123456789- ").strip().lower(),
        )
        db.add(bundle)
        await db.commit()
        await db.refresh(bundle)
        stats["created"] += 1
        logger.info(f"[sync] Created bundle: {title} (${price})")

        if auto_publish:
            published = await _publish_bundle(bundle, db)
            if published:
                stats["published"] += 1
            else:
                stats["errors"].append(f"Publish failed: {title}")

    logger.info(f"[sync] Done — {stats}")
    return stats


async def publish_all_drafts(db: AsyncSession) -> dict:
    """Publish every bundle that is still in draft status."""
    result = await db.execute(select(Bundle).where(Bundle.ml_status == "draft"))
    drafts = result.scalars().all()
    stats = {"published": 0, "errors": []}

    for bundle in drafts:
        ok = await _publish_bundle(bundle, db)
        if ok:
            stats["published"] += 1
        else:
            stats["errors"].append(bundle.name)

    return stats


async def _publish_bundle(bundle: Bundle, db: AsyncSession) -> bool:
    try:
        access_token = await ml.get_valid_token(db)

        # Upload cover image from Drive
        ml_picture_id = None
        try:
            from services.drive import download_first_image_bytes
            result_img = download_first_image_bytes(bundle.drive_folder_id)
            if result_img:
                img_bytes, mime_type = result_img
                ml_picture_id = await ml.upload_picture_bytes(img_bytes, mime_type, access_token)
                logger.info(f"[sync] Uploaded picture: {ml_picture_id}")
        except Exception as e:
            logger.warning(f"[sync] Could not upload picture for {bundle.name}: {e}")

        result = await ml.create_listing({
            "name": bundle.name,
            "description": bundle.description,
            "price": bundle.price,
            "image_count": bundle.image_count,
            "cover_image_url": bundle.cover_image_url,
            "ml_picture_id": ml_picture_id,
        }, access_token)
        bundle.ml_item_id = result["id"]
        bundle.ml_status = "active"
        bundle.updated_at = datetime.utcnow()
        await db.commit()
        logger.info(f"[sync] Published to ML: {bundle.name} → {result['id']}")
        return True
    except Exception as e:
        logger.error(f"[sync] Failed to publish {bundle.name}: {e}")
        return False


# ── Promo management ──────────────────────────────────────────────────────────

async def apply_promo(bundle_id: int, db: AsyncSession, discount: float = PROMO_DISCOUNT) -> dict:
    """
    Put a specific bundle on promo. Shows crossed-out original price on ML.
    discount: fraction, e.g. 0.20 = 20% off.
    """
    result = await db.execute(select(Bundle).where(Bundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        return {"ok": False, "error": "Bundle not found"}
    if not bundle.ml_item_id:
        return {"ok": False, "error": "Bundle not published on ML"}

    original = promo_original_price(bundle.image_count)
    promo_price = _apply_floor(round(original * (1 - discount) / 100) * 100)

    try:
        access_token = await ml.get_valid_token(db)
        await ml.set_promo_price(bundle.ml_item_id, promo_price, original, access_token)
        bundle.price = promo_price
        bundle.ml_status = "promo"
        bundle.updated_at = datetime.utcnow()
        await db.commit()
        logger.info(f"[promo] {bundle.name}: ${original} → ${promo_price} ({int(discount*100)}% off)")
        return {"ok": True, "original_price": original, "promo_price": promo_price}
    except Exception as e:
        logger.error(f"[promo] Failed: {e}")
        return {"ok": False, "error": str(e)}


async def end_promo(bundle_id: int, db: AsyncSession) -> dict:
    """Restore regular price and clear crossed-out price on ML."""
    result = await db.execute(select(Bundle).where(Bundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        return {"ok": False, "error": "Bundle not found"}
    if not bundle.ml_item_id:
        return {"ok": False, "error": "Bundle not published on ML"}

    regular_price = promo_original_price(bundle.image_count)

    try:
        access_token = await ml.get_valid_token(db)
        await ml.clear_promo_price(bundle.ml_item_id, regular_price, access_token)
        bundle.price = regular_price
        bundle.ml_status = "active"
        bundle.updated_at = datetime.utcnow()
        await db.commit()
        logger.info(f"[promo] Ended promo for {bundle.name}, restored to ${regular_price}")
        return {"ok": True, "price": regular_price}
    except Exception as e:
        logger.error(f"[promo] Failed to end promo: {e}")
        return {"ok": False, "error": str(e)}


async def apply_promo_all(db: AsyncSession, discount: float = PROMO_DISCOUNT) -> dict:
    """Put ALL active bundles on promo."""
    result = await db.execute(select(Bundle).where(Bundle.ml_status == "active"))
    bundles = result.scalars().all()
    stats = {"ok": 0, "errors": []}
    for b in bundles:
        res = await apply_promo(b.id, db, discount)
        if res["ok"]:
            stats["ok"] += 1
        else:
            stats["errors"].append(f"{b.name}: {res['error']}")
    return stats


async def end_promo_all(db: AsyncSession) -> dict:
    """End promo on all bundles that are currently in promo status."""
    result = await db.execute(select(Bundle).where(Bundle.ml_status == "promo"))
    bundles = result.scalars().all()
    stats = {"ok": 0, "errors": []}
    for b in bundles:
        res = await end_promo(b.id, db)
        if res["ok"]:
            stats["ok"] += 1
        else:
            stats["errors"].append(f"{b.name}: {res['error']}")
    return stats


# ── Combo / mega-pack bundles ─────────────────────────────────────────────────

COMBO_DISCOUNT = 0.35   # 35% off when buying a combo of 2+ packs


async def create_combo_bundle(
    bundle_ids: list[int],
    db: AsyncSession,
    auto_publish: bool = True,
) -> dict:
    """
    Merge multiple existing bundles into a combo mega-pack listing.
    The combined image count drives pricing; combo discount applied on top.
    """
    result = await db.execute(select(Bundle).where(Bundle.id.in_(bundle_ids)))
    bundles = result.scalars().all()

    if len(bundles) < 2:
        return {"ok": False, "error": "Need at least 2 bundles for a combo"}

    total_images = sum(b.image_count for b in bundles)
    themes = [b.tags or b.name for b in bundles]
    theme_str = " + ".join(t.title() for t in themes[:3])
    if len(themes) > 3:
        theme_str += f" + {len(themes)-3} más"

    combo_name = f"MEGA PACK {theme_str}"
    folder_name = combo_name

    # Pick the first bundle's Drive folder for image cover
    cover_bundle = bundles[0]

    base_price = promo_original_price(min(total_images, 999))
    combo_price = float(round(base_price * (1 - COMBO_DISCOUNT) / 100) * 100)

    # Build combined description with all Drive URLs
    drive_urls_text = "\n".join(
        f"• {b.name}: {b.drive_folder_url}" for b in bundles
    )
    description = f"""🖼️ MEGA PACK DIGITAL - {theme_str.upper()} | {total_images} IMÁGENES

✅ ¿QUÉ INCLUYE?
• {total_images} imágenes de alta calidad en formato JPG/PNG
• {len(bundles)} colecciones temáticas combinadas
• Listas para imprimir en cualquier tamaño (A4, A3, 30x40, 50x70 y más)
• Descarga instantánea vía Google Drive

📦 COLECCIONES INCLUIDAS
{drive_urls_text}

🎨 PRECIO ESPECIAL COMBO — {int(COMBO_DISCOUNT*100)}% de descuento vs. compra individual

📦 CÓMO FUNCIONA
1. Realizá tu compra
2. Recibís un email con los links de descarga en minutos
3. Descargás las imágenes desde Google Drive
4. ¡Imprimís donde quieras!

⚡ ENTREGA INMEDIATA — sin espera, sin envío físico
💬 Ante cualquier consulta, respondemos rápido.

Palabras clave: mega pack digital, arte para imprimir, poster digital, decoración, {', '.join(themes[:5])}"""

    title = f"MEGA Pack {theme_str} {total_images} Imgs Imprimir"[:60]

    # Use first bundle's Drive folder for cover image
    drive_url = cover_bundle.drive_folder_url

    combo = Bundle(
        name=title,
        description=description,
        price=combo_price,
        drive_folder_id=cover_bundle.drive_folder_id,
        drive_folder_url=drive_url,
        image_count=total_images,
        cover_image_url=cover_bundle.cover_image_url,
        ml_status="draft",
        tags=", ".join(themes[:5]),
    )
    db.add(combo)
    await db.commit()
    await db.refresh(combo)

    logger.info(f"[combo] Created combo bundle: {title} ({total_images} imgs, ${combo_price})")

    if auto_publish:
        published = await _publish_bundle(combo, db)
        if published:
            return {"ok": True, "bundle_id": combo.id, "ml_item_id": combo.ml_item_id, "price": combo_price, "images": total_images}
        else:
            return {"ok": False, "error": "Combo created in DB but ML publish failed", "bundle_id": combo.id}

    return {"ok": True, "bundle_id": combo.id, "price": combo_price, "images": total_images, "status": "draft"}


# ── Standalone runner (called by scheduler) ───────────────────────────────────

async def run_full_sync(auto_publish: bool = True) -> dict:
    await init_db()
    async with SessionLocal() as db:
        return await sync_drive_to_bundles(db, auto_publish=auto_publish)


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_full_sync())
