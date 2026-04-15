from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.db import get_db
from database.models import Order
from services.email_service import send_delivery_email
from database.models import Bundle
from datetime import datetime

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/")
async def list_orders(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).order_by(Order.created_at.desc()))
    orders = result.scalars().all()
    return [_order_to_dict(o) for o in orders]


@router.post("/{order_id}/resend-email")
async def resend_email(order_id: int, db: AsyncSession = Depends(get_db)):
    """Manually resend the delivery email for an order."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    if not order.buyer_email:
        raise HTTPException(status_code=400, detail="La orden no tiene email del comprador")

    bundle = None
    if order.bundle_id:
        b_result = await db.execute(select(Bundle).where(Bundle.id == order.bundle_id))
        bundle = b_result.scalar_one_or_none()

    if not bundle:
        raise HTTPException(status_code=400, detail="Bundle no encontrado para esta orden")

    sent = send_delivery_email(
        to_email=order.buyer_email,
        buyer_name=order.buyer_nickname or "Cliente",
        bundle_name=bundle.name,
        drive_url=bundle.drive_folder_url,
        order_id=order.ml_order_id,
        image_count=bundle.image_count,
    )

    if sent:
        order.email_sent = True
        order.email_sent_at = datetime.utcnow()
        order.drive_link_sent = bundle.drive_folder_url
        await db.commit()
        return {"status": "sent", "to": order.buyer_email}
    else:
        raise HTTPException(status_code=500, detail="Error al enviar el email")


def _order_to_dict(o: Order) -> dict:
    return {
        "id": o.id,
        "ml_order_id": o.ml_order_id,
        "ml_item_id": o.ml_item_id,
        "bundle_id": o.bundle_id,
        "buyer_nickname": o.buyer_nickname,
        "buyer_email": o.buyer_email,
        "amount": o.amount,
        "status": o.status,
        "email_sent": o.email_sent,
        "email_sent_at": o.email_sent_at.isoformat() if o.email_sent_at else None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }
