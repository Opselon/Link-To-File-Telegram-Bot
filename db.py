import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import DB_PATH

class Database:
    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Downloads table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    filename TEXT,
                    size INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (telegram_id)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def add_user(self, telegram_id: int):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO users (telegram_id) VALUES (?)",
                (telegram_id,)
            )
            conn.commit()
        finally:
            conn.close()

    def add_download(self, user_id: int, url: str, filename: Optional[str] = None, size: Optional[int] = None) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO downloads (user_id, url, filename, size, status) VALUES (?, ?, ?, ?, ?)",
                (user_id, url, filename, size, 'downloading')
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_download_status(self, download_id: int, status: str, filename: Optional[str] = None, size: Optional[int] = None):
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            if filename and size:
                cursor.execute(
                    "UPDATE downloads SET status = ?, filename = ?, size = ? WHERE id = ?",
                    (status, filename, size, download_id)
                )
            else:
                cursor.execute(
                    "UPDATE downloads SET status = ? WHERE id = ?",
                    (status, download_id)
                )
            conn.commit()
        finally:
            conn.close()

    def get_user_downloads(self, user_id: int) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM downloads WHERE user_id = ?", (user_id,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

db = Database()
