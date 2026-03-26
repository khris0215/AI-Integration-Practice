import logging
import os
import threading
import time
from pathlib import Path

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.retrieval import create_vector_store
from app.paths import DATA_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IGNORED_SUFFIXES = (
    ".tmp",
    ".temp",
    "~",
    ".swp",
    ".crdownload",
    ".part",
)

IGNORED_PREFIXES = (
    "~$",
)

SUPPORTED_SUFFIXES = {
    ".txt",
    ".docx",
    ".pdf",
}

INGEST_ENDPOINT = os.getenv("WATCHER_INGEST_ENDPOINT", "http://127.0.0.1:8000/api/ingest?mode=local")
INGEST_STATUS_ENDPOINT = os.getenv("WATCHER_INGEST_STATUS_ENDPOINT", "http://127.0.0.1:8000/api/ingest/status")


class DebouncedHandler(FileSystemEventHandler):
    def __init__(self, delay: float = 5.0) -> None:
        self.delay = max(float(delay), 0.5)
        self._timer = None
        self._timer_lock = threading.Lock()
        self._rebuild_lock = threading.Lock()

    def _should_ignore(self, path: str) -> bool:
        lower_path = str(path or "").lower()
        filename = Path(lower_path).name
        if filename.startswith(IGNORED_PREFIXES):
            return True
        return filename.endswith(IGNORED_SUFFIXES)

    def _is_supported_file(self, path: str) -> bool:
        candidate = Path(str(path or ""))
        if not candidate.suffix:
            return False
        return candidate.suffix.lower() in SUPPORTED_SUFFIXES

    def on_any_event(self, event):
        if event.is_directory:
            return

        candidate_paths = [event.src_path]
        moved_dest = getattr(event, "dest_path", "")
        if moved_dest:
            candidate_paths.append(moved_dest)

        has_relevant_change = False
        for path in candidate_paths:
            if not path:
                continue
            if self._should_ignore(path):
                continue
            if self._is_supported_file(path):
                has_relevant_change = True
                break

        if not has_relevant_change:
            return

        logger.info(
            "Detected file change event=%s src=%s dest=%s",
            getattr(event, "event_type", "unknown"),
            getattr(event, "src_path", ""),
            getattr(event, "dest_path", ""),
        )
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._rebuild_index)
            self._timer.daemon = True
            self._timer.start()

    def _rebuild_index(self) -> None:
        with self._timer_lock:
            self._timer = None

        if not self._rebuild_lock.acquire(blocking=False):
            logger.info("Rebuild already running; skipping overlapping request.")
            return

        try:
            logger.info("Rebuilding index after %.1fs of inactivity...", self.delay)
            if self._trigger_backend_ingest():
                logger.info("Backend ingestion accepted from watcher event.")
            else:
                logger.warning("Backend ingestion unavailable; rebuilding index directly in watcher process.")
                create_vector_store()
            logger.info("Index rebuild complete.")
        except Exception as exc:
            logger.exception("Index rebuild failed: %s", exc)
        finally:
            self._rebuild_lock.release()

    def _trigger_backend_ingest(self) -> bool:
        """Ask the API process to rebuild index so Chroma file handles stay in one process."""
        try:
            status_resp = requests.get(INGEST_STATUS_ENDPOINT, timeout=(3, 8))
            if status_resp.ok:
                payload = status_resp.json() if status_resp.content else {}
                if bool(payload.get("running")):
                    logger.info("Backend ingest already running; skipping duplicate watcher trigger.")
                    return True
        except Exception:
            # Status endpoint is best-effort; proceed to trigger.
            pass

        resp = requests.post(INGEST_ENDPOINT, timeout=(3, 20))
        if not resp.ok:
            logger.warning("Watcher backend ingest trigger failed status=%s body=%s", resp.status_code, resp.text[:200])
            return False

        return True

    def shutdown(self) -> None:
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

if __name__ == "__main__":
    path = str(DATA_PATH)
    event_handler = DebouncedHandler(delay=5.0)
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()
    logger.info("Watching %s recursively for supported files with debounce.", path)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        event_handler.shutdown()
        observer.stop()
    observer.join()