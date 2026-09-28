"""Validation for the versioned capability contract."""

import re

from .contract import CapabilityContract

_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


class InvalidCapabilityManifest(ValueError):
    pass


class CapabilitySchemaError(ValueError):
    pass


def validate_instance(schema: dict, value, label: str) -> None:
    """Small dependency-free root validator for the contract boundary."""
    expected = schema.get("type")
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected in checks and not checks[expected](value):
        raise CapabilitySchemaError(f"{label} must be {expected}")
    if expected == "object":
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise CapabilitySchemaError(f"{label} missing required field: {missing[0]}")


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
    if contract.timeout_seconds <= 0 or contract.retry.max_attempts <= 0:
        raise InvalidCapabilityManifest("timeout and retry attempts must be positive")
    if contract.provider != "native":
        raise InvalidCapabilityManifest("only native capability providers are supported")
