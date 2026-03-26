import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RUNTIME_DIR / "conversations.db"
logger = logging.getLogger(__name__)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    logger.debug("db.init_db start path=%s", DB_PATH)
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                title TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id);

            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                content_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_attachments_message
            ON attachments(message_id);

            CREATE INDEX IF NOT EXISTS idx_attachments_conversation
            ON attachments(conversation_id);
            """
        )
    logger.debug("db.init_db done")


def create_conversation(title: str = "") -> int:
    logger.debug("db.create_conversation title_len=%s", len(title or ""))
    with get_db() as conn:
        cur = conn.execute("INSERT INTO conversations (title) VALUES (?)", (title,))
        conn.commit()
        conv_id = int(cur.lastrowid)
        logger.debug("db.create_conversation created id=%s", conv_id)
        return conv_id


def get_conversations(limit: int = 50) -> List[Dict]:
    logger.debug("db.get_conversations limit=%s", limit)
    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT
                c.id,
                c.created_at,
                c.updated_at,
                COALESCE(c.title, '') AS title,
                COALESCE((
                    SELECT m.content
                    FROM messages m
                    WHERE m.conversation_id = c.id
                    ORDER BY m.id DESC
                    LIMIT 1
                ), '') AS preview
            FROM conversations c
            ORDER BY datetime(c.updated_at) DESC, c.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        logger.debug("db.get_conversations returned count=%s", len(rows))
        return rows


def get_conversation(conv_id: int) -> Optional[Dict]:
    logger.debug("db.get_conversation conv_id=%s", conv_id)
    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT id, created_at, updated_at, COALESCE(title, '') AS title
            FROM conversations
            WHERE id = ?
            """,
            (conv_id,),
        )
        row = cur.fetchone()
        result = dict(row) if row else None
        logger.debug("db.get_conversation conv_id=%s found=%s", conv_id, bool(result))
        return result


def delete_conversation(conv_id: int) -> bool:
    logger.debug("db.delete_conversation conv_id=%s", conv_id)
    with get_db() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()
        deleted = cur.rowcount > 0
        logger.debug("db.delete_conversation conv_id=%s deleted=%s", conv_id, deleted)
        return deleted


def update_conversation_title(conv_id: int, title: str) -> bool:
    normalized = (title or "").strip()
    if not normalized:
        return False

    with get_db() as conn:
        cur = conn.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (normalized[:120], conv_id),
        )
        conn.commit()
        updated = cur.rowcount > 0
        logger.debug("db.update_conversation_title conv_id=%s updated=%s", conv_id, updated)
        return updated


def add_message(conv_id: int, role: str, content: str) -> int:
    logger.debug("db.add_message conv_id=%s role=%s content_len=%s", conv_id, role, len(content or ""))
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conv_id, role, content),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conv_id,),
        )
        conn.commit()
        message_id = int(cur.lastrowid)
        logger.debug("db.add_message created id=%s conv_id=%s", message_id, conv_id)
        return message_id


def add_attachment(
    conv_id: int,
    message_id: int,
    filename: str,
    storage_path: str,
    content_type: str,
) -> int:
    logger.debug(
        "db.add_attachment conv_id=%s message_id=%s filename=%s content_type=%s",
        conv_id,
        message_id,
        filename,
        content_type,
    )
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO attachments (conversation_id, message_id, filename, storage_path, content_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conv_id, message_id, filename, storage_path, content_type),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conv_id,),
        )
        conn.commit()
        attachment_id = int(cur.lastrowid)
        logger.debug("db.add_attachment created id=%s conv_id=%s", attachment_id, conv_id)
        return attachment_id


def get_attachment(attachment_id: int) -> Optional[Dict]:
    logger.debug("db.get_attachment id=%s", attachment_id)
    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT id, conversation_id, message_id, filename, storage_path, content_type, created_at
            FROM attachments
            WHERE id = ?
            """,
            (attachment_id,),
        )
        row = cur.fetchone()
        result = dict(row) if row else None
        logger.debug("db.get_attachment id=%s found=%s", attachment_id, bool(result))
        return result


def get_messages(conv_id: int, limit: int = 50) -> List[Dict]:
    logger.debug("db.get_messages conv_id=%s limit=%s", conv_id, limit)
    with get_db() as conn:
        cur = conn.execute(
            """
            WITH latest_messages AS (
                SELECT
                    id,
                    role,
                    content,
                    timestamp
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
            SELECT
                m.id,
                m.role,
                m.content,
                m.timestamp,
                a.id AS attachment_id,
                a.filename AS attachment_filename
            FROM latest_messages m
            LEFT JOIN attachments a ON a.message_id = m.id
            ORDER BY m.id ASC
            """,
            (conv_id, limit),
        )
        rows = [dict(row) for row in cur.fetchall()]
        logger.debug("db.get_messages conv_id=%s returned count=%s", conv_id, len(rows))
        return rows
