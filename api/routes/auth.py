from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from services import mercadolibre as ml

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/ml", response_class=HTMLResponse)
async def ml_auth_start():
    """Redirect to MercadoLibre OAuth login."""
    url = ml.get_auth_url()
    return f"""
    <html><body>
    <h2>Conectar con MercadoLibre</h2>
    <p><a href="{url}">Click aquí para autorizar la aplicación</a></p>
    <p>Después de autorizar, copiá el código <code>?code=...</code> de la URL y
    venite a <a href="/auth/ml/callback?code=TU_CODIGO">/auth/ml/callback?code=TU_CODIGO</a></p>
    </body></html>
    """


@router.get("/ml/callback")
async def ml_auth_callback(
    code: str = Query(..., description="Authorization code from ML"),
    db: AsyncSession = Depends(get_db),
):
    """Exchange OAuth code for access token."""
    try:
        data = await ml.exchange_code_for_token(code, db)
        return {
            "status": "ok",
            "message": "Autenticado con MercadoLibre exitosamente",
            "user_id": data.get("user_id"),
            "expires_in": data.get("expires_in"),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al autenticar: {str(e)}")


@router.get("/ml/status")
async def ml_auth_status(db: AsyncSession = Depends(get_db)):
    """Check if ML token is valid."""
    try:
        token = await ml.get_valid_token(db)
        return {"status": "authenticated", "token_preview": token[:10] + "..."}
    except ValueError as e:
        return {"status": "not_authenticated", "message": str(e)}
