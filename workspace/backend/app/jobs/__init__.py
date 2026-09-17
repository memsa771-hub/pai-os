# -*- coding: utf-8 -*-
"""Durable background jobs backed by PostgreSQL.

Deliberately generic — this is not a memory subsystem. Memory is the first
consumer (`app/memory/handlers.py` registers `memory.*` job types), but the
queue itself knows nothing about memory and any future job type can use it.

Why not `asyncio.create_task()`, which PAI Operator uses: an in-process task
dies with the process. For an Operator run whose status the user is actively
watching, that is a recoverable annoyance. For memory formation it is silent
data loss — the student tells us their budget, the pod restarts, and the fact
never lands. Jobs here survive restarts because the queue is a table.
"""

from .service import BackgroundJobService, JobHandlerRegistry, job_handlers

__all__ = ["BackgroundJobService", "JobHandlerRegistry", "job_handlers"]
