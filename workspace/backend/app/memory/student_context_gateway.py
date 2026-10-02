"""Least-privilege, schema-stable student context for core and capabilities."""

from dataclasses import dataclass
from typing import Any, Iterable

from .permissions import capabilities_for_agent
from .student_records import StudentRecordService
from .vault import VaultService

SCOPES = frozenset({
    "identity", "education", "goals", "preferences", "finance", "tests",
    "skills", "projects", "achievements", "activities", "documents", "applications", "exploration",
})
RECORDS = {
    "education": ("education", "course"), "goals": ("goal",),
    "finance": ("financial_sponsor",), "tests": ("test_attempt", "language_proficiency"),
    "skills": ("skill", "certification"), "projects": ("project", "research"),
    "achievements": ("achievement",), "documents": ("document",),
    "activities": ("activity",),
    "applications": ("application", "scholarship_application", "visa"),
    "exploration": ("exploration_experience",),
}


class StudentContextAccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ScopedStudentContext:
    workspace_id: str
    scopes: tuple[str, ...]
    domains: dict[str, Any]

    def to_dict(self) -> dict:
        return {"workspace_id": self.workspace_id, "scopes": list(self.scopes), "domains": self.domains}


class StudentContextGateway:
    def __init__(self, db):
        self.db = db
        self.vault = VaultService(db)
        self.records = StudentRecordService(db)

    def get(self, workspace_id: str, scopes: Iterable[str], *, caller: str,
            granted_permissions: Iterable[str] | None = None) -> ScopedStudentContext:
        requested = tuple(dict.fromkeys(scopes))
        unknown = set(requested) - SCOPES
        if unknown:
            raise StudentContextAccessDenied(f"unknown student context scope: {sorted(unknown)[0]}")
        granted = set(capabilities_for_agent(caller) if granted_permissions is None else granted_permissions)
        for scope in requested:
            permission = f"vault.{scope}.read"
            if permission not in granted:
                raise StudentContextAccessDenied(f"student context permission denied: {permission}")

        # The existing Vault and typed-record stores remain canonical. This is
        # only a projection and deliberately exposes no ORM rows.
        facts = self.vault.snapshot(workspace_id, include_sensitive=True)
        domains: dict[str, Any] = {}
        for scope in requested:
            prefixes = {scope + "."}
            if scope == "goals": prefixes |= {"goal."}
            if scope == "tests": prefixes |= {"test."}
            domain_facts = {key: value for key, value in facts.items()
                            if any(key.startswith(prefix) for prefix in prefixes)}
            record_kinds = RECORDS.get(scope, ())
            snapshot = self.records.snapshot(workspace_id, kinds=record_kinds) if record_kinds else {}
            domains[scope] = {
                "facts": domain_facts,
                "records": {kind: list(snapshot.get(kind, [])) for kind in record_kinds},
            }
        return ScopedStudentContext(str(workspace_id), requested, domains)
