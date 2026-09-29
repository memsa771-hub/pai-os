"""Server-managed language-model inference for PAI."""

from app.inference.client import chat_completion, chat_completion_tools

__all__ = ["chat_completion", "chat_completion_tools"]
