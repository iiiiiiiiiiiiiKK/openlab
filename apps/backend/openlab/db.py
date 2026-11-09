"""Database session management and the SQL migration runner.

SQLite is configured in WAL mode with a generous busy timeout because the task
scheduler, websocket handlers and HTTP requests all touch the same file.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

log = logging.getLogger("openlab.db")

_MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # pragma: no cover
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=8000")
    finally:
        cursor.close()


def init_engine(db_path: Path | str, echo: bool = False) -> Engine:
    global _engine, _SessionLocal
    url = f"sqlite+pysqlite:///{db_path}"
    _engine = create_engine(
        url,
        echo=echo,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    event.listen(_engine, "connect", _apply_sqlite_pragmas)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database engine not initialised")
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        raise RuntimeError("database engine not initialised")
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def migrations_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "migrations"


def applied_migrations(engine: Engine) -> set[str]:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        )
        rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def discover_migrations(directory: Path | None = None) -> list[Path]:
    directory = directory or migrations_dir()
    if not directory.exists():
        return []
    files = [p for p in directory.iterdir() if p.is_file() and _MIGRATION_RE.match(p.name)]
    return sorted(files, key=lambda p: p.name)


def run_migrations(engine: Engine, directory: Path | None = None) -> list[str]:
    """Apply pending SQL migrations in filename order and return their versions."""
    done = applied_migrations(engine)
    applied: list[str] = []
    for path in discover_migrations(directory):
        version = path.stem
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        log.info("applying migration %s", version)
        with engine.begin() as conn:
            for statement in _split_statements(sql):
                conn.execute(text(statement))
            conn.execute(
                text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, datetime('now'))"),
                {"v": version},
            )
        applied.append(version)
    return applied


def _split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buffer.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(buffer).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            buffer = []
    tail = "\n".join(buffer).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


def init_database(db_path: Path | str, auto_migrate: bool = True) -> Engine:
    """Create the engine, run migrations and make sure model tables exist."""
    engine = init_engine(db_path)
    if auto_migrate:
        try:
            run_migrations(engine)
        except Exception as exc:  # pragma: no cover - fall back to metadata
            log.warning("migration runner failed (%s); using metadata.create_all", exc)
    Base.metadata.create_all(engine)
    return engine


def ensure_indexes(engine: Engine) -> None:  # pragma: no cover - convenience
    Base.metadata.create_all(engine)


def dump_schema(db_path: Path) -> str:  # pragma: no cover - tooling helper
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    rows = engine.connect().execute(text("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")).fetchall()
    return ";\n".join(r[0] for r in rows) + ";"
