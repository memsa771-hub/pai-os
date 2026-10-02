"""Deterministic counseling state derivation; the LLM executes, not chooses."""

from app.counseling.state import CounselingMove, CounselingPhase, CounselingState
from .discovery import SUPPRESSED_STATUSES, is_general_information_request


class CounselingEvaluator:
    def derive(self, *, message: str, vault_context: dict | None,
               journey: dict | None, completion: dict | None,
               recent_conversation: list[dict] | None = None,
               active_conflict: dict | None = None) -> CounselingState:
        missing = (completion or {}).get("missingRequirements", (completion or {}).get("missingCritical", []))
        unknowns = tuple(
            str(item.get("key")) for item in missing
            if isinstance(item, dict) and item.get("key")
        )
        eligible = bool((completion or {}).get("personalizedCounselingEligible", True))
        objective = (journey or {}).get("current_objective")
        blockers = [item for item in ((journey or {}).get("blockers") or [])
                    if item.get("status", "open") == "open"]

        understanding = vault_context or {}
        baseline = understanding.get("baseline") or {}
        if is_general_information_request(message) and baseline.get("status") != "confirmed":
            return CounselingState(
                CounselingPhase.DISCOVERING, "low", CounselingMove.REFLECT,
                "answer the general question", False, False, "none", unknowns,
                None, objective, 0,
            )
        if "baseline" in understanding and baseline.get("status") != "confirmed":
            if active_conflict or understanding.get("open_conflicts"):
                conflict = active_conflict or understanding["open_conflicts"][0]
                return CounselingState(
                    CounselingPhase.DISCOVERING, "low", CounselingMove.CLARIFY,
                    conflict.get("summary") or conflict.get("question") or "conflicting claim",
                    False, False, "none", unknowns, conflict, objective, 1,
                )
            if baseline.get("status") == "mirror_review":
                return CounselingState(
                    CounselingPhase.MIRROR_REVIEW, "low", CounselingMove.SHOW_MIRROR,
                    "student mirror", False, False, "none", unknowns, None, objective, 1,
                )
            gaps = self.relevant_gaps(understanding, message)
            focus = next((g.get("focus") or g.get("level") for g in gaps), "student context")
            documents = (understanding.get("documents") or {}).get("nodes") or []
            if documents and focus in {"current_level", "academic_performance", "upper_secondary"}:
                move = CounselingMove.REFLECT
                focus = "use uploaded evidence"
            elif focus in {"current_level", "academic_performance", "upper_secondary"}:
                move = CounselingMove.REQUEST_DOCUMENT
            else:
                move = CounselingMove.ASK
            from .understanding import baseline_sufficient
            ready = baseline_sufficient(understanding)
            identity_ready = bool(understanding.get("identity")) and not (
                understanding.get("education") or {}).get("nodes")
            return CounselingState(
                CounselingPhase.BUILDING_PROFILE if ready else (
                    CounselingPhase.IDENTITY_READY if identity_ready else CounselingPhase.DISCOVERING),
                "low" if ready else "high",
                CounselingMove.SHOW_MIRROR if ready else move,
                focus, False, False, "none", unknowns, None, objective, 1,
            )

        if "baseline" in understanding and baseline.get("status") == "confirmed":
            gaps = self.relevant_gaps(understanding, message)
            relevant_unknowns = tuple(g.get("focus", "context") for g in gaps)
            if active_conflict or understanding.get("open_conflicts"):
                conflict = active_conflict or understanding["open_conflicts"][0]
                return CounselingState(
                    CounselingPhase.COUNSELING, "low", CounselingMove.CLARIFY,
                    conflict.get("summary") or conflict.get("question") or "conflicting claim",
                    False, False, "none", relevant_unknowns, conflict, objective, 1,
                )
            text = message.casefold()
            from .decision_sufficiency import (DecisionSufficiencyEvaluator,
                                                decision_type_for_message)
            decision_type = decision_type_for_message(message, understanding)
            sufficiency = (DecisionSufficiencyEvaluator().evaluate(
                understanding, decision_type, message=message).to_dict()
                if decision_type else None)
            requested_work = any(phrase in text for phrase in (
                "shortlist", "research", "compare programs", "review my cv",
                "review my transcript", "apply to", "submit application",
                "let's explore it", "explore this field", "explore possible directions",
                "try an activity", "start an exploration",
            ))
            requested_roadmap = any(phrase in text for phrase in (
                "roadmap", "step-by-step plan", "plan my path",
            ))
            if requested_work and not blockers:
                move = CounselingMove.DELEGATE
            elif sufficiency and not sufficiency["recommendation_ready"]:
                move = CounselingMove(sufficiency["next_best_move"])
            elif requested_roadmap:
                move = CounselingMove.BUILD_ROADMAP
            else:
                move = CounselingMove.COUNSEL
            return CounselingState(
                CounselingPhase.COUNSELING, "low", move,
                (sufficiency["missing_evidence"][0] if sufficiency and
                 sufficiency["missing_evidence"] else gaps[0].get("focus") if gaps else objective or "current goal"),
                move is CounselingMove.DELEGATE,
                move is CounselingMove.BUILD_ROADMAP,
                "full", relevant_unknowns, None, objective,
                1 if gaps or (sufficiency and sufficiency["missing_evidence"]) else 0,
                sufficiency,
            )

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
        if stage in {"orienting", "orientation"}:
            phase, move = CounselingPhase.ORIENTING, CounselingMove.ALIGN
        elif stage == "understanding":
            phase, move = CounselingPhase.UNDERSTANDING, CounselingMove.ASK
        elif stage in {"aligning", "alignment"}:
            phase, move = CounselingPhase.ALIGNING, CounselingMove.ALIGN
        elif stage in {"planning", "plan"}:
            phase, move = CounselingPhase.PLANNING, CounselingMove.BUILD_ROADMAP
        elif stage in {"review", "reviewing", "decision", "completed"}:
            phase, move = CounselingPhase.REVIEWING, CounselingMove.REVIEW
        elif stage in {"action", "acting", "execution", "application"}:
            phase, move = CounselingPhase.ACTING, CounselingMove.DELEGATE
        elif objective:
            phase, move = CounselingPhase.PLANNING, CounselingMove.BUILD_ROADMAP
        else:
            phase, move = CounselingPhase.ALIGNING, CounselingMove.ALIGN
        decision_ready = phase not in {CounselingPhase.ORIENTING, CounselingPhase.UNDERSTANDING}
        return CounselingState(
            phase, "low", move, objective or "active goal", decision_ready,
            bool(objective) and phase not in {CounselingPhase.ORIENTING, CounselingPhase.UNDERSTANDING},
            "full", unknowns, None, objective, 1,
        )

    @staticmethod
    def relevant_gaps(understanding: dict, message: str) -> list[dict]:
        """Choose a relevant thread, not a required-field interview order."""
        gaps = [g for g in understanding.get("open_gaps", [])
                if g.get("status") not in SUPPRESSED_STATUSES]
        text = message.casefold()
        related = (
            ("academic_performance", ("grade", "gpa", "result", "marks", "transcript")),
            ("budget", ("afford", "budget", "fund", "cost", "scholarship")),
            ("strengths", ("good at", "strength", "skill", "project", "experience", "capable")),
            ("interests", ("interest", "enjoy", "like doing", "curious")),
            ("motivation", ("why", "meaning", "motivat", "matters", "purpose")),
            ("practical_constraints", ("time", "family", "work hours", "relocat", "constraint")),
            ("education_history", ("previous", "history", "before", "qualification")),
            ("work_history", ("work", "job", "career change", "transition")),
            ("target_location", ("country", "location", "destination", "where")),
            ("target_timing", ("intake", "start", "when", "timing")),
        )
        preferred = next((focus for focus, terms in related if any(term in text for term in terms)), None)
        return sorted(gaps, key=lambda gap: 0 if gap.get("focus") == preferred else 1)
