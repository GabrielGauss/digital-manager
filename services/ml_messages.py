"""
MercadoLibre internal messaging service.

POST /messages/packs/{pack_id}/sellers/{seller_id}?tag=post_sale
  → Sends a message in the buyer-seller chat for an order.
  → When pack_id is None, use order_id as pack_id (ML accepts this).

POST /answers
  → Answers a public question on a listing.
"""

import httpx
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

ML_BASE = "https://api.mercadolibre.com"

# Seller account — cached at startup from /users/me
_SELLER_ID: str | None = None


async def get_seller_id(access_token: str) -> str:
    global _SELLER_ID
    if _SELLER_ID:
        return _SELLER_ID
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{ML_BASE}/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        _SELLER_ID = str(r.json()["id"])
    return _SELLER_ID


async def send_order_message(
    order_id: str,
    buyer_id: str,
    text: str,
    access_token: str,
    pack_id: str | None = None,
) -> bool:
    """
    Send a post-sale message to a buyer via ML internal chat.
    Uses pack_id if available, falls back to order_id (works for single-item orders).
    """
    seller_id = await get_seller_id(access_token)
    thread_id = pack_id or order_id

    url = f"{ML_BASE}/messages/packs/{thread_id}/sellers/{seller_id}?tag=post_sale"
    payload = {
        "from": {"user_id": seller_id},
        "to": {"user_id": buyer_id},
        "text": text,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code in (200, 201):
                logger.info(f"[ml_msg] Message sent to buyer {buyer_id} for order {order_id}")
                return True
            logger.error(f"[ml_msg] Failed ({r.status_code}): {r.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"[ml_msg] Exception sending message: {e}")
        return False


def build_delivery_message(buyer_name: str, bundle_name: str, drive_url: str, image_count: int) -> str:
    """
    Build the post-sale internal message text (plain text, ML chat doesn't support HTML).
    Short, scannable, action-oriented.
    """
    first_name = buyer_name.split()[0].capitalize() if buyer_name else "hola"
    clean_name = bundle_name.replace("Pack Digital ", "").replace("Pack ", "").strip()
    count_str = f"{image_count} imágenes" if image_count else "todas las imágenes"

    return f"""¡Hola {first_name}! 👋 Muchas gracias por tu compra.

Tu pack "{clean_name}" ({count_str}) ya está listo para descargar.

🔗 LINK DE DESCARGA:
{drive_url}

📥 ¿CÓMO DESCARGO TODO?
1. Abrí el link de arriba (Google Drive)
2. Clic en los tres puntos ⋮ (arriba a la derecha) → "Descargar todo"
3. Se baja un .zip con todas las imágenes en JPG/PNG
4. ¿Sin cuenta Google? Buscá "Descargar sin iniciar sesión" en la pantalla

✅ El link es tuyo para siempre, guardalo.

Si tenés algún problema o no podés abrir las imágenes, respondeme acá y lo solucionamos al toque. ¡Gracias! 🙌"""


# ── Questions auto-responder ───────────────────────────────────────────────────

SHIPPING_KEYWORDS = ["envío", "envio", "correo", "entreg", "físico", "fisico", "cuando llega", "cuanto tarda"]
DOWNLOAD_KEYWORDS = ["como recibo", "cómo recibo", "como descargo", "cómo descargo",
                     "como funciona", "cómo funciona", "link", "descarga", "drive",
                     "como accedo", "cómo accedo"]
CONTENT_KEYWORDS = ["que incluye", "qué incluye", "cuantas", "cuántas", "imagenes",
                    "imágenes", "resolución", "resolucion", "formato", "tamaño", "tamano"]
PRICE_KEYWORDS = ["precio", "descuento", "oferta", "promo", "rebaja", "cuota", "cuotas"]


def classify_question(text: str) -> str:
    """
    Classify a buyer question. Returns: 'shipping' | 'download' | 'content' | 'price' | 'other'
    """
    t = text.lower()
    if any(kw in t for kw in SHIPPING_KEYWORDS):
        return "shipping"
    if any(kw in t for kw in DOWNLOAD_KEYWORDS):
        return "download"
    if any(kw in t for kw in CONTENT_KEYWORDS):
        return "content"
    if any(kw in t for kw in PRICE_KEYWORDS):
        return "price"
    return "other"


def build_question_answer(category: str, item_name: str = "") -> str | None:
    """
    Generate an auto-answer for a classified question.
    Returns None for categories that should NOT be auto-answered (price negotiations).
    """
    clean = item_name.replace("Pack Digital ", "").replace("Pack ", "").strip()
    name_part = f'"{clean}"' if clean else "este pack"

    if category == "shipping":
        return (
            f"¡Hola! {name_part.capitalize()} es un producto 100% digital, no tiene envío físico. "
            "Cuando confirmás la compra te enviamos el link de descarga inmediatamente "
            "por email y por este mismo chat. ¡Descargás todo desde Google Drive en segundos! "
            "¿Te surge alguna otra consulta? 😊"
        )

    if category == "download":
        return (
            f"¡Hola! El proceso es muy sencillo: una vez que confirmás la compra "
            f"te mandamos el link de Google Drive con todas las imágenes de {name_part}. "
            "Desde ahí podés descargar todo en un .zip (botón ⋮ → 'Descargar todo'). "
            "No necesitás cuenta de Google y el link es permanente. "
            "¿Alguna otra duda? 🙌"
        )

    if category == "content":
        return (
            f"¡Hola! {name_part.capitalize()} incluye imágenes en formato JPG/PNG de alta resolución, "
            "listas para imprimir en A4, A3, 30×40 cm y más. "
            "La cantidad exacta de imágenes está en el título de la publicación. "
            "¿Querés saber algo más del contenido? Con gusto te ayudo. 🖼️"
        )

    # price and other: don't auto-answer
    return None


async def answer_question(question_id: int, answer_text: str, access_token: str) -> bool:
    """Post an answer to a buyer question on a listing."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{ML_BASE}/answers",
                json={"question_id": question_id, "text": answer_text},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code in (200, 201):
                logger.info(f"[ml_msg] Answered question {question_id}")
                return True
            logger.error(f"[ml_msg] Answer failed ({r.status_code}): {r.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"[ml_msg] Exception answering question {question_id}: {e}")
        return False


async def get_unanswered_questions(access_token: str, limit: int = 50) -> list[dict]:
    """Fetch unanswered questions for the seller's listings."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{ML_BASE}/my/received_questions/search?status=UNANSWERED&limit={limit}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            return r.json().get("questions", [])
    except Exception as e:
        logger.error(f"[ml_msg] Failed to fetch questions: {e}")
        return []


async def get_all_questions(access_token: str, limit: int = 20) -> list[dict]:
    """Fetch recent questions (answered and unanswered) for the admin view."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{ML_BASE}/my/received_questions/search?limit={limit}&sort_fields=date_created&sort_types=DESC",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            return r.json().get("questions", [])
    except Exception as e:
        logger.error(f"[ml_msg] Failed to fetch all questions: {e}")
        return []


async def auto_answer_questions(access_token: str, item_names: dict[str, str]) -> dict:
    """
    Auto-answer unanswered questions we can classify.
    item_names: {ml_item_id: bundle_name}
    Returns stats.
    """
    questions = await get_unanswered_questions(access_token)
    stats = {"answered": 0, "skipped": 0, "errors": []}

    for q in questions:
        qid = q["id"]
        text = q.get("text", "")
        item_id = q.get("item_id", "")
        item_name = item_names.get(item_id, "")

        category = classify_question(text)
        answer = build_question_answer(category, item_name)

        if answer is None:
            stats["skipped"] += 1
            logger.info(f"[ml_msg] Skipping question {qid} (category={category}): {text[:60]}")
            continue

        ok = await answer_question(qid, answer, access_token)
        if ok:
            stats["answered"] += 1
        else:
            stats["errors"].append(f"Q{qid}: {text[:40]}")

    return stats
