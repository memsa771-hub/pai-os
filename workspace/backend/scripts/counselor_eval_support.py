"""Synthetic student storage for tests/evals; never opens an existing database."""

import json
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.database import Base, set_session_factory
from app.memory.field_definitions import SEED_FIELD_DEFINITIONS, VaultFieldDefinitionService
from app.memory.student_records import ENTITY_MODELS, StudentRecordService
from app.memory.vault import VaultService
from app.models import (
    BackgroundJob, CloudAgentConfig, EventRecord, ExecutionRun, FileRecord,
    MemoryCandidate, PaiEpisode, PaiMemory, ProfileIssue, ProfileRequirement, StudentRecordRevision,
    User, VaultFact, VaultFieldDefinition, Workspace,
)
from app.services import cloud_agent, pai


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kwargs):
    return "JSON"


class EvalWorkspaceApi:
    """Local workspace reads; real web fetch still uses the existing safety pipeline."""
    def __init__(self, workspace_id, token):
        self.workspace_id = workspace_id

    async def get(self, path, **kwargs):
        if path == "/v1/discover":
            return {"ok": True, "data": {"agents": [], "channels": []}}
        return {"ok": True, "data": {"files": [], "tasks": [], "channels": []}}

    async def post(self, path, **kwargs):
        if path == "/v1/fetch":
            from app.routers.fetch import _extract_text, _static_fetch
            args = kwargs["json"]
            fetched = await _static_fetch(args["url"])
            if fetched["status_code"] >= 400:
                return {"ok": False, "error": {"message": f"HTTP {fetched['status_code']}"}}
            data = _extract_text(fetched["html"], fetched["final_url"])
            return {"ok": True, "data": {
                "content": data["text"][:args.get("max_chars", 20000)],
                "title": data["title"], "url": fetched["final_url"], "source": "static",
            }}
        return {"ok": False, "error": {"code": "eval_read_only", "message": "External writes disabled in evaluation"}}


