from fastapi import APIRouter, Depends, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.db import get_db
from database.models import Order, Bundle
from services import mercadolibre as ml
from services.email_service import send_delivery_email
from services.ml_messages import (
    send_order_message,
    build_delivery_message,
    auto_answer_questions,
    answer_question,
    classify_question,
    build_question_answer,
)
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/ml")
async def mercadolibre_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Receive notifications from MercadoLibre.
    Handles:
      - orders_v2 / orders  → post-sale funnel (email + DM)
      - questions            → auto-answer classification
    """
    body = await request.json()
    logger.info(f"[webhook] ML notification: {body}")

    topic = body.get("topic", "")
    resource = body.get("resource", "")

    if topic in ("orders_v2", "orders"):
        order_id = resource.split("/")[-1]
        background_tasks.add_task(_process_order, order_id, db)
        return {"status": "received", "order_id": order_id}

    if topic == "questions":
        question_id = resource.split("/")[-1]
        background_tasks.add_task(_process_question, question_id, db)
        return {"status": "received", "question_id": question_id}

    return {"status": "ignored", "topic": topic}


# ── Order processing ──────────────────────────────────────────────────────────

async def _process_order(ml_order_id: str, db: AsyncSession):
    existing = await db.execute(select(Order).where(Order.ml_order_id == ml_order_id))
    order = existing.scalar_one_or_none()

    if order and order.email_sent:
        logger.info(f"[webhook] Order {ml_order_id} already processed")
        return

    try:
        access_token = await ml.get_valid_token(db)
        order_data = await ml.get_order(ml_order_id, access_token)
    except Exception as e:
        logger.error(f"[webhook] Failed to fetch order {ml_order_id}: {e}")
        return

    ml_status = order_data.get("status", "")
    if ml_status not in ("paid", "payment_required"):
        logger.info(f"[webhook] Order {ml_order_id} status={ml_status}, skipping")
        return

    items = order_data.get("order_items", [])
    ml_item_id = items[0]["item"]["id"] if items else None
    buyer = order_data.get("buyer", {})
    buyer_id = str(buyer.get("id", ""))
    buyer_nickname = buyer.get("nickname", "Cliente")
    amount = order_data.get("total_amount", 0)
    pack_id = order_data.get("pack_id")  # None for single-item orders

    buyer_email = None
    try:
        buyer_email = await ml.get_buyer_email(buyer_id, access_token)
    except Exception:
        pass

    bundle = None
    if ml_item_id:
        result = await db.execute(select(Bundle).where(Bundle.ml_item_id == ml_item_id))
        bundle = result.scalar_one_or_none()

    if not order:
        order = Order(
            ml_order_id=ml_order_id,
            ml_item_id=ml_item_id or "",
            bundle_id=bundle.id if bundle else None,
            buyer_id=buyer_id,
            buyer_nickname=buyer_nickname,
            buyer_email=buyer_email,
            amount=amount,
            status="paid",
        )
        db.add(order)
    else:
        order.status = "paid"
        order.buyer_email = buyer_email or order.buyer_email

    await db.commit()

    if not bundle:
        logger.warning(f"[webhook] No bundle found for ML item {ml_item_id}")
        return

    drive_url = bundle.drive_folder_url

    # ── 1. Email ─────────────────────────────────────────────────────────────
    email_ok = False
    if buyer_email:
        email_ok = send_delivery_email(
            to_email=buyer_email,
            buyer_name=buyer_nickname,
            bundle_name=bundle.name,
            drive_url=drive_url,
            order_id=ml_order_id,
            image_count=bundle.image_count,
        )
    else:
        logger.warning(f"[webhook] No buyer email for order {ml_order_id}")

    # ── 2. ML internal message ────────────────────────────────────────────────
    msg_text = build_delivery_message(
        buyer_name=buyer_nickname,
        bundle_name=bundle.name,
        drive_url=drive_url,
        image_count=bundle.image_count,
    )
    msg_ok = await send_order_message(
        order_id=ml_order_id,
        buyer_id=buyer_id,
        text=msg_text,
        access_token=access_token,
        pack_id=pack_id,
    )

    # ── 3. Update order record ────────────────────────────────────────────────
    if email_ok or msg_ok:
        order.email_sent = True
        order.email_sent_at = datetime.utcnow()
        order.drive_link_sent = drive_url
        order.status = "delivered"
        await db.commit()
        logger.info(
            f"[webhook] Delivery sent for order {ml_order_id} — "
            f"email={'OK' if email_ok else 'SKIP'} msg={'OK' if msg_ok else 'FAIL'}"
        )
    else:
        logger.error(f"[webhook] Both email and ML message failed for order {ml_order_id}")


# ── Question processing ───────────────────────────────────────────────────────

async def _process_question(question_id: str, db: AsyncSession):
    """Auto-answer a buyer question if we can classify it."""
    try:
        access_token = await ml.get_valid_token(db)

        # Fetch the specific question
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.mercadolibre.com/questions/{question_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code != 200:
                logger.warning(f"[webhook] Could not fetch question {question_id}: {r.status_code}")
                return
            question = r.json()

        if question.get("status") != "UNANSWERED":
            return

        text = question.get("text", "")
        item_id = question.get("item_id", "")

        # Look up item name from our bundles
        result = await db.execute(select(Bundle).where(Bundle.ml_item_id == item_id))
        bundle = result.scalar_one_or_none()
        item_name = bundle.name if bundle else ""

        category = classify_question(text)
        answer = build_question_answer(category, item_name)

        if answer:
            ok = await answer_question(int(question_id), answer, access_token)
            logger.info(
                f"[webhook] Question {question_id} ({category}): "
                f"{'answered' if ok else 'answer failed'}"
            )
        else:
            logger.info(f"[webhook] Question {question_id} ({category}): skipped (manual review needed)")

    except Exception as e:
        logger.error(f"[webhook] Error processing question {question_id}: {e}")
