"""Canonical entity state and append-only business events in SQLite.

Ingestion resolves source records into typed entities and events; this module
persists them under ``<workspace>/.ontology/canon.db``:

- ``entities`` — current state per ``(entity_type, entity_id)``. A later
  observation upserts the property set; the entity row is a read model of
  "what we believe now", not history.
- ``events`` — append-only business facts. A fact key derives from the event
  type, subject, and ``occurred_at`` only, never from ingestion time, so
  re-ingesting a snapshot cannot duplicate an event. ``UPDATE``/``DELETE``
  triggers make the append-only rule structural: history cannot be silently
  rewritten, and a source omission never deletes a recorded fact.
- ``entity_observations`` / ``event_observations`` — links from every
  accepted observation to the entities it updated and the facts it asserted,
  including repeat observations of an already-known fact.
- ``projection_runs`` — frozen projection results (see projections.py), also
  append-only.

One ingest run applies in a single transaction: entity upserts, event
inserts, and observation links commit together or not at all. v0.1 assumes a
single writer per workspace, matching the evidence store.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

CANON_DB = "canon.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    properties TEXT NOT NULL,
    first_observation_id TEXT NOT NULL,
    last_observation_id TEXT NOT NULL,
    last_ingest_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS entity_observations (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    ingest_id TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, observation_id)
);
CREATE TABLE IF NOT EXISTS events (
    fact_key TEXT PRIMARY KEY,
    event TEXT NOT NULL,
    subject_entity TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    first_observation_id TEXT NOT NULL,
    ingest_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_subject ON events (subject_entity, subject_id, event);
CREATE TABLE IF NOT EXISTS event_observations (
    fact_key TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    ingest_id TEXT NOT NULL,
    PRIMARY KEY (fact_key, observation_id)
);
CREATE TABLE IF NOT EXISTS projection_runs (
    run_id TEXT PRIMARY KEY,
    projection TEXT NOT NULL,
    subject TEXT NOT NULL,
    as_of TEXT NOT NULL,
    created_at TEXT NOT NULL,
    definition_digest TEXT NOT NULL,
    inputs TEXT NOT NULL,
    candidates TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'canonical events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'canonical events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS projection_runs_no_update
BEFORE UPDATE ON projection_runs
BEGIN SELECT RAISE(ABORT, 'projection runs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS projection_runs_no_delete
BEFORE DELETE ON projection_runs
BEGIN SELECT RAISE(ABORT, 'projection runs are append-only'); END;
"""


def event_fact_key(
    event: str, subject_entity: str, subject_id: str, occurred_at: datetime
) -> str:
    """Stable identity of one business fact.

    The key covers what the fact *is* — event type, subject, and occurrence
    time — and deliberately excludes ``observed_at`` and ingest/run ids, so
    observing the same fact twice never mints a second event.
    """
    payload = "\n".join(
        ("v1", event, subject_entity, subject_id, _iso_z(occurred_at))
    )
    return "fact-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class CanonicalEvent:
    """One persisted business fact."""

    fact_key: str
    event: str
    subject_entity: str
    subject_id: str
    occurred_at: datetime
    observed_at: datetime
    first_observation_id: str
    ingest_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_key": self.fact_key,
            "event": self.event,
            "subject": {"entity": self.subject_entity, "id": self.subject_id},
            "occurred_at": _iso_z(self.occurred_at),
            "observed_at": _iso_z(self.observed_at),
            "first_observation_id": self.first_observation_id,
            "ingest_id": self.ingest_id,
        }


@dataclass
class ApplyResult:
    """What one ingest run changed in the canonical store."""

    entities_upserted: int = 0
    entity_links: int = 0
    events_appended: int = 0
    events_reobserved: int = 0
    appended: list[CanonicalEvent] = field(default_factory=list)


