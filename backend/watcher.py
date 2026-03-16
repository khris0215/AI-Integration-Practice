import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.retrieval import create_vector_store

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

    def on_any_event(self, event):
        if event.is_directory or self._should_ignore(event.src_path):
            return

        logger.info("Detected file change: %s", event.src_path)
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
            create_vector_store()
            logger.info("Index rebuild complete.")
        except Exception as exc:
            logger.exception("Index rebuild failed: %s", exc)
        finally:
            self._rebuild_lock.release()

    def shutdown(self) -> None:
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

if __name__ == "__main__":
    path = str(Path(__file__).resolve().parents[1] / "file_dump")
    event_handler = DebouncedHandler(delay=5.0)
    observer = Observer()
    observer.schedule(event_handler, path, recursive=False)
    observer.start()
    logger.info("Watching %s for changes with debounce.", path)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        event_handler.shutdown()
        observer.stop()
    observer.join()