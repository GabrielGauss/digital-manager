import resend
from config import RESEND_API_KEY, EMAIL_FROM
import logging

resend.api_key = RESEND_API_KEY
logger = logging.getLogger(__name__)

BRAND_YELLOW = "#FFE600"
BRAND_DARK = "#1a1a1a"


def send_delivery_email(
    to_email: str,
    buyer_name: str,
    bundle_name: str,
    drive_url: str,
    order_id: str,
    image_count: int = 0,
) -> bool:
    """
    Send the Drive download link to a buyer after a confirmed purchase.
    The drive_url must be a publicly shareable link (anyone with the link → view).
    """
    first_name = buyer_name.split()[0].capitalize() if buyer_name else "Cliente"
    clean_name = bundle_name.replace("Pack Digital ", "").replace("Pack ", "").strip()
    count_line = f"{image_count} imágenes" if image_count else "tus imágenes"

    html_body = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Tu pack de imágenes está listo</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: #f0f2f5; color: #333; }}
    .wrap {{ max-width: 600px; margin: 0 auto; }}

    /* Header */
    .header {{ background: {BRAND_YELLOW}; padding: 32px 40px; text-align: center; }}
    .header .logo {{ font-size: 13px; font-weight: 700; letter-spacing: 2px;
                     text-transform: uppercase; color: #555; margin-bottom: 16px; }}
    .header h1 {{ font-size: 26px; font-weight: 800; color: {BRAND_DARK}; line-height: 1.2; }}

    /* Body */
    .body {{ background: white; padding: 40px; }}
    .greeting {{ font-size: 17px; margin-bottom: 20px; }}
    .highlight {{ background: #f8f9fa; border-left: 4px solid {BRAND_YELLOW};
                  border-radius: 0 8px 8px 0; padding: 16px 20px; margin-bottom: 28px; }}
    .highlight .pack-name {{ font-size: 18px; font-weight: 700; color: {BRAND_DARK}; }}
    .highlight .pack-count {{ font-size: 14px; color: #666; margin-top: 4px; }}

    /* CTA */
    .cta-wrap {{ text-align: center; margin: 32px 0; }}
    .cta {{ display: inline-block; background: {BRAND_DARK}; color: {BRAND_YELLOW} !important;
             padding: 16px 40px; border-radius: 50px; text-decoration: none;
             font-weight: 800; font-size: 16px; letter-spacing: 0.5px;
             transition: opacity .2s; }}

    /* Steps */
    .steps-title {{ font-size: 14px; font-weight: 700; text-transform: uppercase;
                    letter-spacing: 1px; color: #888; margin-bottom: 16px; }}
    .steps {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 32px; }}
    .step {{ display: flex; align-items: flex-start; gap: 14px; }}
    .step-num {{ background: {BRAND_YELLOW}; color: {BRAND_DARK}; font-weight: 800;
                 font-size: 13px; width: 28px; height: 28px; border-radius: 50%;
                 display: flex; align-items: center; justify-content: center;
                 flex-shrink: 0; margin-top: 1px; }}
    .step-text {{ font-size: 15px; line-height: 1.5; color: #444; }}
    .step-text strong {{ color: {BRAND_DARK}; }}

    /* Link box */
    .link-box {{ background: #f8f9fa; border: 1.5px dashed #ddd; border-radius: 8px;
                 padding: 14px 18px; margin-bottom: 28px; word-break: break-all; }}
    .link-box .link-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase;
                              letter-spacing: 1px; color: #888; margin-bottom: 6px; }}
    .link-box a {{ color: #3483FA; font-size: 13px; text-decoration: none; }}

    /* Note */
    .note {{ background: #fffbe6; border: 1px solid #FFE600; border-radius: 8px;
             padding: 14px 18px; font-size: 13px; color: #666; line-height: 1.6;
             margin-bottom: 24px; }}
    .note strong {{ color: {BRAND_DARK}; }}

    /* Footer */
    .footer {{ background: #f0f2f5; padding: 24px 40px; text-align: center;
               font-size: 12px; color: #999; line-height: 1.8; }}
    .footer a {{ color: #888; }}

    /* Divider */
    .divider {{ border: none; border-top: 1px solid #f0f0f0; margin: 28px 0; }}
  </style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <div class="logo">Nudo.decor</div>
    <h1>¡Tu pack está<br>listo para descargar!</h1>
  </div>

  <div class="body">
    <p class="greeting">Hola <strong>{first_name}</strong>,</p>
    <p style="font-size:15px;color:#555;margin-bottom:24px;line-height:1.7">
      Tu compra fue confirmada. Aquí encontrás el link para descargar todas tus imágenes.
      El acceso es <strong>inmediato y permanente</strong> — guardá este email.
    </p>

    <div class="highlight">
      <div class="pack-name">{clean_name}</div>
      <div class="pack-count">{count_line} · Descarga en Google Drive</div>
    </div>

    <div class="cta-wrap">
      <a href="{drive_url}" class="cta">Descargar mis imágenes</a>
    </div>

    <hr class="divider">

    <div class="steps-title">¿Cómo descargo mis imágenes?</div>
    <div class="steps">
      <div class="step">
        <div class="step-num">1</div>
        <div class="step-text">
          Hacé click en el botón de arriba. Se abre <strong>Google Drive</strong> con todas las imágenes.
        </div>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <div class="step-text">
          Para descargar todo a la vez: tocá los <strong>tres puntos ⋮</strong> (arriba a la derecha)
          → <strong>"Descargar todo"</strong>. Se baja un .zip con todas las imágenes.
        </div>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <div class="step-text">
          Descomprimí el .zip y ya tenés los archivos JPG/PNG listos para llevar a imprimir.
        </div>
      </div>
      <div class="step">
        <div class="step-num">4</div>
        <div class="step-text">
          ¿No tenés cuenta de Google? No importa. Abrí el link y usá <strong>"Descargar sin iniciar sesión"</strong>.
        </div>
      </div>
    </div>

    <div class="link-box">
      <div class="link-label">Tu link de descarga</div>
      <a href="{drive_url}">{drive_url}</a>
    </div>

    <div class="note">
      <strong>Importante:</strong> Este link es tuyo para siempre. Podés descargarlo
      tantas veces como quieras. Si tenés algún problema o las imágenes no abren,
      respondé este email y te ayudamos en minutos.
    </div>

    <p style="font-size:14px;color:#666;line-height:1.7">
      ¡Gracias por confiar en Nudo.decor!<br>
      Esperamos que tus impresiones queden geniales. 🖼️
    </p>
  </div>

  <div class="footer">
    <p>Orden <strong>#{order_id}</strong> · Nudo.decor</p>
    <p style="margin-top:4px">¿Problemas? Respondé este email o contactanos por MercadoLibre.</p>
  </div>

</div>
</body>
</html>"""

    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [to_email],
            "subject": f"🖼️ Tu descarga está lista — {clean_name}",
            "html": html_body,
        })
        logger.info(f"[email] Delivery email sent to {to_email} for order #{order_id}")
        return True
    except Exception as e:
        logger.error(f"[email] Failed to send to {to_email}: {e}")
        return False
