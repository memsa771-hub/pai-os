"""Deterministic counseling state derivation; the LLM executes, not chooses."""

from app.counseling.state import CounselingMove, CounselingPhase, CounselingState


class CounselingEvaluator:
    def derive(self, *, message: str, vault_context: dict | None,
               journey: dict | None, completion: dict | None,
               recent_conversation: list[dict] | None = None,
               active_conflict: dict | None = None) -> CounselingState:
        missing = (completion or {}).get("missingRequirements", (completion or {}).get("missingCritical", []))
        unknowns = tuple(
            item.get("key") for item in missing
            if isinstance(item, dict) and item.get("key")
        )
        eligible = bool((completion or {}).get("personalizedCounselingEligible", True))
        objective = (journey or {}).get("current_objective")
        blockers = (journey or {}).get("blockers") or []

        if active_conflict:
            return CounselingState(
                CounselingPhase.REVIEWING, "low", CounselingMove.CLARIFY,
                active_conflict.get("summary") or "conflicting student claim",
                False, False, "none", unknowns, active_conflict, objective, 1,
            )
        if blockers:
            return CounselingState(
                CounselingPhase.REVIEWING, "low", CounselingMove.REVIEW,
                "active blocker", False, False, "limited" if eligible else "none",
                unknowns, None, objective, 1,
            )
        if not eligible:
            return CounselingState(
                CounselingPhase.UNDERSTANDING, "high", CounselingMove.ASK,
                unknowns[0] if unknowns else "student context", False, False,
                "none", unknowns, None, objective, 1,
            )
        if not journey:
            return CounselingState(
                CounselingPhase.ORIENTING, "medium", CounselingMove.ALIGN,
                "goal", False, False, "limited", unknowns, None, None, 1,
            )

        stage = str((journey or {}).get("current_stage") or "").casefold()
        if stage in {"review", "reviewing", "decision"}:
            phase, move = CounselingPhase.REVIEWING, CounselingMove.REVIEW
        elif stage in {"action", "acting", "execution", "application"}:
            phase, move = CounselingPhase.ACTING, CounselingMove.DELEGATE
        elif objective:
            phase, move = CounselingPhase.PLANNING, CounselingMove.BUILD_ROADMAP
        else:
            phase, move = CounselingPhase.ALIGNING, CounselingMove.ALIGN
        return CounselingState(
            phase, "low", move, objective or "active goal", True,
            bool(objective), "full", unknowns, None, objective, 1,
        )
