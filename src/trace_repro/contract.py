"""The contract a target repository publishes so trace-repro can drive it.

``trace-repro`` is not supposed to know what a checkout service, an inventory
service or a payments service look like. What it *does* need from a repository is
small and completely declarative:

* which entity the incident contends (``orders`` / ``8812``)
* three entry points, by kind, as ``module:callable`` routes
* the payloads and the millisecond offsets they ran at
* the seed row the replay starts from
* the assertions a correct run must satisfy

That is ``incident.json``, at the repository root. It is JSON on purpose: the
generated reproduction test runs in a repository where trace-repro is *not*
installed, so it has to be able to read the contract with nothing but the
standard library.

The alternative -- a scenario registry inside trace-repro -- would put demo
specifics in the product, and would mean the tool only ever works on the
repositories it already knows about.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CONTRACT_FILENAME = "incident.json"

#: Every entry point is dispatched by kind, so the same three names cover every
#: repository: an inbound callback, a scheduled job, and an ordinary request.
KINDS = ("webhook", "worker", "request")

_REQUIRED_FIELDS = (
    "key",
    "title",
    "service",
    "package",
    "entity_type",
    "primary_key",
    "crash_endpoint",
    "outcome_field",
    "seed",
    "routes",
    "participants",
)


class ContractError(ValueError):
    """The repository's ``incident.json`` is missing, malformed or incomplete."""


@dataclass(frozen=True)
class Participant:
    """One replayable request, exactly as it was recorded in production."""

    kind: str
    endpoint: str
    payload: dict = field(default_factory=dict)
    offset_ms: float = 0.0
    route: str = ""

    @property
    def name(self) -> str:
        return self.endpoint

    def with_offset(self, offset_ms: float) -> Participant:
        return Participant(
            kind=self.kind,
            endpoint=self.endpoint,
            payload=dict(self.payload),
            offset_ms=offset_ms,
            route=self.route,
        )


@dataclass(frozen=True)
class IncidentSpec:
    """A parsed ``incident.json``."""

    key: str
    title: str
    service: str
    package: str
    entity_type: str
    primary_key: str
    crash_endpoint: str
    outcome_field: str
    seed: dict[str, dict]
    routes: dict[str, str]
    participants: tuple[Participant, ...]
    assertions: tuple[str, ...] = ()
    duplicate_participants: tuple[Participant, ...] = ()
    duplicate_assertions: tuple[str, ...] = ()
    notes: str = ""

    @property
    def incident_id(self) -> str:
        return f"incident_{self.primary_key}"

    @property
    def entity_key(self) -> str:
        return f"{self.entity_type.lower()}:{self.primary_key}"

    @property
    def offsets_ms(self) -> tuple[float, ...]:
        return tuple(p.offset_ms for p in self.participants)

    def route_for(self, kind: str) -> str:
        route = self.routes.get(kind, "")
        if not route:
            raise ContractError(
                f"incident.json declares no route for kind {kind!r}; "
                f"known kinds: {sorted(self.routes)}"
            )
        return route

    def participant_of_kind(self, kind: str) -> Participant | None:
        for participant in self.participants:
            if participant.kind == kind:
                return participant
        return None

    @property
    def crash_participant(self) -> Participant | None:
        for participant in self.participants:
            if participant.endpoint == self.crash_endpoint:
                return participant
        return self.participants[-1] if self.participants else None


def contract_path(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / CONTRACT_FILENAME


def read_contract_data(repo_root: str | Path) -> dict | None:
    """The raw JSON, or ``None`` when the repository does not publish one."""
    path = contract_path(repo_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path} is not valid JSON: {exc}") from exc


def _participant(raw: dict, index: int, route: str) -> Participant:
    if not isinstance(raw, dict):
        raise ContractError(f"participants[{index}] must be an object")
    kind = raw.get("kind", "")
    if kind not in KINDS:
        raise ContractError(
            f"participants[{index}].kind must be one of {KINDS}, got {kind!r}"
        )
    endpoint = raw.get("endpoint", "")
    if not endpoint:
        raise ContractError(f"participants[{index}] has no endpoint")
    payload = raw.get("payload", {})
    if not isinstance(payload, dict):
        raise ContractError(f"participants[{index}].payload must be an object")
    return Participant(
        kind=kind,
        endpoint=endpoint,
        payload=payload,
        offset_ms=float(raw.get("offset_ms", 0.0)),
        route=raw.get("route", route),
    )


def parse_contract(data: dict) -> IncidentSpec:
    """Validate the raw document and turn it into an :class:`IncidentSpec`."""
    if not isinstance(data, dict):
        raise ContractError("incident.json must contain a JSON object")

    missing = [name for name in _REQUIRED_FIELDS if name not in data]
    if missing:
        raise ContractError(
            "incident.json is missing required field(s): " + ", ".join(missing)
        )

    routes = data.get("routes") or {}
    if not isinstance(routes, dict) or not routes:
        raise ContractError("incident.json 'routes' must be a non-empty object")

    raw_participants = data.get("participants") or []
    if len(raw_participants) < 2:
        raise ContractError(
            "a cross-trace incident needs at least two participants; "
            f"got {len(raw_participants)}"
        )
    participants = tuple(
        _participant(raw, i, routes.get(raw.get("kind", ""), ""))
        for i, raw in enumerate(raw_participants)
    )

    duplicates = data.get("duplicate_participants") or []
    duplicate_participants = tuple(
        _participant(raw, i, routes.get(raw.get("kind", ""), ""))
        for i, raw in enumerate(duplicates)
    )

    seed = data.get("seed") or {}
    if not isinstance(seed, dict) or not seed:
        raise ContractError("incident.json 'seed' must map a table to a row")

    offsets = [p.offset_ms for p in participants]
    if offsets != sorted(offsets):
        raise ContractError(
            "participants must be listed in the order they start: "
            f"offsets {offsets} are not ascending"
        )

    return IncidentSpec(
        key=data["key"],
        title=data["title"],
        service=data["service"],
        package=data["package"],
        entity_type=data["entity_type"],
        primary_key=str(data["primary_key"]),
        crash_endpoint=data["crash_endpoint"],
        outcome_field=data.get("outcome_field", "status"),
        seed=seed,
        routes=dict(routes),
        participants=participants,
        assertions=tuple(data.get("assertions", ())),
        duplicate_participants=duplicate_participants,
        duplicate_assertions=tuple(data.get("duplicate_assertions", ())),
        notes=data.get("notes", ""),
    )


def load_contract(repo_root: str | Path) -> IncidentSpec:
    """Read and validate ``<repo_root>/incident.json``.

    A repository without a contract cannot be driven: there is nothing telling
    trace-repro which entry points to call or which row to seed. Failing loudly
    here is better than importing a service package and guessing.
    """
    data = read_contract_data(repo_root)
    if data is None:
        path = contract_path(repo_root)
        raise ContractError(
            f"{path} not found. trace-repro drives a repository through its "
            f"{CONTRACT_FILENAME}: it names the contended entity, the entry "
            "points, the recorded offsets and the assertions a correct run must "
            "satisfy."
        )
    return parse_contract(data)


def spec_for(repo_root: str | Path) -> IncidentSpec | None:
    """The contract, or ``None`` when the repository does not publish one."""
    data = read_contract_data(repo_root)
    return parse_contract(data) if data is not None else None


__all__ = [
    "CONTRACT_FILENAME",
    "KINDS",
    "ContractError",
    "IncidentSpec",
    "Participant",
    "contract_path",
    "load_contract",
    "parse_contract",
    "read_contract_data",
    "spec_for",
]
