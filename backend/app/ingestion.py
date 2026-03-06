import requests
from pathlib import Path
from .onedrive_auth import get_token

DATA_FOLDER = Path("./data")
DATA_FOLDER.mkdir(exist_ok=True)

def download_files_from_onedrive(folder_path="/FraudIncidents"):
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{folder_path}:/children"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    items = resp.json().get("value", [])

    for item in items:
        if item.get("file"):
            file_name = item["name"]
            download_url = item["@microsoft.graph.downloadUrl"]
            file_content = requests.get(download_url).content
            local_path = DATA_FOLDER / file_name
            local_path.write_bytes(file_content)
            print(f"Downloaded {file_name}")

if __name__ == "__main__":
    download_files_from_onedrive()