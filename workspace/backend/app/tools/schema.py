"""Shared recursive JSON-schema subset for tools and capabilities."""

from typing import Any


class SchemaValidationError(ValueError):
    pass


def validate_schema_instance(schema: dict, value: Any, path: str = "value") -> None:
    expected = schema.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if expected in checks and not checks[expected](value):
        raise SchemaValidationError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path} must be one of {schema['enum']}")
    if expected in ("number", "integer") and checks[expected](value):
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        if minimum is not None and value < minimum:
            raise SchemaValidationError(f"{path} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise SchemaValidationError(f"{path} must be <= {maximum}")
    if expected == "string" and isinstance(value, str):
        minimum, maximum = schema.get("minLength"), schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            raise SchemaValidationError(f"{path} must be at least {minimum} characters")
        if maximum is not None and len(value) > maximum:
            raise SchemaValidationError(f"{path} must be at most {maximum} characters")
    if expected == "array" and isinstance(value, list):
        minimum, maximum = schema.get("minItems"), schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise SchemaValidationError(f"{path} must have at least {minimum} item(s)")
        if maximum is not None and len(value) > maximum:
            raise SchemaValidationError(f"{path} must have at most {maximum} item(s)")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_schema_instance(schema["items"], item, f"{path}[{index}]")
    if expected == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise SchemaValidationError(f"{path} missing required field: {key}")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise SchemaValidationError(f"{path} has unknown field: {sorted(unknown)[0]}")
        for key, child in properties.items():
            if key in value:
                validate_schema_instance(child, value[key], f"{path}.{key}")
