"""Хранилище: настройки напоминаний и прогресс карточек (SQLite)."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

INTERVALS = [1, 2, 4, 8, 16, 32]  # дни до следующего показа по ящикам 1..6


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                remind    TEXT,
                last_sent TEXT
            );
            CREATE TABLE IF NOT EXISTS cards (
                user_id INTEGER NOT NULL,
                key     TEXT    NOT NULL,
                box     INTEGER NOT NULL,
                due     TEXT    NOT NULL,
                PRIMARY KEY (user_id, key)
            );
            """
        )
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}
        if "part" not in cols:  # миграция: выбранная часть книги
            self.conn.execute("ALTER TABLE users ADD COLUMN part INTEGER")
        self.conn.commit()

    # ----- пользователи и напоминания -----
    def ensure_user(self, uid: int) -> None:
        self.conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
        self.conn.commit()

    def get_part(self, uid: int) -> int | None:
        row = self.conn.execute("SELECT part FROM users WHERE user_id=?", (uid,)).fetchone()
        return row["part"] if row else None

    def set_part(self, uid: int, part: int) -> None:
        self.ensure_user(uid)
        self.conn.execute("UPDATE users SET part=? WHERE user_id=?", (part, uid))
        self.conn.commit()

    def get_remind(self, uid: int) -> str | None:
        row = self.conn.execute("SELECT remind FROM users WHERE user_id=?", (uid,)).fetchone()
        return row["remind"] if row else None

    def set_remind(self, uid: int, hhmm: str | None) -> None:
        self.ensure_user(uid)
        self.conn.execute("UPDATE users SET remind=?, last_sent=NULL WHERE user_id=?", (hhmm, uid))
        self.conn.commit()

    def users_with_reminders(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT user_id, remind, last_sent FROM users WHERE remind IS NOT NULL").fetchall()

    def mark_sent(self, uid: int, day: str) -> None:
        self.conn.execute("UPDATE users SET last_sent=? WHERE user_id=?", (day, uid))
        self.conn.commit()

    # ----- карточки -----
    def cards_state(self, uid: int) -> dict[str, tuple[int, str]]:
        rows = self.conn.execute("SELECT key, box, due FROM cards WHERE user_id=?", (uid,)).fetchall()
        return {r["key"]: (r["box"], r["due"]) for r in rows}

    def review(self, uid: int, key: str, known: bool, today: date) -> None:
        row = self.conn.execute("SELECT box FROM cards WHERE user_id=? AND key=?", (uid, key)).fetchone()
        box = row["box"] if row else 0
        if known:
            box = min(box + 1, len(INTERVALS))
            due = today + timedelta(days=INTERVALS[box - 1])
        else:
            box, due = 0, today
        self.conn.execute(
            "INSERT INTO cards (user_id, key, box, due) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET box=excluded.box, due=excluded.due",
            (uid, key, box, due.isoformat()),
        )
        self.conn.commit()
