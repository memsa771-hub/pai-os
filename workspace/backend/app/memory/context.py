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
    memories: list[dict] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    # Which refs produced this, for debugging and for the Operator run record.
    resolved_refs: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.vault or self.memories or self.episodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vault": self.vault,
            "memories": self.memories,
            "episodes": self.episodes,
            "resolved_refs": self.resolved_refs,
        }

    def to_prompt_block(self) -> str:
        """Render as a compact prompt section. Empty string when there is
        nothing to say — never a block of headings with no content."""
        if self.is_empty():
            return ""
        lines: list[str] = ["## What you know about this student"]
        if self.vault:
            lines.append("\n### Profile")
            for key, value in sorted(self.vault.items()):
                lines.append(f"- {key}: {value}")
        if self.memories:
            lines.append("\n### Preferences and goals")
            for memory in self.memories:
                lines.append(f"- {memory['content']}")
        if self.episodes:
            lines.append("\n### Recent history")
            for episode in self.episodes:
                lines.append(f"- {episode['summary']}")
        return "\n".join(lines)


class MemoryContextService:
    """Assembles student context for any PAI caller."""

    def __init__(self, db):
        self.db = db
        self.vault = VaultService(db)
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

        for ref in refs:
            name, _, argument = ref.partition(":")
            name = name.strip().lower()

            if name == REF_VAULT:
                if Capability.VAULT_READ.value not in granted:
                    continue
                context.vault = self._vault_section(workspace_id, include_sensitive)
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

    def _vault_section(self, workspace_id: str, include_sensitive: bool) -> dict[str, Any]:
        snapshot = self.vault.snapshot(workspace_id, include_sensitive=include_sensitive)
        if len(snapshot) <= MAX_VAULT_FIELDS:
            return snapshot
        return dict(sorted(snapshot.items())[:MAX_VAULT_FIELDS])

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
