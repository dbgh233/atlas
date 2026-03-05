"""SQLite database manager with migration support for Atlas."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
from starlette.requests import Request

logger = logging.getLogger(__name__)

# Path to migrations directory (project root / migrations)
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


class Database:
    """Async SQLite database manager with WAL mode and auto-migration."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def connect(self) -> aiosqlite.Connection:
        """Open a connection with WAL mode and Row factory enabled."""
        # Ensure parent directory exists (handles Railway volume mount timing)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        logger.info("Database connected: %s (WAL mode)", self.db_path)
        return db

    @classmethod
    async def run_migrations(cls, db: aiosqlite.Connection) -> None:
        """Apply pending SQL migrations from the migrations/ directory.

        Migrations are tracked in a ``_migrations`` table so they run
        exactly once, even across restarts.
        """
        # 1. Ensure tracking table exists
        await db.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "  name TEXT PRIMARY KEY,"
            "  applied_at TEXT DEFAULT (datetime('now'))"
            ")"
        )
        await db.commit()

        # 2. Read already-applied migrations
        cursor = await db.execute("SELECT name FROM _migrations")
        applied = {row[0] for row in await cursor.fetchall()}

        # 3. Scan migrations/ for *.sql files, sorted by name
        if not MIGRATIONS_DIR.is_dir():
            logger.warning("Migrations directory not found: %s", MIGRATIONS_DIR)
            return

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

        # 4. Apply each unapplied migration
        for mig_path in migration_files:
            if mig_path.name in applied:
                logger.debug("Migration already applied: %s", mig_path.name)
                continue

            sql = mig_path.read_text(encoding="utf-8")
            await db.executescript(sql)
            await db.execute(
                "INSERT INTO _migrations (name) VALUES (?)", (mig_path.name,)
            )
            await db.commit()
            logger.info("Migration applied: %s", mig_path.name)

    @staticmethod
    async def close(db: aiosqlite.Connection) -> None:
        """Close the database connection."""
        await db.close()
        logger.info("Database connection closed")


def get_db(request: Request) -> aiosqlite.Connection:
    """FastAPI dependency: retrieve the database connection from app state."""
    return request.app.state.db
