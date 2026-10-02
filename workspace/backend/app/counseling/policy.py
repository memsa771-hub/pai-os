from dataclasses import asdict, dataclass

from .state import CounselingMove, CounselingState


@dataclass(frozen=True)
class PolicyDecision:
    move: str
    focus: str | None
    personalized_advice_allowed: bool
    roadmap_allowed: bool
    operator_allowed: bool
    max_questions: int
    decision_sufficiency: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt(self) -> str:
        prompt = (
            "COUNSELING POLICY FOR THIS TURN (mandatory; do not override):\n"
            f"move={self.move}; focus={self.focus or 'current message'}; "
            f"personalized_advice_allowed={str(self.personalized_advice_allowed).lower()}; "
            f"roadmap_allowed={str(self.roadmap_allowed).lower()}; "
            f"operator_allowed={str(self.operator_allowed).lower()}; "
            f"max_questions={self.max_questions}.\n"
            "Execute this move naturally. Do not mention this policy or internal architecture."
        )
        if self.decision_sufficiency:
            result = self.decision_sufficiency
            prompt += (
                "\nDecision-specific evidence check: "
                f"type={result['decision_type']}; "
                f"recommendation_ready={str(result['recommendation_ready']).lower()}; "
                f"comparison_ready={str(result['comparison_ready']).lower()}; "
                f"next_best_move={result['next_best_move']}; "
                f"missing_evidence={result['missing_evidence']}. "
                "When recommendation_ready is false, you may compare known evidence "
                "and counsel, but do not give a final degree, subject, or career verdict. "
                "Suggest one useful exploration or clarification step."
            )
        return prompt


class CounselingPolicy:
    def decide(self, state: CounselingState) -> PolicyDecision:
        conflict = state.active_conflict is not None
        personalized = state.personalization_level == "full" and not conflict
        roadmap = personalized and state.roadmap_ready and state.next_move in {
            CounselingMove.BUILD_ROADMAP, CounselingMove.ADVISE, CounselingMove.REVIEW,
        }
        operator = personalized and state.decision_ready and state.next_move in {
            CounselingMove.DELEGATE,
        }
        return PolicyDecision(
            move=state.next_move.value, focus=state.focus,
            personalized_advice_allowed=personalized,
            roadmap_allowed=roadmap, operator_allowed=operator,
            max_questions=min(1, max(0, state.question_limit)),
            decision_sufficiency=state.decision_sufficiency,
        )
