"""Validated lifecycle coordination between conversation intent and storage.

The coordinator deliberately has no counseling or recommendation knowledge. It
recognizes durable intent and turns it into calls to :class:`JourneyService`;
models and callers never receive an ORM write surface.
"""

from __future__ import annotations

import re
from typing import Any

from .service import JourneyError, JourneyService, new_id
from .transitions import JourneyStage


class JourneyCoordinator:
    def __init__(self, service: JourneyService):
        self.service = service

    def observe_message(self, workspace_id: str, message: str, *, actor: str = "student"):
        """Apply a small, conservative durable-intent recognizer.

        This is intentionally not a guidance engine. Ambiguous ideas are left
        alone; an upstream model can instead submit an explicit ``process_intent``
        proposal, which is validated through the same service boundary.
        """
        lower = " ".join(str(message or "").casefold().split())
        active = self.service.list_active(workspace_id)
        current = self.service.resolve_active(workspace_id)
        if current and any(phrase in lower for phrase in ("pause this goal", "pause my journey", "put this goal on hold")):
            return self.service.pause(workspace_id, current.id, actor=actor)
        if current and any(phrase in lower for phrase in ("i completed this goal", "this journey is complete", "mark this goal complete")):
            return self.service.complete(workspace_id, current.id, actor=actor)
        if current and any(phrase in lower for phrase in ("abandon this goal", "i no longer want this", "drop this goal")):
            return self.service.abandon(workspace_id, current.id, actor=actor)
        if any(phrase in lower for phrase in ("focus on", "switch focus to")):
            selected = next((item for item in active
                             if item.journey_type.replace("_", " ") in lower
                             or any(token in lower for token in item.title.casefold().split()
                                    if len(token) > 5)), None)
            if selected is not None:
                return self.service.set_primary(workspace_id, selected.id, actor=actor)

        intent = self._intent_from_message(message)
        if intent is None:
            decision_target = _extract_confirmed_target(message)
            if decision_target and current and current.current_focus_goal_id:
                primary = next((goal for goal in current.goals if goal.get("type") == "primary"), None)
                return self.service.add_decision(workspace_id, current.id, {
                    "id": new_id("decision"),
                    "goal_id": (primary or {}).get("id") or current.current_focus_goal_id,
                    "type": "target_country", "value": decision_target,
                    "status": "confirmed", "source": "student",
                }, actor=actor)
            return self.service.resolve_active(workspace_id)
        if intent.get("relationship") == "subgoal" and not active:
            intent = {**intent, "relationship": "separate"}
        return self.process_intent(workspace_id, intent, actor=actor)

    def process_intent(self, workspace_id: str, intent: dict[str, Any], *, actor: str = "system"):
        """Validate and persist a proposed meaningful journey change."""
        if not isinstance(intent, dict):
            raise JourneyError("journey intent must be an object")
        action = str(intent.get("action") or "upsert")
        if action != "upsert":
            return self._apply_action(workspace_id, action, intent, actor)
        journey_type = _text(intent.get("journey_type"), "journey_type")
        if journey_type == "direction_discovery":
            from .direction_discovery import DirectionDiscoveryService
            return DirectionDiscoveryService(self.service.db).start(
                workspace_id, intent.get("directions"), actor=actor)
        goal_title = _text(intent.get("goal_title"), "goal_title")
        relationship = str(intent.get("relationship") or "same_or_new")
        if relationship not in {"same_or_new", "same", "subgoal", "separate"}:
            raise JourneyError("invalid journey relationship")

        active = self.service.list_active(workspace_id)
        same = next((item for item in active if item.journey_type == journey_type), None)
        parent = self.service.resolve_active(workspace_id)

        if relationship == "subgoal":
            if parent is None:
                raise JourneyError("a subgoal requires an active parent journey")
            return self._add_subgoal(parent, goal_title, intent, actor)
        if relationship in {"same", "same_or_new"} and same is not None:
            return self._refine(same, goal_title, intent, actor)
        return self._create(workspace_id, journey_type, goal_title, intent, actor,
                            primary=not bool(active) or bool(intent.get("primary")))

    apply = process_intent

    def _apply_action(self, workspace_id: str, action: str, intent: dict, actor: str):
        journey_id = intent.get("journey_id")
        journey = self.service.get(workspace_id, str(journey_id)) if journey_id else self.service.resolve_active(workspace_id)
        if journey is None and action == "resume":
            paused = self.service.list(workspace_id, status="paused")
            journey = paused[0] if paused else None
        if journey is None:
            raise JourneyError("an active journey is required")
        if action == "resume":
            return self.service.resume(workspace_id, journey.id, actor=actor)
        if action in {"pause", "complete", "abandon"}:
            return self.service.set_status(workspace_id, journey.id, {
                "pause": "paused", "complete": "completed", "abandon": "abandoned",
            }[action], actor=actor)
        if action == "set_primary":
            return self.service.set_primary(workspace_id, journey.id, actor=actor)
        if action == "set_stage":
            return self.service.set_stage(workspace_id, journey.id, _text(intent.get("stage"), "stage"), actor=actor)
        if action == "set_focus":
            return self.service.set_focus_goal(
                workspace_id, journey.id, _text(intent.get("goal_id"), "goal_id"), actor=actor,
            )
        if action == "resolve_blocker":
            return self.service.resolve_blocker(
                workspace_id, journey.id, _text(intent.get("blocker_id"), "blocker_id"), actor=actor,
            )
        if action == "add_subgoal":
            return self._add_subgoal(
                journey, _text(intent.get("goal_title"), "goal_title"), intent, actor,
            )
        if action in {"add_decision", "update_decision"}:
            decision = dict(intent.get("decision") or {})
            decision.setdefault("id", new_id("decision"))
            decision.setdefault("goal_id", journey.current_focus_goal_id)
            decision.setdefault("source", "student")
            return self.service.add_decision(workspace_id, journey.id, decision, actor=actor)
        if action == "update_milestone":
            milestone = dict(intent.get("milestone") or {})
            milestone.setdefault("id", new_id("milestone"))
            milestone.setdefault("goal_id", journey.current_focus_goal_id)
            return self.service.upsert_milestone(workspace_id, journey.id, milestone, actor=actor)
        if action == "add_blocker":
            blocker = dict(intent.get("blocker") or {})
            blocker.setdefault("id", new_id("blocker"))
            blocker.setdefault("goal_id", journey.current_focus_goal_id)
            return self.service.add_blocker(workspace_id, journey.id, blocker, actor=actor)
        if action == "update_goal":
            changes = dict(intent.get("changes") or {})
            return self.service.update_goal(
                workspace_id, journey.id, _text(intent.get("goal_id"), "goal_id"),
                actor=actor, **changes,
            )
        if action == "update_journey":
            allowed = {"title", "current_objective", "target_outcome", "target_date"}
            changes = dict(intent.get("changes") or {})
            if set(changes) - allowed:
                raise JourneyError("unsupported coordinator journey fields")
            return self.service.update(workspace_id, journey.id, actor=actor, **changes)
        raise JourneyError("invalid journey action")

    def _create(self, workspace_id: str, journey_type: str, goal_title: str,
                intent: dict, actor: str, *, primary: bool):
        goal_id = new_id("goal")
        goal = {
            "id": goal_id, "parent_goal_id": None, "type": "primary",
            "title": goal_title, "status": "active",
            "priority": str(intent.get("priority") or "high"), "depends_on": [],
        }
        journey = self.service.create(
            workspace_id, journey_type, str(intent.get("title") or goal_title),
            primary=primary, actor=actor, goals=[goal], current_focus_goal_id=goal_id,
            current_stage=str(intent.get("current_stage") or JourneyStage.UNDERSTANDING.value),
            current_objective=intent.get("current_objective") or "Understand the starting context",
            target_outcome=intent.get("target_outcome") or goal_title,
        )
        return self._apply_details(journey, intent, actor)

    def _refine(self, journey, goal_title: str, intent: dict, actor: str):
        primary = next((goal for goal in journey.goals if goal.get("type") == "primary"), None)
        changes = {}
        if intent.get("title"):
            changes["title"] = intent["title"]
        if intent.get("target_outcome"):
            outcome = intent["target_outcome"]
            previous = journey.target_outcome
            if isinstance(previous, str) and isinstance(outcome, str):
                suffix = re.search(r"\s+in\s+([A-Z][A-Za-z .'-]+)$", previous)
                if suffix and not re.search(r"\s+in\s+[A-Z]", outcome):
                    outcome = f"{outcome} in {suffix.group(1)}"
            changes["target_outcome"] = outcome
        if intent.get("current_objective"):
            changes["current_objective"] = intent["current_objective"]
        if changes:
            journey = self.service.update(journey.workspace_id, journey.id, actor=actor, **changes)
        if primary and _normalized_title(goal_title) != _normalized_title(primary["title"]):
            # Refinement replaces a generic outcome; it does not manufacture a
            # second journey or discard the goal's stable identity.
            journey = self.service.update_goal(
                journey.workspace_id, journey.id, primary["id"], actor=actor,
                title=goal_title,
            )
        return self._apply_details(journey, intent, actor)

    def _add_subgoal(self, journey, title: str, intent: dict, actor: str):
        if any(_normalized_title(goal.get("title")) == _normalized_title(title) for goal in journey.goals):
            return journey
        primary = next((goal for goal in journey.goals if goal.get("type") == "primary"), None)
        if primary is None:
            raise JourneyError("parent journey has no primary goal")
        goal = {
            "id": new_id("goal"), "parent_goal_id": primary["id"], "type": "subgoal",
            "title": title, "status": "active",
            "priority": str(intent.get("priority") or "medium"),
            "depends_on": list(intent.get("depends_on") or []),
        }
        journey = self.service.add_goal(
            journey.workspace_id, journey.id, goal,
            focus=bool(intent.get("focus", True)), actor=actor,
        )
        return self._apply_details(journey, intent, actor)

    def _apply_details(self, journey, intent: dict, actor: str):
        if intent.get("decision"):
            decision = dict(intent["decision"])
            decision.setdefault("id", new_id("decision"))
            primary = next((goal for goal in journey.goals if goal.get("type") == "primary"), None)
            default_goal = (primary or {}).get("id") if decision.get("type") == "target_country" else journey.current_focus_goal_id
            decision.setdefault("goal_id", default_goal)
            decision.setdefault("source", "student")
            journey = self.service.add_decision(journey.workspace_id, journey.id, decision, actor=actor)
        if intent.get("milestone"):
            milestone = dict(intent["milestone"])
            milestone.setdefault("id", new_id("milestone"))
            milestone.setdefault("goal_id", journey.current_focus_goal_id)
            journey = self.service.upsert_milestone(journey.workspace_id, journey.id, milestone, actor=actor)
        if intent.get("blocker"):
            blocker = dict(intent["blocker"])
            blocker.setdefault("id", new_id("blocker"))
            blocker.setdefault("goal_id", journey.current_focus_goal_id)
            journey = self.service.add_blocker(journey.workspace_id, journey.id, blocker, actor=actor)
        return journey

    @staticmethod
    def _intent_from_message(message: str) -> dict | None:
        text = " ".join(str(message or "").strip().split())
        lower = text.casefold().replace(chr(0x2019), "'")
        durable = any(phrase in lower for phrase in (
            "i want", "i plan", "i'm planning", "my goal", "i need to",
            "i have decided", "i've decided", "i decided", "i also want",
            "let's explore", "help me explore", "can we explore",
        ))
        # A short clarification such as "I want MSc AI" remains durable; a
        # hypothetical or information-only question does not create state.
        if not durable or any(marker in lower for marker in (
            "just curious", "hypothetically", "what is ", "maybe", "might want",
        )):
            return None

        country = _extract_target(lower)
        specialty = _extract_specialty(text)
        if country and specialty and specialty.casefold() == f"msc {country}".casefold():
            specialty = None
        confirmed = any(marker in lower for marker in ("i have decided", "i've decided", "i decided", "i confirm"))

        if ("let's explore it" in lower or (
                re.search(r"\b(explore|exploring|discover)\b", lower)
                and re.search(r"\b(degree|study|subject|field|direction|career|options?)\b", lower))):
            return {
                "journey_type": "direction_discovery", "relationship": "same_or_new",
                "goal_title": "Discover a study direction",
            }

        if re.search(r"\b(internship|intern)\b", lower):
            return {
                "journey_type": "internship_search", "relationship": "separate",
                "goal_title": "Find a suitable internship",
                "title": "Internship search", "target_outcome": "Secure a suitable internship",
                "current_objective": "Define internship targets and readiness",
            }
        if re.search(r"\b(ielts|toefl|english requirement|language test)\b", lower):
            return {
                "journey_type": "skill_development", "relationship": "subgoal",
                "goal_title": "Meet the English-language requirement",
                "current_objective": "Plan the language requirement",
            }
        if re.search(r"\b(phd|doctorate|doctoral)\b", lower):
            kind = "phd_research"
            base = "Pursue a suitable doctoral program"
        elif re.search(r"\b(master'?s|masters|msc|m\.sc\.|postgraduate)\b", lower):
            kind = "postgraduate_admission"
            level = specialty or "master's"
            base = f"Gain admission to a suitable {level} program"
        elif re.search(r"\b(bachelor'?s|undergraduate|college admission)\b", lower):
            kind = "undergraduate_admission"
            base = "Gain admission to a suitable undergraduate program"
        elif re.search(r"\b(change careers?|career transition|move into)\b", lower):
            kind = "career_transition"
            base = "Complete a career transition"
        elif re.search(r"\b(explore careers?|career options?)\b", lower):
            kind = "career_exploration"
            base = "Choose a suitable career direction"
        elif re.search(r"\b(improve my grades?|academic recovery|raise my gpa)\b", lower):
            kind = "academic_recovery"
            base = "Improve academic standing"
        elif re.search(r"\b(learn|develop|improve)\b", lower) and re.search(r"\b(skill|coding|programming|data|design)\b", lower):
            kind = "skill_development"
            base = "Develop the target skill"
        else:
            return None

        outcome = f"{base}{f' in {country}' if country else ''}"
        result: dict[str, Any] = {
            "journey_type": kind, "relationship": "same_or_new",
            "goal_title": outcome, "title": outcome,
            "target_outcome": outcome,
            "current_objective": "Understand academic background" if "admission" in kind or kind == "phd_research" else "Understand the starting context",
        }
        if confirmed and country:
            result["decision"] = {
                "type": "target_country", "value": country,
                "status": "confirmed", "source": "student",
            }
        return result


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JourneyError(f"{name} is required")
    return value.strip()