class CanonicalStore(Protocol):
    """Narrow persistence boundary for canonical state and frozen runs."""

    def apply(self, ingest_id: str, observations: Iterable[Any]) -> ApplyResult:
        """Transactionally apply one run's accepted observations."""
        ...

    def entity_state(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        """Current properties and provenance for one entity, or None."""
        ...

    def subject_ids_with_event(self, event: str, subject_entity: str) -> list[str]:
        """Distinct subject ids having at least one event of ``event``."""
        ...

    def events_for_subject(
        self, subject_entity: str, subject_id: str
    ) -> list[CanonicalEvent]:
        """All recorded facts about one subject, ordered by occurred_at."""
        ...

    def event_links(self, fact_key: str) -> list[tuple[str, str]]:
        """``(observation_id, ingest_id)`` pairs that asserted this fact."""
        ...

    def record_projection_run(
        self,
        *,
        run_id: str,
        projection: str,
        subject: str,
        as_of: datetime,
        created_at: datetime,
        definition_digest: str,
        inputs: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> None:
        """Freeze one projection run; runs are append-only."""
        ...

    def projection_run(self, run_id: str) -> dict[str, Any] | None:
        """A frozen run record with parsed inputs/candidates, or None."""
        ...


class SqliteCanonicalStore:
    """SQLite canonical store rooted at a workspace runtime directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.db_path = self.root / CANON_DB
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.root.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            self._conn = conn
        return self._conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after the first canon.db schema.

        ``CREATE TABLE IF NOT EXISTS`` does not retrofit columns onto a
        database created by an earlier revision, so additive changes are
        applied explicitly.
        """
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(entities)")
        }
        if "last_ingest_id" not in columns:
            conn.execute(
                "ALTER TABLE entities ADD COLUMN last_ingest_id "
                "TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def apply(self, ingest_id: str, observations: Iterable[Any]) -> ApplyResult:
        conn = self._connect()
        result = ApplyResult()
        # ``with conn:`` commits on success and rolls back on any exception,
        # so a failed run never leaves half-written canonical state.
        with conn:
            for observation in observations:
                for entity in observation.entities:
                    conn.execute(
                        """
                        INSERT INTO entities (
                            entity_type, entity_id, properties,
                            first_observation_id, last_observation_id,
                            last_ingest_id, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
                            properties = excluded.properties,
                            last_observation_id = excluded.last_observation_id,
                            last_ingest_id = excluded.last_ingest_id,
                            updated_at = excluded.updated_at
                        WHERE excluded.updated_at > entities.updated_at
                            OR (
                                excluded.updated_at = entities.updated_at
                                AND excluded.last_ingest_id > entities.last_ingest_id
                            )
                        """,
                        (
                            entity.entity,
                            entity.id,
                            json.dumps(
                                {k: _jsonable(v) for k, v in entity.properties.items()},
                                sort_keys=True,
                            ),
                            observation.observation_id,
                            observation.observation_id,
                            ingest_id,
                            _iso_z(observation.observed_at),
                        ),
                    )
                    result.entities_upserted += 1
                    result.entity_links += conn.execute(
                        """
                        INSERT OR IGNORE INTO entity_observations (
                            entity_type, entity_id, observation_id, ingest_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            entity.entity,
                            entity.id,
                            observation.observation_id,
                            ingest_id,
                        ),
                    ).rowcount
                for event in observation.events:
                    fact_key = event_fact_key(
                        event.event,
                        event.subject_entity,
                        event.subject_id,
                        event.occurred_at,
                    )
                    inserted = conn.execute(
                        """
                        INSERT OR IGNORE INTO events (
                            fact_key, event, subject_entity, subject_id,
                            occurred_at, observed_at, first_observation_id, ingest_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fact_key,
                            event.event,
                            event.subject_entity,
                            event.subject_id,
                            _iso_z(event.occurred_at),
                            _iso_z(event.observed_at),
                            observation.observation_id,
                            ingest_id,
                        ),
                    ).rowcount == 1
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO event_observations (
                            fact_key, observation_id, ingest_id
                        ) VALUES (?, ?, ?)
                        """,
                        (fact_key, observation.observation_id, ingest_id),
                    )
                    if inserted:
                        result.events_appended += 1
                        result.appended.append(
                            CanonicalEvent(
                                fact_key=fact_key,
                                event=event.event,
                                subject_entity=event.subject_entity,
                                subject_id=event.subject_id,
                                occurred_at=event.occurred_at,
                                observed_at=event.observed_at,
                                first_observation_id=observation.observation_id,
                                ingest_id=ingest_id,
                            )
                        )
                    else:
                        result.events_reobserved += 1
        return result

    def _has_db(self) -> bool:
        """True when a connection or an existing db file is available.

        Read paths check this before connecting so that inspection (for
        example ``project --run``) never creates ``.ontology/canon.db`` on a
        workspace that has not ingested anything.
        """
        return self._conn is not None or self.db_path.exists()

    def entity_state(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        if not self._has_db():
            return None
        row = self._connect().execute(
            """
            SELECT properties, first_observation_id, last_observation_id, updated_at
            FROM entities WHERE entity_type = ? AND entity_id = ?
            """,
            (entity_type, entity_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "properties": json.loads(row[0]),
            "first_observation_id": row[1],
            "last_observation_id": row[2],
            "updated_at": row[3],
        }

    def subject_ids_with_event(self, event: str, subject_entity: str) -> list[str]:
        if not self._has_db():
            return []
        rows = self._connect().execute(
            """
            SELECT DISTINCT subject_id FROM events
            WHERE event = ? AND subject_entity = ? ORDER BY subject_id
            """,
            (event, subject_entity),
        ).fetchall()
        return [row[0] for row in rows]

    def events_for_subject(
        self, subject_entity: str, subject_id: str
    ) -> list[CanonicalEvent]:
        if not self._has_db():
            return []
        rows = self._connect().execute(
            """
            SELECT fact_key, event, occurred_at, observed_at,
                   first_observation_id, ingest_id
            FROM events
            WHERE subject_entity = ? AND subject_id = ?
            ORDER BY occurred_at, fact_key
            """,
            (subject_entity, subject_id),
        ).fetchall()
        return [
            CanonicalEvent(
                fact_key=row[0],
                event=row[1],
                subject_entity=subject_entity,
                subject_id=subject_id,
                occurred_at=datetime.fromisoformat(row[2]),
                observed_at=datetime.fromisoformat(row[3]),
                first_observation_id=row[4],
                ingest_id=row[5],
            )
            for row in rows
        ]

    def event_links(self, fact_key: str) -> list[tuple[str, str]]:
        if not self._has_db():
            return []
        rows = self._connect().execute(
            """
            SELECT observation_id, ingest_id FROM event_observations
            WHERE fact_key = ? ORDER BY observation_id
            """,
            (fact_key,),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def record_projection_run(
        self,
        *,
        run_id: str,
        projection: str,
        subject: str,
        as_of: datetime,
        created_at: datetime,
        definition_digest: str,
        inputs: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> None:
        conn = self._connect()
        with conn:
            conn.execute(
                """
                INSERT INTO projection_runs (
                    run_id, projection, subject, as_of, created_at,
                    definition_digest, inputs, candidates
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    projection,
                    subject,
                    _iso_z(as_of),
                    _iso_z(created_at),
                    definition_digest,
                    json.dumps(inputs, sort_keys=True),
                    json.dumps(candidates, sort_keys=True),
                ),
            )

    def projection_run(self, run_id: str) -> dict[str, Any] | None:
        if not self._has_db():
            return None
        row = self._connect().execute(
            """
            SELECT projection, subject, as_of, created_at,
                   definition_digest, inputs, candidates
            FROM projection_runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": run_id,
            "projection": row[0],
            "subject": row[1],
            "as_of": row[2],
            "created_at": row[3],
            "definition_digest": row[4],
            "inputs": json.loads(row[5]),
            "candidates": json.loads(row[6]),
        }


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso_z(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


__all__ = [
    "ApplyResult",
    "CANON_DB",
    "CanonicalEvent",
    "CanonicalStore",
    "SqliteCanonicalStore",
    "event_fact_key",
]
