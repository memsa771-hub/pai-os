"""Profile completion and rollout-aware Counselor policy."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.config import config
from app.models import User, Workspace
from .education_journey import EducationJourneyService
from .profile_requirements import ProfileRequirementRegistry, TIERS
from .student_snapshot import StudentSnapshot, StudentSnapshotService


TIER_THRESHOLDS = {"critical": 1.0, "important": 0.80, "enrichment": 0.20}


class ProfileCompletionService:
    def __init__(self, db):
        self.db = db
        self.snapshots = StudentSnapshotService(db)
        self.journeys = EducationJourneyService()
        self.registry = ProfileRequirementRegistry(db)

    def evaluate(self, workspace_id: str, snapshot: StudentSnapshot | None = None) -> dict:
        snapshot = snapshot or self.snapshots.build(workspace_id)
        journey = self.journeys.evaluate(snapshot)
        requirements = self.registry.active()
        counts = {tier: {"filled": 0, "total": 0} for tier in TIERS}
        missing = []

        for requirement in requirements:
            applicable, filled = self.registry.evaluate(requirement, snapshot, journey)
            if not applicable:
                continue
            counts[requirement.tier]["total"] += 1
            if filled:
                counts[requirement.tier]["filled"] += 1
                continue
            missing.append({
                "key": requirement.key,
                "tier": requirement.tier,
                "question": requirement.question,
                "priority": requirement.priority,
            })

        tiers = {}
        for tier in TIERS:
            filled, total = counts[tier]["filled"], counts[tier]["total"]
            ratio = 1.0 if total == 0 else filled / total
            tiers[tier] = {
                "filled": filled,
                "total": total,
                "percentage": round(ratio * 100),
                "satisfied": ratio >= TIER_THRESHOLDS[tier],
            }

        order = {tier: index for index, tier in enumerate(TIERS)}
        missing.sort(key=lambda item: (order[item["tier"]], -item["priority"], item["key"]))
        eligible = all(tiers[tier]["satisfied"] for tier in TIERS)
        rollout = self.enforcement(workspace_id)
        return {
            "tiers": tiers,
            "missingRequirements": missing,
            "nextRequirement": missing[0] if missing else None,
            "personalizedCounselingEligible": eligible,
            "requirementVersion": max((row.version for row in requirements), default=0),
            "enforcementMode": rollout["mode"],
            "enforced": rollout["enforced"],
            "counselorMode": "collection" if rollout["enforced"] and not eligible else "normal",
        }

    def enforcement(self, workspace_id: str) -> dict:
        mode = str(getattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_MODE", "shadow") or "shadow").strip().lower()
        if mode not in {"off", "shadow", "new", "all"}:
            raise ValueError("PAI_PROFILE_COMPLETION_ROLLOUT_MODE must be off, shadow, new, or all")
        if mode in {"off", "shadow"}:
            return {"mode": mode, "enforced": False}
        if mode == "all":
            return {"mode": mode, "enforced": True}

        cutoff_text = str(getattr(config, "PAI_PROFILE_COMPLETION_ROLLOUT_AT", "") or "").strip()
        if not cutoff_text:
            return {"mode": mode, "enforced": False}
        cutoff = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        workspace = self.db.execute(select(Workspace).where(Workspace.id == workspace_id)).scalar_one_or_none()
        if workspace is None:
            return {"mode": mode, "enforced": False}
        created_at = workspace.created_at
        if workspace.owner_user_id:
            user_created = self.db.execute(
                select(User.created_at).where(User.id == workspace.owner_user_id)
            ).scalar_one_or_none()
            created_at = user_created or created_at
        if created_at is None:
            return {"mode": mode, "enforced": False}
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return {"mode": mode, "enforced": created_at >= cutoff}


def counselor_policy_prompt(completion: dict) -> str:
    if completion["counselorMode"] == "normal":
        return "Personalized counseling policy: normal mode."
    next_requirement = completion.get("nextRequirement") or {}
    return (
        "Personalized counseling policy: COLLECTION MODE (server-enforced).\n"
        "Answer general educational and factual questions normally, but do not give "
        "student-specific recommendations, rankings, fit conclusions, or personalized research. "
        "Ask at most one missing-profile question. If the student's message answers the active "
        "requirement below, call profile__answer before replying so it is saved and recalculated.\n"
        f"Active requirement key: {next_requirement.get('key') or 'none'}\n"
        f"Question: {next_requirement.get('question') or 'No applicable question is available.'}"
    )


def collection_hold_message(completion: dict) -> str:
    next_requirement = completion.get("nextRequirement") or {}
    question = next_requirement.get("question")
    if question:
        return (
            "The background work is complete, but I need one detail before I can "
            f"interpret it for your situation: {question}"
        )
    return (
        "The background work is complete, but your profile is not yet complete "
        "enough for me to interpret it as personalized advice."
    )
