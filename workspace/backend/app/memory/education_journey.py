"""Derive broad education history and gaps without inventing records."""

from typing import Any

from .student_snapshot import StudentSnapshot


LEVEL_GROUPS = {
    "pre_university": frozenset({"school", "secondary", "upper_secondary"}),
    "undergraduate": frozenset({"diploma", "associate", "bachelor", "professional"}),
    "postgraduate": frozenset({"master", "mphil", "doctorate"}),
}
GROUP_ORDER = {"pre_university": 0, "undergraduate": 1, "postgraduate": 2, "unknown": 3}
KNOWN_HISTORY_STATUSES = frozenset({"completed", "current"})


def education_group(level: Any) -> str:
    normalized = str(level or "").strip().casefold()
    for group, levels in LEVEL_GROUPS.items():
        if normalized in levels:
            return group
    return "unknown"


class EducationJourneyService:
    def evaluate(self, snapshot: StudentSnapshot) -> dict:
        records = []
        for source in snapshot.records.get("education", []):
            row = dict(source)
            row["group"] = education_group(row.get("canonical_level"))
            records.append(row)

        records.sort(key=self._sort_key)
        gaps: list[dict] = []
        unknown = [row for row in records if row["group"] == "unknown"]
        for row in unknown:
            qualification = row.get("qualification_name") or "this qualification"
            gaps.append({
                "key": f"education_level:{row['id']}",
                "expectedGroup": "unknown",
                "status": "clarification_required",
                "recordId": row["id"],
                "question": f"What level best describes {qualification}?",
            })

        # An unclassified record could itself be the missing prerequisite, so
        # ask what it is before asserting that a broad history group is absent.
        if not unknown:
            completed_history = {
                row["group"] for row in records
                if row.get("academic_status") in KNOWN_HISTORY_STATUSES
            }
            observed_history = {
                row["group"] for row in records
                if row.get("academic_status") != "planned"
            }
            if "postgraduate" in observed_history and "undergraduate" not in completed_history:
                gaps.append({
                    "key": "undergraduate_history",
                    "expectedGroup": "undergraduate",
                    "status": "missing_information",
                    "question": "What qualification did you complete before postgraduate study?",
                })
            if "undergraduate" in observed_history and "pre_university" not in completed_history:
                gaps.append({
                    "key": "pre_university_history",
                    "expectedGroup": "pre_university",
                    "status": "missing_information",
                    "question": "What qualification did you complete before undergraduate study?",
                })

        return {"education": records, "gaps": gaps}

    @staticmethod
    def _sort_key(row: dict) -> tuple:
        known_date = row.get("end_date") or (
            str(row["graduation_year"]) if row.get("graduation_year") else None
        ) or row.get("start_date")
        return (known_date is None, known_date or "", GROUP_ORDER[row["group"]], row.get("id") or "")
