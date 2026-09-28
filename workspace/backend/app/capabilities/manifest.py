"""Validation for the versioned capability contract."""

import re

from app.tools.schema import SchemaValidationError as CapabilitySchemaError
from app.tools.schema import validate_schema_instance as validate_instance
from .contract import CapabilityContract, FallbackPolicy

_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


class InvalidCapabilityManifest(ValueError):
    pass


def validate(contract: CapabilityContract) -> None:
    if not _ID.fullmatch(contract.id):
        raise InvalidCapabilityManifest("capability id must be a dotted lowercase identifier")
    if not _SEMVER.fullmatch(contract.version):
        raise InvalidCapabilityManifest("capability version must use semantic versioning")
    if not contract.name.strip() or not contract.description.strip():
        raise InvalidCapabilityManifest("name and description are required")
    for name, schema in (("input", contract.input_schema), ("output", contract.output_schema)):
        if not isinstance(schema, dict) or schema.get("type") not in _SCHEMA_TYPES:
            raise InvalidCapabilityManifest(f"{name} schema must declare a supported type")
    if contract.approval not in {"none", "always", "risk_based"}:
        raise InvalidCapabilityManifest("invalid approval policy")
    if contract.fallback_policy not in set(FallbackPolicy):
        raise InvalidCapabilityManifest("invalid fallback policy")
    for task_type in contract.owns_task_types:
        if not re.fullmatch(r"^[a-z][a-z0-9_]{1,63}$", task_type):
            raise InvalidCapabilityManifest(f"invalid owned task type: {task_type}")
    if contract.timeout_seconds <= 0 or contract.retry.max_attempts <= 0:
        raise InvalidCapabilityManifest("timeout and retry attempts must be positive")
    if contract.provider != "native":
        raise InvalidCapabilityManifest("only native capability providers are supported")
