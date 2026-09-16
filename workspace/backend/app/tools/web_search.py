from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.config import config

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class WebSearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[dict]: ...


class BraveWebSearchProvider(WebSearchProvider):
    def __init__(self, api_key: str, base_url: str = BRAVE_SEARCH_URL):
        self.api_key = api_key
        self.base_url = base_url

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        count = max(1, min(limit, 20))
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                self.base_url, params={"q": query, "count": count},
                headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            )
            response.raise_for_status()
        items = (response.json().get("web") or {}).get("results") or []
        return [{
            "title": item.get("title", ""), "url": item.get("url", ""),
            "snippet": item.get("description", ""), "source": "brave",
        } for item in items[:count]]


class TavilyWebSearchProvider(WebSearchProvider):
    def __init__(self, api_key: str, base_url: str = TAVILY_SEARCH_URL):
        self.api_key = api_key
        self.base_url = base_url

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        count = max(1, min(limit, 20))
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                self.base_url,
                json={"query": query, "max_results": count},
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
        items = response.json().get("results") or []
        return [{
            "title": item.get("title", ""), "url": item.get("url", ""),
            "snippet": item.get("content", ""), "source": "tavily",
        } for item in items[:count]]


_provider_override: Optional[WebSearchProvider] = None


def set_web_search_provider(provider: Optional[WebSearchProvider]) -> None:
    global _provider_override
    _provider_override = provider


def get_web_search_provider() -> Optional[WebSearchProvider]:
    if _provider_override is not None:
        return _provider_override
    name = (config.WEB_SEARCH_PROVIDER or "").strip().lower()
    if not name or not config.WEB_SEARCH_API_KEY:
        return None
    if name == "brave":
        return BraveWebSearchProvider(
            config.WEB_SEARCH_API_KEY,
            config.WEB_SEARCH_BASE_URL or BRAVE_SEARCH_URL,
        )
    if name == "tavily":
        return TavilyWebSearchProvider(
            config.WEB_SEARCH_API_KEY,
            config.WEB_SEARCH_BASE_URL or TAVILY_SEARCH_URL,
        )
    return None
