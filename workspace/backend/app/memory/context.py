# -*- coding: utf-8 -*-
"""MemoryContextService — the single read path into memory for prompts.

Two jobs:

1. `build_student_context(...)` assembles a *compact* context — critical Vault
   facts, relevant semantic memories, recent episodes. Never the whole
   database: every section is budgeted, because the failure mode of a memory
   system is not "forgot something", it is "sent 80k tokens of history and
   buried the question".

2. `resolve_refs(...)` turns Operator's lightweight `context_refs`
   (`["vault", "memory:preferences", "episodes:recent"]`) into fresh data at
   read time. ExecutionRun keeps storing *references*, never payloads, so a
   run started an hour ago sees the student's current profile rather than a
   stale copy — and the row stays small.

Retrieval today is structured + lexical. The hybrid dense/sparse/rerank stack
slots in behind `_semantic_section` without changing this contract.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .episodic import EpisodicMemoryService
from .permissions import capabilities_for_agent
from .semantic import MemoryService
from .vault import VaultService
from .student_records import StudentRecordService
from .field_definitions import usable_in_counseling
from app.tools.policy import Capability

logger = logging.getLogger(__name__)

# Per-section budgets. Small on purpose.
MAX_VAULT_FIELDS = 40
MAX_SEMANTIC_MEMORIES = 8
MAX_EPISODES = 5

# Reference grammar: "vault", "memory", "memory:<type>", "episodes",
# "episodes:recent", "episodes:<event_type>".
REF_VAULT = "vault"
REF_MEMORY = "memory"
REF_EPISODES = "episodes"


@dataclass
class StudentContext:
    """A compact, caller-shaped view of what PAI knows about this student."""

    workspace_id: str
    vault: dict[str, Any] = field(default_factory=dict)
    records: dict[str, list[dict]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    readiness: dict[str, Any] = field(default_factory=dict)
    memories: list[dict] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    # Which refs produced this, for debugging and for the Operator run record.
    resolved_refs: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.vault or self.records or self.issues or self.memories or self.episodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vault": self.vault,
            "records": self.records,
            "issues": self.issues,
            "readiness": self.readiness,
            "memories": self.memories,
            "episodes": self.episodes,
            "resolved_refs": self.resolved_refs,
        }

    def to_prompt_block(self) -> str:
        """Render as a compact prompt section. Empty string when there is
        nothing to say — never a block of headings with no content."""
        from .foreground import MEMORY_RULES, MEMORY_RULES_TRAILER, render_block
        block, _ = render_block(self)
        return "\n\n".join((MEMORY_RULES, block, MEMORY_RULES_TRAILER)) if block else ""


class MemoryContextService:
    """Assembles student context for any PAI caller."""

    def __init__(self, db):
        self.db = db
        self.vault = VaultService(db)
        self.records = StudentRecordService(db)
        self.memories = MemoryService(db)
        self.episodes = EpisodicMemoryService(db)

    def build_student_context(
        self,
        workspace_id: str,
        query: Optional[str] = None,
        context_refs: Optional[list[str]] = None,
        caller: str = "counselor",
        include_sensitive: bool = False,
    ) -> StudentContext:
        """The unified entry point.

        `context_refs=None` means "the default useful set". An explicit list
        narrows it, which is how Operator asks for only what its objective
        needs.

        Reads are capability-gated too, not just writes: a caller without
        `vault.read` gets no Vault section. Otherwise a future low-trust agent
        could read the whole profile simply by calling this directly.
        """
        granted = capabilities_for_agent(self._agent_name_for(caller))
        context = StudentContext(workspace_id=workspace_id)

        refs = context_refs if context_refs is not None else [REF_VAULT, REF_MEMORY, REF_EPISODES]
        for ref in refs:
            name, _, argument = ref.partition(":")
            name = name.strip().lower()

            if name == REF_VAULT:
                if Capability.VAULT_READ.value not in granted:
                    continue
                context.vault = self._vault_section(workspace_id, include_sensitive)
                context.records = self._record_section(workspace_id, query)
                context.issues = self._issues_section(workspace_id)
                context.resolved_refs.append(ref)
            elif name == REF_MEMORY:
                if Capability.MEMORY_READ.value not in granted:
                    continue
                context.memories = self._semantic_section(
                    workspace_id, query, memory_type=argument or None
                )
                context.resolved_refs.append(ref)
            elif name == REF_EPISODES:
                if Capability.MEMORY_READ.value not in granted:
                    continue
                context.episodes = self._episode_section(
                    workspace_id, query, selector=argument or None
                )
                context.resolved_refs.append(ref)
            else:
                logger.debug("ignoring unknown context ref: %s", ref)

        return context

    def resolve_refs(
        self, workspace_id: str, context_refs: list[str], caller: str = "operator",
    ) -> StudentContext:
        """Resolve an ExecutionRun's stored refs to fresh data."""
        return self.build_student_context(
            workspace_id=workspace_id, context_refs=context_refs, caller=caller,
        )

    async def build_student_context_async(
        self,
        workspace_id: str,
        query: Optional[str] = None,
        context_refs: Optional[list[str]] = None,
        caller: str = "counselor",
        include_sensitive: bool = False,
    ) -> StudentContext:
        """Same contract, but uses hybrid retrieval when a query is present.

        Kept as a separate method rather than making `build_student_context`
        async: both existing callers (PAI Operator's background task and the
        memory tools) are synchronous, and hybrid retrieval needs an event
        loop. They keep working unchanged on the structured/lexical path.

        Vault stays structured-only either way — exact state is not a
        similarity problem.
        """
        granted = capabilities_for_agent(self._agent_name_for(caller))
        context = StudentContext(workspace_id=workspace_id)
        refs = context_refs if context_refs is not None else [REF_VAULT, REF_MEMORY, REF_EPISODES]

        wants_memory = any(r.partition(":")[0].strip().lower() == REF_MEMORY for r in refs)
        wants_episodes = any(r.partition(":")[0].strip().lower() == REF_EPISODES for r in refs)
        can_read_memory = Capability.MEMORY_READ.value in granted

        # Recorded so the caller can report what retrieval ACTUALLY did rather
        # than assuming "a query was supplied, therefore hybrid" — the
        # retriever degrades to lexical on its own when no vector backend is
        # configured or the index is unreachable.
        self.last_retrieval_mode = None

        retrieved = None
        if query and can_read_memory and (wants_memory or wants_episodes):
            from .retriever import KIND_EPISODE, KIND_SEMANTIC, MemoryRetriever

            kinds = tuple(
                k for k, wanted in (
                    (KIND_SEMANTIC, wants_memory), (KIND_EPISODE, wants_episodes),
                ) if wanted
            )
            retrieved = await MemoryRetriever(self.db).retrieve(
                workspace_id=workspace_id, query=query, kinds=kinds,
                limit=max(MAX_SEMANTIC_MEMORIES, MAX_EPISODES),
            )
            self.last_retrieval_mode = retrieved.mode

        for ref in refs:
            name, _, argument = ref.partition(":")
            name = name.strip().lower()

            if name == REF_VAULT:
                if Capability.VAULT_READ.value not in granted:
                    continue
                context.vault = self._vault_section(workspace_id, include_sensitive)
                context.records = self._record_section(workspace_id, query)
                context.issues = self._issues_section(workspace_id)
                context.resolved_refs.append(ref)
            elif name == REF_MEMORY:
                if not can_read_memory:
                    continue
                context.memories = (
                    [MemoryService.to_dict(m) for m in retrieved.memories[:MAX_SEMANTIC_MEMORIES]]
                    if retrieved is not None
                    else self._semantic_section(workspace_id, query, argument or None)
                )
                context.resolved_refs.append(ref)
            elif name == REF_EPISODES:
                if not can_read_memory:
                    continue
                context.episodes = (
                    [EpisodicMemoryService.to_dict(e) for e in retrieved.episodes[:MAX_EPISODES]]
                    if retrieved is not None
                    else self._episode_section(workspace_id, query, argument or None)
                )
                context.resolved_refs.append(ref)
            else:
                logger.debug("ignoring unknown context ref: %s", ref)

        if retrieved is not None:
            logger.info(
                "context: workspace=%s mode=%s memories=%d episodes=%d",
                workspace_id, retrieved.mode,
                len(context.memories), len(context.episodes),
            )
        return context

    # -- sections ----------------------------------------------------------

    def _record_section(self, workspace_id: str, query: Optional[str]) -> dict[str, list[dict]]:
        from .student_records import ENTITY_MODELS
        q = (query or "").lower()
        kinds = ["goal", "education", "test_attempt", "language_proficiency", "work_experience", "skill", "project", "certification", "research", "achievement", "financial_sponsor", "scholarship_application", "visa", "document"]
        if any(word in q for word in ("career", "work", "job", "intern", "project", "skill")):
            kinds = ["goal", "education", "work_experience", "skill", "project", "certification", "research", "achievement", "test_attempt", "language_proficiency"]
        if any(word in q for word in ("application", "admission", "deadline")):
            kinds.append("application")
        snapshot = self.records.snapshot(workspace_id, kinds=kinds, limit=5)
        safe_columns = {
            "education": ("id", "institution_name", "qualification_name", "canonical_level", "field_of_study", "graduation_year", "academic_status", "result"),
            "goal": ("id", "goal_type", "title", "commitment", "target_date"),
            "test_attempt": ("id", "test_type", "test_date", "expiry_date", "overall_score"),
            "work_experience": ("id", "organization", "role", "experience_type", "start_date", "end_date"),
            "project": ("id", "name", "role"),
            "skill": ("id", "name", "proficiency"),
            "certification": ("id", "name", "issuer", "issued_on"),
            "application": ("id", "institution_name", "program_name", "intake", "application_status", "deadline"),
            "language_proficiency": ("id", "language", "proficiency", "evidence_type"),
            "research": ("id", "title", "organization", "role", "start_date", "end_date"),
            "achievement": ("id", "title", "achievement_type", "issuer", "achieved_on"),
            "financial_sponsor": ("id", "sponsor_type", "name", "commitment_status"),
            "scholarship_application": ("id", "scholarship_name", "provider", "application_status", "deadline"),
            "visa": ("id", "country", "visa_type", "application_status", "expiry_date"),
            "document": ("id", "file_id", "document_type", "title"),
        }
        from .student_schema import RECORD_SPECS
        for kind in safe_columns:
            safe_columns[kind] += ("verification_status",)
        return {
            kind: [
                {key: row[key] for key in safe_columns[kind] if key in row}
                | ({"details": {key: value for key, value in row.get("details", {}).items()
                                if key in RECORD_SPECS[kind]["properties"].get("details", {}).get("properties", {})}}
                   if row.get("details") else {})
                for row in snapshot[kind][:5 if kind in ("education", "goal", "test_attempt") else 2]]
            for kind in kinds if kind in ENTITY_MODELS and snapshot[kind]
        }

    def _issues_section(self, workspace_id):
        return [{"id": issue.id, "type": issue.issue_type, "severity": issue.severity,
                 "summary": issue.summary, "clarification_question": issue.clarification_question,
                 "record_type": (issue.evidence or {}).get("record_type"),
                 "record_id": (issue.evidence or {}).get("record_id"),
                 "field_key": (issue.evidence or {}).get("field_key")}
                for issue in self.records.issues(workspace_id)[:3]]

    def _vault_section(self, workspace_id: str, include_sensitive: bool) -> dict[str, Any]:
        definitions = {d.key: d for d in self.vault.fields.list_definitions()}
        allowed = {key for key, definition in definitions.items() if usable_in_counseling(definition)}
        snapshot = self.vault.snapshot(workspace_id, include_sensitive=include_sensitive,
                                      allowed_sensitive_keys=allowed)
        if not include_sensitive:
            snapshot = {key: value for key, value in snapshot.items() if key in allowed}
        ranked = sorted(snapshot, key=lambda key: (
            -(getattr(definitions.get(key), "profile_priority", 50) or 50), key
        ))
        return {key: snapshot[key] for key in ranked[:MAX_VAULT_FIELDS]}

    def _semantic_section(
        self, workspace_id: str, query: Optional[str], memory_type: Optional[str],
    ) -> list[dict]:
        """Relevant semantic memories.

        With a query this is lexical search (the sparse half of the eventual
        hybrid); without one it is the most important memories. Phase 2 fuses a
        dense retriever and a reranker in here.
        """
        if query:
            found = self.memories.search(
                workspace_id, query, limit=MAX_SEMANTIC_MEMORIES, memory_type=memory_type
            )
            if found:
                return [MemoryService.to_dict(m) for m in found]
        return [
            MemoryService.to_dict(m)
            for m in self.memories.list_memories(
                workspace_id, memory_type=memory_type, limit=MAX_SEMANTIC_MEMORIES
            )
        ]

    def _episode_section(
        self, workspace_id: str, query: Optional[str], selector: Optional[str],
    ) -> list[dict]:
        if selector and selector != "recent":
            found = self.episodes.recent(
                workspace_id, limit=MAX_EPISODES, event_type=selector
            )
        elif query:
            found = self.episodes.search(workspace_id, query, limit=MAX_EPISODES)
            if not found:
                found = self.episodes.recent(workspace_id, limit=MAX_EPISODES)
        else:
            found = self.episodes.recent(workspace_id, limit=MAX_EPISODES)
        return [EpisodicMemoryService.to_dict(e) for e in found]

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _agent_name_for(caller: str) -> str:
        """Map a caller label to the agent name permissions are keyed on."""
        from .permissions import COUNSELOR_AGENT_NAME, OPERATOR_AGENT_NAME

        if caller in ("counselor", COUNSELOR_AGENT_NAME):
            return COUNSELOR_AGENT_NAME
        if caller in ("operator", OPERATOR_AGENT_NAME):
            return OPERATOR_AGENT_NAME
        return caller
