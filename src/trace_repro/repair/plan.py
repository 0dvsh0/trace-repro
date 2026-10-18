"""Plan a source-level repair for an upstream mutator.

Everything here is driven by :mod:`ast`. No line-number guessing, no regex over
prose, no LLM. The planner reads a real function, decides which of three
canonical concurrency repairs it needs, and emits precise, individually
justified edits:

``session_isolation``
    Escalate the unit of work to a locking transaction (``SELECT ... FOR
    UPDATE`` semantics). This is the actual fix for a lost update -- and it
    only counts once **every** writer of the row takes the lock.

``row_lock``
    Mark the entity load as lock-acquiring.

``idempotency_guard``
    Refuse to re-apply an event that has already been applied. A retried
    webhook is not a race, it is an at-least-once delivery problem, and it
    corrupts data even after the lock lands.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

DEDUP_FIELD = "last_event_id"


@dataclass(slots=True)
class FixEdit:
    """One surgical, reviewable change to one line range of one file."""

    strategy: str
    file_path: str
    line: int                 # 1-based, anchor line for reporting/diffing
    before: str
    after: str
    rationale: str
    insert_after: int = 0     # >0 means "insert `after` lines after `line`"
    insert_indent: int = 4

    @property
    def is_insert(self) -> bool:
        return bool(self.insert_after)


@dataclass(slots=True)
class FunctionPlan:
    file_path: str
    function_name: str
    qualified_name: str
    edits: list[FixEdit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.edits


class RepairPlanner:
    """Turns one function's source into a list of concurrency repairs."""

    def __init__(self, file_path: str, source: str | None = None):
        self.file_path = str(Path(file_path).resolve())
        self.source = source if source is not None else Path(self.file_path).read_text("utf-8")
        self.lines = self.source.splitlines()
        self.tree = ast.parse(self.source, filename=self.file_path)

    # -- lookup -----------------------------------------------------------
    def find_function(self, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        return None

    # -- planning ---------------------------------------------------------
    def plan(self, function_name: str) -> FunctionPlan:
        fn = self.find_function(function_name)
        if fn is None:
            raise LookupError(f"function {function_name!r} not found in {self.file_path}")
        plan = FunctionPlan(
            file_path=self.file_path,
            function_name=function_name,
            qualified_name=function_name,
        )
        already_locked = self._has_for_update(fn)
        plan.notes.append(
            "row lock already present" if already_locked else "no row lock detected"
        )

        session_edit = self._plan_session_isolation(fn, already_locked)
        if session_edit:
            plan.edits.append(session_edit)

        lock_edit = self._plan_row_lock(fn, already_locked)
        if lock_edit:
            plan.edits.append(lock_edit)

        guard_edit = self._plan_idempotency_guard(fn)
        if guard_edit:
            plan.edits.append(guard_edit)

        django_lock = self._plan_django_row_lock(fn, already_locked or self._has_django_lock(fn))
        if django_lock:
            plan.edits.append(django_lock)

        orm_guard = self._plan_orm_idempotency_guard(fn)
        if orm_guard:
            plan.edits.append(orm_guard)

        if not plan.edits:
            plan.notes.append("nothing to repair")
        return plan

    # -- strategies -------------------------------------------------------
    @staticmethod
    def _has_for_update(fn) -> bool:
        return any(
            isinstance(n, ast.keyword) and n.arg == "for_update" for n in ast.walk(fn)
        ) or any(
            isinstance(n, ast.Attribute) and n.attr == "with_for_update" for n in ast.walk(fn)
        )

    @staticmethod
    def _has_django_lock(fn) -> bool:
        return any(
            isinstance(n, ast.Attribute) and n.attr == "select_for_update" for n in ast.walk(fn)
        )

    # -- Django/ORM ------------------------------------------------------

    def _plan_django_row_lock(self, fn, already_locked: bool) -> FixEdit | None:
        """``Order.objects.get(...)`` -> ``...select_for_update().get(...)``

        This is the ORM's spelling of the same repair: load the row with
        ``SELECT ... FOR UPDATE`` so a concurrent writer cannot hand us a stale
        value between the read and the write.

        Only single-line chains are edited, because the edit is anchored to one
        line and a multi-line chain has no single correct insertion point.
        """
        if already_locked:
            return None
        for node in ast.walk(fn):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            if not isinstance(value.func, ast.Attribute):
                continue
            terminal = value.func.attr
            if terminal not in ("get", "first", "last"):
                continue
            if not self._chain_has_orm_manager(value):
                continue
            lineno = value.lineno
            if value.end_lineno != lineno:
                continue
            line = self.lines[lineno - 1]
            needle = f".{terminal}("
            idx = line.rfind(needle)
            if idx == -1:
                continue
            after = line[:idx] + f".select_for_update(){needle}" + line[idx + len(needle):]
            return FixEdit(
                strategy="django_row_lock",
                file_path=self.file_path,
                line=lineno,
                before=line.rstrip("\n"),
                after=after.rstrip("\n"),
                rationale=(
                    "load the row with SELECT ... FOR UPDATE so a concurrent "
                    "writer cannot hand us a stale value before this write"
                ),
            )
        return None

    @staticmethod
    def _chain_has_orm_manager(call: ast.Call) -> bool:
        """Walk a chain like ``Order.objects.filter(...).first()``."""
        node: ast.AST | None = call.func
        while node is not None:
            if isinstance(node, ast.Attribute):
                if node.attr == "objects":
                    return True
                node = node.value
            elif isinstance(node, ast.Call):
                node = node.func
            elif isinstance(node, ast.Name):
                # ``get_model("order", "Order")`` style indirection
                return True
            else:
                return False
        return False

    def _plan_session_isolation(self, fn, already_locked: bool) -> FixEdit | None:
        """``with db.session() as s:`` -> ``with db.session(for_update=True) as s:``"""
        if already_locked:
            return None
        for node in ast.walk(fn):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            for item in node.items:
                call = item.context_expr
                if not isinstance(call, ast.Call):
                    continue
                if not self._is_session_factory(call.func):
                    continue
                if any(k.arg == "for_update" for k in call.keywords):
                    continue
                line_no = call.lineno
                line = self.lines[line_no - 1]
                open_paren = line.find("(", line.find("session"))
                if open_paren == -1:
                    continue
                after = line[: open_paren + 1] + "for_update=True" + line[open_paren + 1 :]
                return FixEdit(
                    strategy="session_isolation",
                    file_path=self.file_path,
                    line=line_no,
                    before=line.rstrip("\n"),
                    after=after.rstrip("\n"),
                    rationale=(
                        "escalate the unit of work to a locking transaction so the "
                        "read-modify-write below is atomic against concurrent writers"
                    ),
                )
        return None

    @staticmethod
    def _is_session_factory(func: ast.AST) -> bool:
        if isinstance(func, ast.Name):
            return func.id == "session"
        if isinstance(func, ast.Attribute):
            return func.attr == "session"
        return False

    def _plan_row_lock(self, fn, already_locked: bool) -> FixEdit | None:
        """``...filter_by(...).one()`` -> ``...filter_by(...).with_for_update().one()``"""
        if already_locked:
            return None
        for node in ast.walk(fn):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
                continue
            value = node.value if hasattr(node, "value") else node
            if not isinstance(value, ast.Call):
                continue
            if not isinstance(value.func, ast.Attribute) or value.func.attr not in (
                "one",
                "first",
            ):
                continue
            if not self._chain_has_query(value):
                continue
            terminal = value.func.attr
            line_no = value.lineno
            if value.end_lineno != line_no:
                # Multi-line chains are out of scope for a line-anchored edit.
                continue
            line = self.lines[line_no - 1]
            needle = f".{terminal}("
            idx = line.rfind(needle)
            if idx == -1:
                continue
            after = line[:idx] + f".with_for_update(){needle}" + line[idx + len(needle) :]
            return FixEdit(
                strategy="row_lock",
                file_path=self.file_path,
                line=line_no,
                before=line.rstrip("\n"),
                after=after.rstrip("\n"),
                rationale=(
                    "load the entity with SELECT ... FOR UPDATE so a concurrent "
                    "writer cannot hand us a stale row"
                ),
            )
        return None

    @staticmethod
    def _chain_has_query(call: ast.Call) -> bool:
        """Walk a fluent chain like ``s.query(..).filter_by(..).one()``."""
        node: ast.AST | None = call.func
        while True:
            if isinstance(node, ast.Attribute):
                if node.attr == "query":
                    return True
                node = node.value
            elif isinstance(node, ast.Call):
                node = node.func
            else:
                return False

    def _plan_idempotency_guard(self, fn) -> FixEdit | None:
        """Refuse to re-apply an event id that has already been applied.

        Detected from the function's own assignment shape: if it writes
        ``order.last_event_id = event.id``, then ``event.id`` is the dedup key.
        """
        load_line: int | None = None
        load_var: str | None = None
        dedup_expr_src: str | None = None

        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "one":
                    if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                        load_line = node.end_lineno
                        load_var = node.targets[0].id
                        continue
            if load_var and isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == DEDUP_FIELD
                        and isinstance(target.value, ast.Name)
                        and target.value.id == load_var
                    ):
                        dedup_expr_src = ast.unparse(node.value)

        if not (load_line and load_var and dedup_expr_src):
            return None

        anchor = self.lines[load_line - 1]
        indent = len(anchor) - len(anchor.lstrip())
        pad = " " * indent

        # Already guarded? Do not stack a second copy.
        guard_head = f"if {load_var}.{DEDUP_FIELD} == {dedup_expr_src}:"
        for existing in self.lines[load_line : load_line + 4]:
            if existing.strip() == guard_head:
                return None

        guard = [
            f"{pad}{guard_head}",
            f"{pad}    return {load_var}.as_dict()",
        ]
        return FixEdit(
            strategy="idempotency_guard",
            file_path=self.file_path,
            line=load_line,
            before=anchor.rstrip("\n"),
            after="\n".join([anchor.rstrip("\n")] + guard),
            rationale=(
                "webhook delivery is at-least-once; skip the write when this event "
                "id was already applied instead of corrupting the order"
            ),
            insert_after=len(guard),
            insert_indent=indent,
        )

    def _plan_orm_idempotency_guard(self, fn) -> FixEdit | None:
        """``Model.objects.create(reference=r, ...)`` -> ``get_or_create(reference=r, defaults=dict(...))``

        A retried webhook is not a race, it is an at-least-once delivery
        problem, and it corrupts data even after the lock lands. Where the
        call already carries the provider's event reference, that reference is
        the idempotency key: the event must apply once, however many times it
        arrives.
        """
        for node in ast.walk(fn):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
                continue
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            if not isinstance(value.func, ast.Attribute):
                continue
            if value.func.attr != "create" or not value.keywords:
                continue
            if not self._chain_has_orm_manager(value):
                continue
            # ``Transaction.objects.create`` -> the manager is
            # ``Transaction.objects``, so the model itself is one hop further.
            manager = value.func.value
            model_node = getattr(manager, "value", None)
            if model_node is None:
                continue
            lineno = value.lineno
            if value.end_lineno != lineno:
                continue

            names = [k.arg for k in value.keywords if k.arg]
            key = next((n for n in ("reference", "event_id", "idempotency_key")
                        if n in names), None)
            if key is None:
                continue

            key_value = next(k.value for k in value.keywords if k.arg == key)
            others = [k for k in value.keywords if k.arg != key]
            defaults = ", ".join(f"{k.arg}={ast.unparse(k.value)}" for k in others)
            original = self.lines[lineno - 1]
            indent = original[: len(original) - len(original.lstrip())]
            replacement = (
                f"{indent}{ast.unparse(model_node)}.objects.get_or_create("
                f"{key}={ast.unparse(key_value)}, defaults=dict({defaults}))"
            )
            return FixEdit(
                strategy="orm_idempotency_guard",
                file_path=self.file_path,
                line=lineno,
                before=self.lines[lineno - 1].rstrip("\n"),
                after=replacement,
                rationale=(
                    "delivery is at-least-once; apply this event only once by "
                    f"keying on the provider's {key}"
                ),
            )
        return None

    # -- safety -----------------------------------------------------------
    def validate(self, function_name: str) -> None:
        """Re-parse after planning so a malformed edit can never be applied."""
        probe = self.source
        for edit in self.plan(function_name).edits:
            probe = _apply_to_source(probe, [edit])
        ast.parse(probe, filename=self.file_path)


