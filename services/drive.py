import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE, DRIVE_ROOT_FOLDER_ID

# drive scope is required to create sharing permissions on folders.
# If the stored token lacks this scope, delete google_token.json
# and restart the server — it will prompt re-auth in the browser.
SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


def _get_service():
    creds = None

    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(GOOGLE_TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def list_folders(parent_id: str = DRIVE_ROOT_FOLDER_ID) -> list[dict]:
    """List all sub-folders inside parent_id."""
    service = _get_service()
    query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, webViewLink)",
        orderBy="name",
    ).execute()
    return results.get("files", [])


def list_images(folder_id: str) -> list[dict]:
    """List all image files inside a folder."""
    service = _get_service()
    image_types = "image/jpeg,image/png,image/webp,image/gif"
    query = (
        f"'{folder_id}' in parents and trashed=false "
        f"and (mimeType='image/jpeg' or mimeType='image/png' "
        f"or mimeType='image/webp' or mimeType='image/gif')"
    )
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, webViewLink, thumbnailLink)",
        orderBy="name",
    ).execute()
    return results.get("files", [])


def get_folder_info(folder_id: str) -> dict | None:
    """Get folder metadata."""
    service = _get_service()
    try:
        return service.files().get(
            fileId=folder_id,
            fields="id, name, webViewLink",
        ).execute()
    except Exception:
        return None


def get_folder_share_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"


def share_folder_publicly(folder_id: str) -> str:
    """
    Grant 'anyone with the link can view' permission on a Drive folder.
    Returns the public shareable URL.
    Safe to call multiple times (ignores duplicate permission errors).
    """
    service = _get_service()
    try:
        service.permissions().create(
            fileId=folder_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
    except Exception as e:
        # Already shared or other non-fatal error
        err = str(e)
        if "already exists" not in err.lower() and "duplicate" not in err.lower():
            print(f"[drive] Warning sharing {folder_id}: {e}")
    return get_folder_share_url(folder_id)


def download_first_image_bytes(folder_id: str, max_size_kb: int = 4000) -> tuple[bytes, str] | None:
    """
    Download the first image in a folder, compress to fit ML's upload limit.
    Returns (bytes, mime_type) or None.
    """
    images = list_images(folder_id)
    if not images:
        return None
    service = _get_service()
    file_id = images[0]["id"]

    try:
        import io
        from googleapiclient.http import MediaIoBaseDownload
        from PIL import Image

        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        # Compress: resize to max 1500px wide, JPEG quality 85
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
        if img.width > 1500 or img.height > 1500:
            img.thumbnail((1500, 1500), Image.LANCZOS)

        out = io.BytesIO()
        quality = 85
        img.save(out, format="JPEG", quality=quality, optimize=True)

        # If still too big, reduce quality
        while out.tell() > max_size_kb * 1024 and quality > 40:
            quality -= 10
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)

        return out.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[drive] Error downloading image: {e}")
        return None


def scan_root_folder() -> list[dict]:
    """
    Scan the root Drive folder and return each sub-folder with image count.
    Auto-shares each sub-folder as 'anyone with link can view' so buyers
    can access the download link without needing a Google account.
    """
    folders = list_folders(DRIVE_ROOT_FOLDER_ID)
    result = []
    for folder in folders:
        images = list_images(folder["id"])
        public_url = share_folder_publicly(folder["id"])
        result.append({
            "id": folder["id"],
            "name": folder["name"],
            "url": public_url,
            "image_count": len(images),
            "thumbnail": images[0].get("thumbnailLink") if images else None,
        })
    return result
