# -*- coding: utf-8 -*-
"""Placement AI's hosted origins, pinned.

Placement AI is its own product. The hosted application and every auth route
live on app.placement-ai.com; the backend on api.placement-ai.com. Nothing in
the hosted product path may depend on an OpenAgents host.

These defaults are what a deployment gets when it sets no environment at all,
so a wrong one here is a silent production misconfiguration: OAuth callbacks
and platform webhooks would be registered against a domain this product does
not own.
"""

import re

import pytest

from app.config import config

APP_ORIGIN = "https://app.placement-ai.com"
API_ORIGIN = "https://api.placement-ai.com"


class TestCanonicalOrigins:
    def test_public_api_base(self):
        assert config.PUBLIC_API_BASE == API_ORIGIN

    def test_frontend_base(self):
        assert config.FRONTEND_BASE_URL == APP_ORIGIN

    def test_google_oauth_callback_returns_to_our_api(self):
        assert config.GOOGLE_OAUTH_REDIRECT_URI.startswith(API_ORIGIN + "/")

    @pytest.mark.parametrize("value", [
        "PUBLIC_API_BASE", "FRONTEND_BASE_URL", "GOOGLE_OAUTH_REDIRECT_URI", "EMAIL_FROM",
    ])
    def test_no_openagents_host_in_the_product_path(self, value):
        assert "openagents.org" not in getattr(config, value)

    @pytest.mark.parametrize("origin", [APP_ORIGIN, API_ORIGIN])
    def test_placement_is_spelled_correctly(self, origin):
        """A typo'd host is a silent outage: DNS, the Supabase redirect
        allowlist and CORS all have to agree on one spelling."""
        assert re.fullmatch(r"https://(app|api)\.placement-ai\.com", origin)

    def test_defaults_are_overridable_for_self_hosting(self):
        """Every one of these is an env var, so a self-hosted or local
        deployment is not pinned to Placement AI's own domains."""
        import inspect

        source = inspect.getsource(type(config))
        for name in ("PUBLIC_API_BASE", "FRONTEND_BASE_URL",
                     "GOOGLE_OAUTH_REDIRECT_URI", "EMAIL_FROM"):
            assert f'os.environ.get(\n        "{name}"' in source or \
                   f'os.environ.get("{name}"' in source, name
