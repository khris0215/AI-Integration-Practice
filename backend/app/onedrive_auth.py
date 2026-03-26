import os
from pathlib import Path

import msal
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
TENANT_ID = os.getenv("TENANT_ID")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/Files.Read"]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
TOKEN_CACHE_PATH = RUNTIME_DIR / "msal_token_cache.bin"


def _load_cache() -> msal.SerializableTokenCache:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        cache.deserialize(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        TOKEN_CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")


def get_token(interactive: bool = True):
    if not CLIENT_ID or not TENANT_ID:
        raise Exception("CLIENT_ID and TENANT_ID must be set in backend/.env")

    cache = _load_cache()
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        silent = app.acquire_token_silent(SCOPES, account=accounts[0])
        if "access_token" in silent:
            _save_cache(cache)
            return silent["access_token"]

    if not interactive:
        raise Exception("No cached OneDrive token available. Run manual onedrive ingest once to authenticate.")

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise Exception("Failed to create device flow")
    print(flow["message"])  # Instructs user to visit https://microsoft.com/devicelogin
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        _save_cache(cache)
        return result["access_token"]
    raise Exception(result.get("error_description"))