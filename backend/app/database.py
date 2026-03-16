import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RUNTIME_DIR / "conversations.db"


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


def create_conversation(title: str = "") -> int:
    with get_db() as conn:
        cur = conn.execute("INSERT INTO conversations (title) VALUES (?)", (title,))
        conn.commit()
        return int(cur.lastrowid)


def get_conversations(limit: int = 50) -> List[Dict]:
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
        return [dict(row) for row in cur.fetchall()]


def get_conversation(conv_id: int) -> Optional[Dict]:
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
        return dict(row) if row else None


def delete_conversation(conv_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()
        return cur.rowcount > 0


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
        return cur.rowcount > 0


def add_message(conv_id: int, role: str, content: str) -> int:
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
        return int(cur.lastrowid)


def add_attachment(
    conv_id: int,
    message_id: int,
    filename: str,
    storage_path: str,
    content_type: str,
) -> int:
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
        return int(cur.lastrowid)


def get_attachment(attachment_id: int) -> Optional[Dict]:
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
        return dict(row) if row else None


def get_messages(conv_id: int, limit: int = 50) -> List[Dict]:
    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT
                m.id,
                m.role,
                m.content,
                m.timestamp,
                a.id AS attachment_id,
                a.filename AS attachment_filename
            FROM messages m
            LEFT JOIN attachments a ON a.message_id = m.id
            WHERE m.conversation_id = ?
            ORDER BY m.id ASC
            LIMIT ?
            """,
            (conv_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]