def _apply_to_source(source: str, edits: list[FixEdit]) -> str:
    """Apply edits to source text. Insertions are applied after replacements."""
    lines = source.splitlines()
    replacements: dict[int, str] = {}
    insertions: dict[int, list[str]] = {}

    for edit in edits:
        if edit.is_insert:
            insertions.setdefault(edit.line, []).extend(edit.after.split("\n")[1:])
        else:
            replacements[edit.line - 1] = edit.after

    out: list[str] = []
    for i, line in enumerate(lines):
        if i in replacements:
            out.append(replacements[i])
        else:
            out.append(line)
        if i + 1 in insertions:
            out.extend(insertions[i + 1])

    return "\n".join(out) + ("\n" if source.endswith("\n") else "")


def plan_repo(repo_root: str | Path, targets: list[tuple[str, str]]) -> list[FunctionPlan]:
    """Plan repairs for ``[(file_path, function_name), ...]``."""
    by_file: dict[str, list[str]] = {}
    for file_path, func_name in targets:
        by_file.setdefault(str(Path(file_path).resolve()), []).append(func_name)
    plans: list[FunctionPlan] = []
    for file_path, names in by_file.items():
        planner = RepairPlanner(file_path)
        for name in names:
            try:
                plans.append(planner.plan(name))
            except LookupError:
                continue
    return plans
