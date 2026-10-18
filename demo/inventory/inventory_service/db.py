"""Data layer for the inventory service.

Deliberately boring, and deliberately honest about isolation:

* without ``for_update`` a session runs in SQLite autocommit mode, so a
  read-modify-write is **not** atomic -- this is the production bug;
* with ``for_update=True`` the session opens ``BEGIN IMMEDIATE``, taking the
  database write lock for the whole critical section, so a second mutator
  blocks and then reads *fresh* state.

SQLite has no ``SELECT ... FOR UPDATE``. ``BEGIN IMMEDIATE`` is the closest
real equivalent, and the trade-off (it is a database-wide write lock, and this
service holds it across a network call) is called out in the README rather than
papered over.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager

from . import observ
from .observ import current_span, get_telemetry, in_span, row_lock

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    id                   INTEGER PRIMARY KEY,
    sku                  TEXT    NOT NULL,
    available            INTEGER NOT NULL DEFAULT 0,
    status               TEXT    NOT NULL DEFAULT 'QUARANTINED',
    expires_at           TEXT,
    last_event_id        TEXT,
    version              INTEGER NOT NULL DEFAULT 0,
    receipt_applications INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reservations (
    id         INTEGER PRIMARY KEY,
    stock_id   INTEGER NOT NULL,
    quantity   INTEGER NOT NULL,
    created_at TEXT
);
"""

# Fields whose writes are worth a StateMutation node, per model.
TRACKED_FIELDS: dict[str, tuple[str, ...]] = {
    "stocks": ("available", "status", "last_event_id", "receipt_applications"),
    "reservations": ("quantity",),
}

_schema_cache: dict[str, tuple[str, str, frozenset[str]]] = {}
_schema_lock = threading.Lock()


def _table_shape(model: str) -> tuple[str, str, frozenset[str]]:
    """``(primary_key_column, table_name, column_names)`` for one model.

    Cached per process: ``PRAGMA table_info`` on every dirty write would be an
    observable cost inside the critical section the race is measured against.
    """
    with _schema_lock:
        cached = _schema_cache.get(model)
    if cached is not None:
        return cached
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA)
        info = conn.execute(f"PRAGMA table_info({model})").fetchall()
    finally:
        conn.close()
    if not info:
        raise LookupError(f"{model} is not in the schema")
    pk = next(row[1] for row in info if row[5])
    shape = (pk, model, frozenset(row[1] for row in info))
    with _schema_lock:
        _schema_cache[model] = shape
    return shape