def _normalized_title(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _extract_target(lower: str) -> str | None:
    ending = r"([a-z][a-z .'-]{1,35}?)(?:[,.!?]|\s+(?:for|while|after|and)\b|$)"
    # Prefer "in X" so the infinitive in "want to do a master's in X"
    # cannot swallow the actual target.
    match = re.search(r"\bin\s+" + ending, lower)
    if match is None:
        match = re.search(r"\bto\s+" + ending, lower)
    if match is None:
        return None
    value = " ".join(word.capitalize() for word in match.group(1).split())
    return value if value.split()[0] not in {"Do", "Get", "Study", "Move", "Pursue", "Learn"} else None


def _extract_specialty(text: str) -> str | None:
    match = re.search(r"\b(?:MSc|MS|Master'?s?)(?:\s+(?:in|of))?\s+([A-Za-z][A-Za-z &-]{1,35})", text, re.I)
    if not match:
        return None
    value = re.split(r"\s+(?:in|to|while|and)\s+", match.group(1), maxsplit=1, flags=re.I)[0].strip()
    return (f"MSc {value}" if value and value.casefold() not in
            {"program", "degree", "abroad", "abroad for a"} else None)


def _extract_confirmed_target(text: str) -> str | None:
    match = re.search(
        r"\b(?:i\s+(?:have\s+)?(?:decided(?:\s+on)?|confirm|choose|chose)|definitely)\s+([A-Za-z][A-Za-z .'-]{1,35}?)(?:\s+(?:after|for|while|because)\b|[,.!?]|$)",
        str(text or ""), re.I,
    )
    if not match:
        return None
    return " ".join(word.capitalize() for word in match.group(1).split())
