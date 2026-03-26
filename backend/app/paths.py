import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_DATA_PATH = Path.home() / "OneDrive" / "FraudIncidents"
ALT_DEFAULT_DATA_PATH = Path.home() / "Desktop" / "OneDrive" / "FraudIncidents"

# Load backend/.env once so DATA_PATH can be configured there.
load_dotenv(BACKEND_ROOT / ".env")


def resolve_data_path() -> Path:
    configured = str(os.getenv("DATA_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser()

    if DEFAULT_DATA_PATH.exists():
        return DEFAULT_DATA_PATH

    if ALT_DEFAULT_DATA_PATH.exists():
        return ALT_DEFAULT_DATA_PATH

    return DEFAULT_DATA_PATH


def ensure_data_path(path: Path | None = None) -> Path:
    resolved = path or resolve_data_path()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


DATA_PATH = ensure_data_path()