class Row:
    """A tiny mutable row object; dirty tracking drives the write telemetry."""

    __slots__ = ("_values", "_dirty", "_model")

    def __init__(self, model: str, values: dict):
        object.__setattr__(self, "_values", dict(values))
        object.__setattr__(self, "_dirty", [])
        object.__setattr__(self, "_model", model)

    def __getattr__(self, item):
        try:
            return object.__getattribute__(self, "_values")[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def __setattr__(self, key, value):
        tracked = TRACKED_FIELDS.get(self._model, ())
        if key in tracked:
            old = self._values.get(key)
            if old != value:
                change = (key, old, value)
                if change not in self._dirty:
                    self._dirty.append(change)
        self._values[key] = value

    def as_dict(self) -> dict:
        return dict(self._values)

    @property
    def dirty(self) -> list:
        return list(self._dirty)


class Query:
    """The slice of a query builder this service uses."""

    def __init__(self, session: Session, model: str):
        self._session = session
        self._model = model
        self._filters: dict = {}
        self._for_update = False

    def filter_by(self, **kwargs) -> Query:
        self._filters.update(kwargs)
        return self

    def with_for_update(self) -> Query:
        self._for_update = True
        return self

    def one(self) -> Row:
        if self._for_update:
            # Escalate the session to a write transaction if it is not one.
            self._session.ensure_row_lock()
        filters = self._resolved_filters()
        where = " AND ".join(f"{k} = ?" for k in filters)
        sql = f"SELECT * FROM {self._model}"
        params: list = []
        if where:
            sql += f" WHERE {where}"
            params.extend(filters.values())
        cursor = self._session.conn.execute(sql, params)
        found = cursor.fetchone()
        if found is None:
            raise LookupError(f"{self._model} {self._filters} not found")
        row = Row(self._model, dict(found))
        self._session._tracked.append(row)
        self._session._observe_read(self._model, row.as_dict(),
                                    lock_held=self._for_update or self._session.for_update)
        return row

    def _resolved_filters(self) -> dict:
        """``filter_by(id=...)`` means the primary key, whatever it is named."""
        pk, _table, columns = _table_shape(self._model)
        if "id" not in self._filters or "id" in columns:
            return dict(self._filters)
        filters = dict(self._filters)
        filters[pk] = filters.pop("id")
        return filters

    def all(self) -> list[Row]:
        cursor = self._session.conn.execute(f"SELECT * FROM {self._model}")
        out = []
        for found in cursor.fetchall():
            row = Row(self._model, dict(found))
            self._session._tracked.append(row)
            self._session._observe_read(self._model, row.as_dict(),
                                        lock_held=self._session.for_update)
            out.append(row)
        return out

    def create(self, **values) -> Row:
        row = Row(self._model, values)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self._session.conn.execute(
            f"INSERT INTO {self._model} ({cols}) VALUES ({marks})", list(values.values())
        )
        return row


class Session:
    """One unit of work. Isolations: autocommit vs BEGIN IMMEDIATE."""

    def __init__(self, conn: sqlite3.Connection, for_update: bool):
        self.conn = conn
        self.for_update = for_update
        self._tracked: list[Row] = []
        self._in_tx = False

    # -- query API --------------------------------------------------------
    def query(self, model: str) -> Query:
        return Query(self, model)

    def get(self, model: str, pk: int) -> Row:
        return Query(self, model).filter_by(id=pk).one()

    # -- transaction control ---------------------------------------------
    def ensure_row_lock(self) -> None:
        if self._in_tx or self.conn.in_transaction:
            self._in_tx = True
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._in_tx = True

    def commit(self) -> None:
        for row in self._tracked:
            values = row.as_dict()
            pk_column, _table, columns = _table_shape(row._model)
            pk_value = values.get(pk_column)
            for field, old, new in row.dirty:
                sql = f"UPDATE {row._model} SET {field} = ?"
                if "version" in columns:
                    sql += ", version = version + 1"
                self.conn.execute(f"{sql} WHERE {pk_column} = ?", (new, pk_value))
                self._observe_write(row._model, values, field, old, new)
        if self._in_tx:
            self.conn.execute("COMMIT")
            self._in_tx = False

    def rollback(self) -> None:
        if self._in_tx:
            self.conn.execute("ROLLBACK")
            self._in_tx = False

    # -- internals --------------------------------------------------------
    def _observe_read(self, model: str, values: dict, lock_held: bool) -> None:
        telemetry, span = get_telemetry(), current_span()
        if not telemetry or span is None:
            return
        from trace_repro.models import StateEntity

        pk_column, _table, _columns = _table_shape(model)
        pk = values.get(pk_column)
        if pk is None:
            return
        telemetry.record_read(span, StateEntity(model, str(pk)), lock_held=lock_held)

    def _observe_write(self, model: str, values: dict, field: str, old, new) -> None:
        telemetry, span = get_telemetry(), current_span()
        if not telemetry or span is None:
            return
        from trace_repro.models import StateEntity

        pk_column, _table, _columns = _table_shape(model)
        telemetry.record_write(
            span,
            StateEntity(model, str(values.get(pk_column))),
            field,
            old,
            new,
            lock_held=self.for_update,
        )


class Database:
    """Per-thread SQLite connections with a shared file."""

    def __init__(self, path: str = ":memory:", busy_timeout_ms: int = 5000):
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        self._local = threading.local()
        self._shared: sqlite3.Connection | None = None
        if path == ":memory:":
            self._shared = self._new_conn()

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000.0,
                               isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_conn()
            self._local.conn = conn
        return conn

    def migrate(self) -> None:
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def session(self, for_update: bool = False):
        """``with db.session()`` (the bug) vs ``with db.session(for_update=True)`` (the fix).

        Concurrency note: SQLite gives one connection per thread only when a
        file backs the database. ``:memory:`` shares a single connection and
        therefore cannot express concurrency at all -- always use a temp file
        when you need genuine concurrency.
        """
        session = Session(self.conn, for_update=for_update)
        try:
            with row_lock(for_update):
                yield session
        except BaseException:
            session.rollback()
            raise
        finally:
            session.rollback()


def sleep_ms(ms: float) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


__all__ = ["Database", "Query", "Row", "Session", "sleep_ms", "in_span", "observ"]
