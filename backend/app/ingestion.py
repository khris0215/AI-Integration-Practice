from pathlib import Path

import requests
from .onedrive_auth import get_token
from .paths import DATA_PATH

SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx"}


def get_local_data_snapshot() -> dict:
    files = [p for p in Path(DATA_PATH).glob("*") if p.is_file()]
    supported = [p for p in files if p.suffix.lower() in SUPPORTED_EXTENSIONS]
    return {
        "data_path": str(DATA_PATH),
        "total_files": len(files),
        "supported_files": len(supported),
    }


def download_files_from_onedrive(folder_path="/FraudIncidents", overwrite=True, interactive=True):
    token = get_token(interactive=interactive)
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{folder_path}:/children"
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    items = resp.json().get("value", [])

    downloaded = 0
    skipped = 0

    for item in items:
        if item.get("file"):
            file_name = item["name"]
            download_url = item["@microsoft.graph.downloadUrl"]
            local_path = DATA_PATH / file_name
            if local_path.exists() and not overwrite:
                skipped += 1
                continue

            file_content = requests.get(download_url, timeout=60).content
            local_path = DATA_PATH / file_name
            local_path.write_bytes(file_content)
            downloaded += 1
            print(f"Downloaded {file_name}")

    return {
        "folder_path": folder_path,
        "remote_item_count": len(items),
        "downloaded_files": downloaded,
        "skipped_files": skipped,
        "data_path": str(DATA_PATH),
    }

if __name__ == "__main__":
    download_files_from_onedrive()