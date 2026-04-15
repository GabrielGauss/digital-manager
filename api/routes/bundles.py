from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.db import get_db
from database.models import Bundle
from services import mercadolibre as ml
from services.drive import scan_root_folder, list_images, get_folder_share_url
from datetime import datetime

router = APIRouter(prefix="/bundles", tags=["bundles"])


class BundleCreate(BaseModel):
    name: str
    description: str
    price: float
    drive_folder_id: str
    drive_folder_url: str | None = None
    image_count: int = 0
    cover_image_url: str | None = None
    category: str | None = None
    tags: str | None = None


class BundleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = None
    cover_image_url: str | None = None
    category: str | None = None
    tags: str | None = None


@router.get("/")
async def list_bundles(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Bundle).order_by(Bundle.created_at.desc()))
    bundles = result.scalars().all()
    return [_bundle_to_dict(b) for b in bundles]


@router.get("/drive/scan")
async def scan_drive():
    """Scan Google Drive root folder and return sub-folders as bundle suggestions."""
    try:
        folders = scan_root_folder()
        return {"folders": folders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al leer Drive: {str(e)}")


@router.get("/drive/{folder_id}/images")
async def get_folder_images(folder_id: str):
    """List images inside a Drive folder."""
    try:
        images = list_images(folder_id)
        return {"folder_id": folder_id, "count": len(images), "images": images}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/")
async def create_bundle(data: BundleCreate, db: AsyncSession = Depends(get_db)):
    drive_url = data.drive_folder_url or get_folder_share_url(data.drive_folder_id)
    bundle = Bundle(
        name=data.name,
        description=data.description,
        price=data.price,
        drive_folder_id=data.drive_folder_id,
        drive_folder_url=drive_url,
        image_count=data.image_count,
        cover_image_url=data.cover_image_url,
        category=data.category,
        tags=data.tags,
        ml_status="draft",
    )
    db.add(bundle)
    await db.commit()
    await db.refresh(bundle)
    return _bundle_to_dict(bundle)


@router.put("/{bundle_id}")
async def update_bundle(bundle_id: int, data: BundleUpdate, db: AsyncSession = Depends(get_db)):
    bundle = await _get_bundle_or_404(bundle_id, db)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(bundle, field, value)
    bundle.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(bundle)
    return _bundle_to_dict(bundle)


@router.post("/{bundle_id}/publish")
async def publish_bundle(bundle_id: int, db: AsyncSession = Depends(get_db)):
    """Publish bundle to MercadoLibre."""
    bundle = await _get_bundle_or_404(bundle_id, db)

    if bundle.ml_item_id and bundle.ml_status == "active":
        raise HTTPException(status_code=400, detail="Bundle ya está publicado en ML")

    try:
        access_token = await ml.get_valid_token(db)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    try:
        result = await ml.create_listing({
            "name": bundle.name,
            "description": bundle.description,
            "price": bundle.price,
            "cover_image_url": bundle.cover_image_url,
        }, access_token)

        bundle.ml_item_id = result["id"]
        bundle.ml_status = "active"
        bundle.updated_at = datetime.utcnow()
        await db.commit()

        return {
            "status": "published",
            "ml_item_id": result["id"],
            "ml_permalink": result.get("permalink"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al publicar en ML: {str(e)}")


@router.post("/{bundle_id}/pause")
async def pause_bundle(bundle_id: int, db: AsyncSession = Depends(get_db)):
    bundle = await _get_bundle_or_404(bundle_id, db)
    if not bundle.ml_item_id:
        raise HTTPException(status_code=400, detail="Bundle no tiene publicación en ML")

    access_token = await ml.get_valid_token(db)
    await ml.pause_listing(bundle.ml_item_id, access_token)
    bundle.ml_status = "paused"
    bundle.updated_at = datetime.utcnow()
    await db.commit()
    return {"status": "paused"}


@router.delete("/{bundle_id}")
async def delete_bundle(bundle_id: int, db: AsyncSession = Depends(get_db)):
    bundle = await _get_bundle_or_404(bundle_id, db)
    await db.delete(bundle)
    await db.commit()
    return {"status": "deleted"}


async def _get_bundle_or_404(bundle_id: int, db: AsyncSession) -> Bundle:
    result = await db.execute(select(Bundle).where(Bundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Bundle no encontrado")
    return bundle


def _bundle_to_dict(b: Bundle) -> dict:
    return {
        "id": b.id,
        "name": b.name,
        "description": b.description,
        "price": b.price,
        "drive_folder_id": b.drive_folder_id,
        "drive_folder_url": b.drive_folder_url,
        "image_count": b.image_count,
        "cover_image_url": b.cover_image_url,
        "ml_item_id": b.ml_item_id,
        "ml_status": b.ml_status,
        "category": b.category,
        "tags": b.tags,
        "active": b.active,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }
