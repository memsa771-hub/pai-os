"""Milestone helpers shared by journey clients."""


def next_incomplete(milestones: list[dict]) -> dict | None:
    return next((item for item in milestones if item.get("status") != "completed"), None)