class StudentSession:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pai-counselor-eval-")
        self.engine = create_engine("sqlite:///" + str(Path(self.tmp.name) / "student.db"),
                                    connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def setup(connection, record):
            connection.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat())
            connection.execute("PRAGMA foreign_keys=ON")

        models = [User, Workspace, CloudAgentConfig, ExecutionRun, EventRecord, FileRecord,
                  VaultFact, VaultFieldDefinition, MemoryCandidate, PaiMemory, PaiEpisode,
                  ProfileIssue, ProfileRequirement, StudentRecordRevision, BackgroundJob,
                  *ENTITY_MODELS.values()]
        Base.metadata.create_all(self.engine, tables=[m.__table__ for m in models])
        self.factory = sessionmaker(bind=self.engine, autoflush=False)
        self.previous_factory = set_session_factory(self.factory)
        self.transcript = []
        self.indexed = 0
        self.counter = int(time.time() * 1000)
        with self.factory() as db:
            user = User(email="counselor-eval@example.test", username="counselor_eval")
            db.add(user)
            db.flush()
            workspace = Workspace(name="Synthetic counselor evaluation", owner_user_id=user.id,
                                  password_hash="synthetic-test-only")
            db.add(workspace)
            db.flush()
            self.workspace_id = str(workspace.id)
            cfg = CloudAgentConfig(workspace_id=workspace.id, agent_name=pai.PAI_AGENT_NAME,
                                   provider=pai.PAI_PROVIDER, model="server-managed", category="assistant",
                                   api_key="__server_managed__", status="active")
            db.add(cfg)
            fields = VaultFieldDefinitionService(db)
            for spec in SEED_FIELD_DEFINITIONS:
                fields.upsert_definition(spec)
            db.commit()
            self.config_id = cfg.id
            self.user_id = str(user.id)
        self.patches = [patch.object(pai, "WorkspaceApi", EvalWorkspaceApi),
                        patch.object(cloud_agent, "_post_response", self.post_response)]
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *args):
        for item in reversed(self.patches):
            item.stop()
        set_session_factory(self.previous_factory)
        self.engine.dispose()
        self.tmp.cleanup()

    def next_timestamp(self):
        self.counter += 1
        return self.counter

    async def post_response(self, db, workspace_id, target, agent_name, content, depth,
                            message_type="chat", metadata=None, **kwargs):
        event_id = str(uuid.uuid4())
        db.add(EventRecord(id=event_id, network_id=workspace_id,
                           type="workspace.message.posted", source=f"openagents:{agent_name}",
                           target=target, payload={"content": content, "message_type": message_type},
                           metadata_=metadata or {}, timestamp=self.next_timestamp()))
        db.commit()
        self.transcript.append({"role": "assistant", "content": content, "message_type": message_type})
        return event_id

    async def turn(self, content):
        event_data = {"id": str(uuid.uuid4()), "source": f"human:{self.user_id}",
                      "target": "channel/pai-counselor", "payload": {"content": content},
                      "timestamp": self.next_timestamp()}
        self.transcript.append({"role": "user", "content": content})
        with self.factory() as db:
            db.add(EventRecord(id=event_data["id"], network_id=self.workspace_id,
                               type="workspace.message.posted", source=event_data["source"],
                               target=event_data["target"], payload=event_data["payload"],
                               timestamp=event_data["timestamp"]))
            db.commit()
            cfg = db.get(CloudAgentConfig, self.config_id)
            await cloud_agent._invoke_assistant_agent(db, self.workspace_id, event_data, cfg, 0)

    async def extract_and_reconcile(self):
        from app.memory.handlers import embed_memory, extract_memory, reconcile_memory
        # Execute the very same persisted jobs the worker handles, in the order
        # the worker runs them: extract -> reconcile -> embed. Embedding is
        # included because a counselor that cannot recall what it stored is the
        # failure this evaluation exists to catch. With no vector backend
        # configured the embed job is a no-op, so this stays correct offline.
        with self.factory() as db:
            jobs = db.execute(select(BackgroundJob).where(
                BackgroundJob.workspace_id == self.workspace_id,
                BackgroundJob.job_type == "memory.extract", BackgroundJob.status == "pending",
            )).scalars().all()
            for job in jobs:
                result = await extract_memory(job, db)
                job.status = "completed"
                db.commit()
            jobs = db.execute(select(BackgroundJob).where(
                BackgroundJob.workspace_id == self.workspace_id,
                BackgroundJob.job_type == "memory.reconcile", BackgroundJob.status == "pending",
            )).scalars().all()
            outcomes = []
            for job in jobs:
                outcomes.append(await reconcile_memory(job, db))
                job.status = "completed"
                db.commit()
            # Reconciliation enqueues the embed jobs, so this must read the
            # table again rather than reuse the list above.
            jobs = db.execute(select(BackgroundJob).where(
                BackgroundJob.workspace_id == self.workspace_id,
                BackgroundJob.job_type == "memory.embed", BackgroundJob.status == "pending",
            )).scalars().all()
            for job in jobs:
                self.indexed += (await embed_memory(job, db)).get("indexed", 0)
                job.status = "completed"
                db.commit()
            return outcomes

    def profile(self):
        with self.factory() as db:
            return {
                "facts": VaultService(db).snapshot(self.workspace_id, include_sensitive=True),
                "records": StudentRecordService(db).snapshot(self.workspace_id),
                "memories": [row.content for row in db.execute(select(PaiMemory).where(
                    PaiMemory.workspace_id == self.workspace_id)).scalars()],
                "candidates": [{"type": row.candidate_type, "key": row.key,
                                "status": row.status, "reason": row.rejection_reason,
                                "evidence": row.evidence} for row in db.execute(
                    select(MemoryCandidate).where(MemoryCandidate.workspace_id == self.workspace_id)).scalars()],
            }
