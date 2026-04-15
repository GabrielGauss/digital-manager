from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.db import get_db
from database.models import Bundle
from services.auto_sync import (
    sync_drive_to_bundles,
    publish_all_drafts,
    apply_promo,
    end_promo,
    apply_promo_all,
    end_promo_all,
    create_combo_bundle,
    PROMO_DISCOUNT,
    promo_original_price,
)
from services import mercadolibre as ml
from services.ml_messages import (
    get_all_questions,
    get_unanswered_questions,
    auto_answer_questions,
    answer_question,
    classify_question,
    build_question_answer,
)
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/sync")
async def trigger_sync(
    background_tasks: BackgroundTasks,
    auto_publish: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger Drive → bundles → ML sync."""
    background_tasks.add_task(_run_sync, auto_publish, db)
    return {"status": "sync started", "auto_publish": auto_publish}


@router.post("/sync/now")
async def trigger_sync_now(
    auto_publish: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Trigger sync and wait for result (blocking)."""
    stats = await sync_drive_to_bundles(db, auto_publish=auto_publish)
    return {"status": "done", "stats": stats}


@router.post("/publish-all")
async def publish_all(db: AsyncSession = Depends(get_db)):
    """Publish all draft bundles to ML."""
    stats = await publish_all_drafts(db)
    return {"status": "done", "stats": stats}


# ── Promos ────────────────────────────────────────────────────────────────────
# NOTE: /all/ routes MUST be declared before /{bundle_id}/ to avoid ambiguity

@router.post("/promo/all/start")
async def start_promo_all(
    discount: float = PROMO_DISCOUNT,
    db: AsyncSession = Depends(get_db),
):
    """Put ALL active bundles on promo."""
    stats = await apply_promo_all(db, discount)
    return {"status": "done", "stats": stats}


@router.post("/promo/all/end")
async def stop_promo_all(db: AsyncSession = Depends(get_db)):
    """End promo on all bundles currently in promo status."""
    stats = await end_promo_all(db)
    return {"status": "done", "stats": stats}


@router.post("/promo/{bundle_id}/start")
async def start_promo(
    bundle_id: int,
    discount: float = PROMO_DISCOUNT,
    db: AsyncSession = Depends(get_db),
):
    """Apply promo price to a single bundle (default 20% off)."""
    result = await apply_promo(bundle_id, db, discount)
    return result


@router.post("/promo/{bundle_id}/end")
async def stop_promo(bundle_id: int, db: AsyncSession = Depends(get_db)):
    """Restore regular price on a single bundle."""
    result = await end_promo(bundle_id, db)
    return result


# ── Combos ────────────────────────────────────────────────────────────────────

@router.post("/combo")
async def create_combo(
    bundle_ids: list[int],
    auto_publish: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a combo mega-pack from multiple bundle IDs.
    E.g. POST /admin/combo?bundle_ids=1&bundle_ids=2&bundle_ids=3
    """
    result = await create_combo_bundle(bundle_ids, db, auto_publish=auto_publish)
    return result


async def _run_sync(auto_publish: bool, db: AsyncSession):
    await sync_drive_to_bundles(db, auto_publish=auto_publish)


# ── Questions ──────────────────────────────────────────────────────────────────

@router.get("/questions")
async def list_questions(db: AsyncSession = Depends(get_db)):
    """List recent questions (answered + unanswered) with ML item info."""
    access_token = await ml.get_valid_token(db)
    questions = await get_all_questions(access_token, limit=30)

    # Enrich with bundle names from our DB
    result = await db.execute(select(Bundle))
    bundles = {b.ml_item_id: b.name for b in result.scalars().all() if b.ml_item_id}

    enriched = []
    for q in questions:
        item_id = q.get("item_id", "")
        category = classify_question(q.get("text", ""))
        auto_answer = build_question_answer(category, bundles.get(item_id, ""))
        enriched.append({
            "id": q["id"],
            "text": q.get("text", ""),
            "status": q.get("status", ""),
            "date_created": q.get("date_created", ""),
            "item_id": item_id,
            "item_name": bundles.get(item_id, item_id),
            "buyer_id": q.get("from", {}).get("id"),
            "answer": q.get("answer", {}).get("text") if q.get("answer") else None,
            "category": category,
            "suggested_answer": auto_answer,
        })
    return enriched


@router.post("/questions/auto-answer")
async def auto_answer_all(db: AsyncSession = Depends(get_db)):
    """Auto-answer all classifiable unanswered questions."""
    access_token = await ml.get_valid_token(db)
    result = await db.execute(select(Bundle))
    item_names = {b.ml_item_id: b.name for b in result.scalars().all() if b.ml_item_id}
    stats = await auto_answer_questions(access_token, item_names)
    return {"status": "done", "stats": stats}


@router.post("/questions/{question_id}/answer")
async def answer_one_question(
    question_id: int,
    text: str,
    db: AsyncSession = Depends(get_db),
):
    """Manually answer a specific question."""
    access_token = await ml.get_valid_token(db)
    ok = await answer_question(question_id, text, access_token)
    return {"ok": ok, "question_id": question_id}


# ── Price sync ─────────────────────────────────────────────────────────────────

@router.post("/sync-prices")
async def sync_all_prices(db: AsyncSession = Depends(get_db)):
    """
    Update all published ML listings to the current price stored in the DB.
    Also resets any launch/promo prices back to base tier prices.
    """
    access_token = await ml.get_valid_token(db)
    result = await db.execute(select(Bundle))
    bundles = result.scalars().all()

    stats = {"updated": 0, "skipped": 0, "errors": []}

    for bundle in bundles:
        if not bundle.ml_item_id:
            stats["skipped"] += 1
            continue

        # Recalculate correct base price from current tiers
        correct_price = promo_original_price(bundle.image_count)

        # Only update ML if the DB price differs from the correct tier price
        # (catches bundles still on launch/promo prices)
        if abs(bundle.price - correct_price) < 0.01:
            stats["skipped"] += 1
            continue

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.put(
                    f"https://api.mercadolibre.com/items/{bundle.ml_item_id}",
                    json={"price": correct_price},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if r.status_code == 200:
                    bundle.price = correct_price
                    bundle.ml_status = "active"
                    stats["updated"] += 1
                    logger.info(f"[price-sync] {bundle.name}: ${bundle.price} → ${correct_price}")
                else:
                    stats["errors"].append(f"{bundle.ml_item_id}: {r.text[:80]}")
        except Exception as e:
            stats["errors"].append(f"{bundle.ml_item_id}: {e}")

    await db.commit()
    return {"status": "done", "stats": stats}
