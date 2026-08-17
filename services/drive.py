import io
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image
from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_TOKEN_FILE, DRIVE_ROOT_FOLDER_ID

SCOPES = ["https://www.googleapis.com/auth/drive"]


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


def list_folders(parent_id: str) -> list[dict]:
    service = _get_service()
    query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, webViewLink)",
        orderBy="name",
    ).execute()
    return results.get("files", [])


def list_images(folder_id: str) -> list[dict]:
    service = _get_service()
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


def list_images_recursive(folder_id: str, max_depth: int = 3) -> list[dict]:
    """List all images in folder and all subfolders recursively."""
    images = list_images(folder_id)
    if max_depth > 0:
        for subfolder in list_folders(folder_id):
            images.extend(list_images_recursive(subfolder["id"], max_depth - 1))
    return images


def get_folder_share_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"


def share_folder_publicly(folder_id: str) -> str:
    service = _get_service()
    try:
        service.permissions().create(
            fileId=folder_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
    except Exception as e:
        err = str(e)
        if "already exists" not in err.lower() and "duplicate" not in err.lower():
            print(f"[drive] Warning sharing {folder_id}: {e}")
    return get_folder_share_url(folder_id)


def _compress_image(raw_bytes: bytes, max_size_kb: int = 4000) -> tuple[bytes, str]:
    """Compress image to JPEG, resize if needed."""
    buf = io.BytesIO(raw_bytes)
    img = Image.open(buf).convert("RGB")
    if img.width > 1500 or img.height > 1500:
        img.thumbnail((1500, 1500), Image.LANCZOS)
    out = io.BytesIO()
    quality = 85
    img.save(out, format="JPEG", quality=quality, optimize=True)
    while out.tell() > max_size_kb * 1024 and quality > 40:
        quality -= 10
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue(), "image/jpeg"


def _download_file_bytes(file_id: str) -> bytes:
    service = _get_service()
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def download_first_image_bytes(folder_id: str, max_size_kb: int = 4000) -> tuple[bytes, str] | None:
    """Download and compress the first image found in folder (or subfolders)."""
    images = list_images_recursive(folder_id)
    if not images:
        return None
    try:
        raw = _download_file_bytes(images[0]["id"])
        return _compress_image(raw, max_size_kb)
    except Exception as e:
        print(f"[drive] Error downloading image: {e}")
        return None


def download_preview_images(folder_id: str, count: int = 5) -> list[tuple[bytes, str]]:
    """
    Download up to `count` preview images spread across the folder and subfolders.
    Selects images evenly distributed so previews show variety.
    """
    all_images = list_images_recursive(folder_id)
    if not all_images:
        return []

    # Select evenly distributed images for variety
    if len(all_images) <= count:
        selected = all_images
    else:
        step = len(all_images) / count
        selected = [all_images[int(i * step)] for i in range(count)]

    results = []
    for img in selected:
        try:
            raw = _download_file_bytes(img["id"])
            compressed = _compress_image(raw)
            results.append(compressed)
        except Exception as e:
            print(f"[drive] Error downloading preview {img.get('name')}: {e}")

    return results


def scan_root_folder() -> list[dict]:
    """
    Scan the root Drive folder. For each subfolder:
    - Counts images recursively (including nested subfolders like decade folders)
    - Shares publicly so buyers can access without Google account
    - Returns thumbnail from first image found anywhere in the tree
    """
    folders = list_folders(DRIVE_ROOT_FOLDER_ID)
    result = []
    for folder in folders:
        all_images = list_images_recursive(folder["id"])
        image_count = len(all_images)
        public_url = share_folder_publicly(folder["id"])
        thumbnail = all_images[0].get("thumbnailLink") if all_images else None
        result.append({
            "id": folder["id"],
            "name": folder["name"],
            "url": public_url,
            "image_count": image_count,
            "thumbnail": thumbnail,
        })
    return result
