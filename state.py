"""SubtitleCat 插件的持久化抓取状态。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class SubtitleCatFetchState:
    """记录影片是否完成过一次 SubtitleCat 抓取。

    状态放在宿主托管的插件 data 目录中。SQLite 自带事务和跨进程锁，适合
    API/APS 进程在手动任务与定时任务偶尔并行时安全地更新同一份状态。
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS movie_fetch_state (
                    movie_number TEXT PRIMARY KEY,
                    last_fetched_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _key(movie_number: str) -> str:
        normalized = (movie_number or "").strip()
        if not normalized:
            raise ValueError("movie_number 不能为空")
        return normalized

    def has_fetched(self, movie_number: str) -> bool:
        key = self._key(movie_number)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM movie_fetch_state WHERE movie_number = ? LIMIT 1",
                (key,),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def mark_fetched(self, movie_number: str, fetched_at: datetime) -> None:
        key = self._key(movie_number)
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO movie_fetch_state (movie_number, last_fetched_at)
                VALUES (?, ?)
                ON CONFLICT(movie_number) DO UPDATE SET
                    last_fetched_at = excluded.last_fetched_at
                """,
                (key, fetched_at.isoformat()),
            )
            connection.commit()
        finally:
            connection.close()
