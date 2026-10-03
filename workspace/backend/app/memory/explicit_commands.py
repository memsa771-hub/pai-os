"""Narrow authorization of semantic owner commands, without language parsing."""

from .voice_attribution import contained


def validated_commands(raw: object, message: str) -> list[dict]:
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        operation, quote = item.get("operation"), item.get("quote")
        if (not isinstance(operation, str) or operation not in {"upsert", "retract", "forget"}
                or not contained(quote, message)):
            continue
        key = item.get("key")
        scope = item.get("content_scope")
        if operation in {"upsert", "retract"} and isinstance(key, str) and key:
            result.append({"operation": operation, "quote": quote, "key": key})
        elif (operation == "forget" and isinstance(scope, str) and 3 <= len(scope) <= 200
              and contained(scope, quote)):
            result.append({"operation": operation, "quote": quote,
                           "content_scope": scope})
    return result


def authorizes_fact(commands: list[dict] | None, operation: str, key: str,
                    current_value=None, proposed_value=None) -> bool:
    return isinstance(key, str) and any(
        item.get("operation") == operation and item.get("key") == key
        for item in commands or [] if isinstance(item, dict))


def authorizes_memory_forget(commands: list[dict] | None, content: str) -> bool:
    if not isinstance(content, str) or any(char in content for char in "%_"):
        return False
    return any(item.get("operation") == "forget"
               and item.get("content_scope") == content
               for item in commands or [] if isinstance(item, dict))
