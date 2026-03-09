import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from app.retrieval import create_vector_store
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ChangeHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        # Ignore directory changes and temporary files
        if event.is_directory or event.src_path.endswith('.tmp'):
            return
        logger.info(f"Detected change in {event.src_path}, rebuilding index...")
        try:
            create_vector_store()
            logger.info("Index rebuild complete.")
        except Exception as e:
            logger.error(f"Index rebuild failed: {e}")

if __name__ == "__main__":
    path = "C:/Users/Christian/Desktop/AI-Integration-Practice/file_dump"  # <-- check this path
    event_handler = ChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path, recursive=False)
    observer.start()
    logger.info(f"Watching {path} for changes...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()