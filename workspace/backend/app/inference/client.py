"""OpenAI-compatible inference transport used by PAI services.

Provider catalogs and user-managed credentials intentionally do not live here.
PAI selects its model, key, and optional gateway once through server config.
"""

from typing import Optional

from openai import AsyncOpenAI, OpenAI


REASONING_EFFORTS = ("none", "low", "medium", "high")
_NO_NONE_MODELS_PREFIXES = ("gpt-5-mini",)


def _tool_call_effort_for(model: str) -> str:
    name = (model or "").lower()
    return "minimal" if name.startswith(_NO_NONE_MODELS_PREFIXES) else "none"


def _reasoning_effort_for(
    model: str, effort: Optional[str], has_tools: bool,
) -> Optional[str]:
    """Return a supported reasoning setting, or omit the parameter."""
    name = (model or "").lower()
    if not name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        return None
    if has_tools:
        return _tool_call_effort_for(model)
    return effort if effort in REASONING_EFFORTS else None


def _token_limit_kwarg(model: str) -> str:
    """Return the output-token parameter accepted by the selected model."""
    name = (model or "").lower()
    if name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        return "max_completion_tokens"
    return "max_tokens"


def create_client(
    api_key: str,
    base_url: Optional[str] = None,
    timeout: float = 120,
) -> AsyncOpenAI:
    """Create the single OpenAI-compatible client used by PAI."""
    from app.config import config

    kwargs: dict = {
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": config.LLM_MAX_RETRIES,
    }
    if base_url:
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized += "/v1"
        kwargs["base_url"] = normalized
    return AsyncOpenAI(**kwargs)


def create_sync_client(
    api_key: str,
    base_url: Optional[str] = None,
    timeout: float = 120,
) -> OpenAI:
    """Create a synchronous client for transaction-bound classifiers."""
    from app.config import config

    kwargs: dict = {
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": config.LLM_MAX_RETRIES,
    }
    if base_url:
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized += "/v1"
        kwargs["base_url"] = normalized
    return OpenAI(**kwargs)


async def chat_completion(
    api_key: str,
    model: str,
    messages: list[dict],
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Return text from the configured PAI-compatible chat endpoint."""
    client = create_client(api_key, base_url=base_url)
    api_messages = []
    if system_prompt:
        api_messages.append({"role": "system", "content": system_prompt})
    api_messages.extend(messages)

    kwargs: dict = {"model": model, "messages": api_messages}
    effort = _reasoning_effort_for(model, reasoning_effort, has_tools=False)
    if effort:
        kwargs["reasoning_effort"] = effort
    if max_tokens:
        kwargs[_token_limit_kwarg(model)] = max_tokens

    try:
        response = await client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        text = message.content or ""
        if not text and getattr(message, "reasoning", None):
            text = message.reasoning
        return text
    finally:
        await client.close()


async def chat_completion_tools(
    api_key: str,
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    base_url: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> dict:
    """Return an assistant message, including normalized function calls."""
    client = create_client(api_key, base_url=base_url)
    api_messages: list[dict] = []
    if system_prompt:
        api_messages.append({"role": "system", "content": system_prompt})
    api_messages.extend(messages)

    kwargs: dict = {"model": model, "messages": api_messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    effort = _reasoning_effort_for(model, reasoning_effort, has_tools=bool(tools))
    if effort:
        kwargs["reasoning_effort"] = effort
    if max_tokens:
        kwargs[_token_limit_kwarg(model)] = max_tokens

    try:
        response = await client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        result: dict = {"role": "assistant", "content": message.content or ""}
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments or "{}",
                    },
                }
                for call in tool_calls
            ]
        return result
    finally:
        await client.close()
