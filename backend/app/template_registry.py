import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
TEMPLATE_STORE_DIR = RUNTIME_DIR / "templates_store"
TEMPLATE_REGISTRY_PATH = RUNTIME_DIR / "templates_registry.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_fields(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        values = str(raw).split(",")
    cleaned = []
    for value in values:
        text = str(value or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def _default_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": "tpl-cfir-core",
            "name": "CFIR Core",
            "type": "cfir",
            "owner_team": "Fraud Ops",
            "fields": [
                "incident_id",
                "date",
                "type",
                "description",
                "impact",
                "actions_taken",
                "recommendations",
            ],
            "active": True,
            "is_default": True,
            "source": "prebuilt",
            "filename": "cfir_core_template.txt",
            "storage_path": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
        {
            "id": "tpl-incident-summary",
            "name": "Incident Summary",
            "type": "incident_summary",
            "owner_team": "Fraud Ops",
            "fields": ["incident_id", "date", "description", "impact", "actions_taken"],
            "active": True,
            "is_default": False,
            "source": "prebuilt",
            "filename": "incident_summary_template.txt",
            "storage_path": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
        {
            "id": "tpl-exec-brief",
            "name": "Executive Brief",
            "type": "executive_brief",
            "owner_team": "Risk Management",
            "fields": ["incident_id", "date", "type", "impact", "recommendations"],
            "active": True,
            "is_default": False,
            "source": "prebuilt",
            "filename": "executive_brief_template.txt",
            "storage_path": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
    ]


def _default_template_contents() -> dict[str, str]:
    return {
        "cfir_core_template.txt": (
            "Incident ID: {{incident_id}}\n"
            "Date: {{date}}\n"
            "Type: {{type}}\n"
            "Description: {{description}}\n"
            "Impact: {{impact}}\n"
            "Actions taken: {{actions_taken}}\n"
            "Recommendations: {{recommendations}}\n"
        ),
        "incident_summary_template.txt": (
            "Incident Summary\n"
            "- Incident ID: {{incident_id}}\n"
            "- Date: {{date}}\n"
            "- Description: {{description}}\n"
            "- Impact: {{impact}}\n"
            "- Actions taken: {{actions_taken}}\n"
        ),
        "executive_brief_template.txt": (
            "Executive Brief\n"
            "Case: {{incident_id}}\n"
            "Date: {{date}}\n"
            "Type: {{type}}\n"
            "Business impact: {{impact}}\n"
            "Recommended next actions: {{recommendations}}\n"
        ),
    }


def _ensure_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATE_STORE_DIR.mkdir(parents=True, exist_ok=True)


def _load_registry() -> list[dict[str, Any]]:
    if not TEMPLATE_REGISTRY_PATH.exists():
        return []
    try:
        payload = json.loads(TEMPLATE_REGISTRY_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
    except Exception:
        return []
    return []


def _save_registry(items: list[dict[str, Any]]) -> None:
    TEMPLATE_REGISTRY_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def ensure_registry_initialized() -> None:
    _ensure_dirs()
    existing = _load_registry()
    if existing:
        return

    templates = _default_templates()
    default_files = _default_template_contents()
    for item in templates:
        filename = item["filename"]
        path = TEMPLATE_STORE_DIR / filename
        path.write_text(default_files.get(filename, ""), encoding="utf-8")
        item["storage_path"] = str(path)

    _save_registry(templates)


def list_templates(active_only: bool = False) -> list[dict[str, Any]]:
    items = _load_registry()
    if active_only:
        items = [item for item in items if bool(item.get("active", True))]
    return sorted(items, key=lambda item: (not bool(item.get("is_default")), item.get("name", "")))


def get_template(template_id: str) -> dict[str, Any] | None:
    for item in _load_registry():
        if item.get("id") == template_id:
            return item
    return None


def create_template(
    *,
    name: str,
    template_type: str,
    owner_team: str,
    fields: Any,
    active: bool,
    is_default: bool,
    filename: str,
    content_bytes: bytes,
    source: str = "user",
) -> dict[str, Any]:
    _ensure_dirs()
    items = _load_registry()

    if is_default:
        for item in items:
            item["is_default"] = False
            item["updated_at"] = _now_iso()

    stored_name = f"{uuid4().hex}_{filename}"
    storage_path = TEMPLATE_STORE_DIR / stored_name
    storage_path.write_bytes(content_bytes)

    record = {
        "id": f"tpl-{uuid4().hex[:10]}",
        "name": str(name or "").strip() or filename,
        "type": str(template_type or "custom").strip() or "custom",
        "owner_team": str(owner_team or "").strip() or "Unassigned",
        "fields": _normalize_fields(fields),
        "active": bool(active),
        "is_default": bool(is_default),
        "source": source,
        "filename": filename,
        "storage_path": str(storage_path),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    items.append(record)
    _save_registry(items)
    return record


def update_template(template_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    items = _load_registry()
    target = None

    for item in items:
        if item.get("id") == template_id:
            target = item
            break

    if target is None:
        return None

    if "is_default" in patch and bool(patch["is_default"]):
        for item in items:
            item["is_default"] = False
            item["updated_at"] = _now_iso()

    if "name" in patch:
        target["name"] = str(patch.get("name") or target.get("name") or "").strip() or target.get("name")
    if "type" in patch:
        target["type"] = str(patch.get("type") or target.get("type") or "").strip() or target.get("type")
    if "owner_team" in patch:
        target["owner_team"] = str(patch.get("owner_team") or target.get("owner_team") or "").strip() or target.get("owner_team")
    if "fields" in patch:
        target["fields"] = _normalize_fields(patch.get("fields"))
    if "active" in patch:
        target["active"] = bool(patch.get("active"))
    if "is_default" in patch:
        target["is_default"] = bool(patch.get("is_default"))

    target["updated_at"] = _now_iso()
    _save_registry(items)
    return target


def get_default_template() -> dict[str, Any] | None:
    items = _load_registry()
    for item in items:
        if bool(item.get("is_default")) and bool(item.get("active", True)):
            return item
    for item in items:
        if bool(item.get("active", True)):
            return item
    return None
