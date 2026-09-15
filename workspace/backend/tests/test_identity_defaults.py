# -*- coding: utf-8 -*-
"""
Identity providers must be opt-in.

Sign in with Apple's audience allowlist (APPLE_CLIENT_IDS) is empty by
default, so a baked-in bundle id would make every deployment trust identity
tokens minted for that tenant. That matters because POST
/v1/workspaces/{id}/claim accepts a bearer with no workspace token and claims
any workspace whose creator_email is unset, after which
_verify_workspace_access grants that email full access.

Supabase doesn't have an equivalent footgun here: SUPABASE_URL defaults to an
obvious placeholder (not a real, trusted project), and verification only ever
trusts a token whose `iss` matches the configured project.
"""

import importlib


def _fresh_config(monkeypatch, **env):
    """Re-import the config module with a controlled environment."""
    monkeypatch.delenv("APPLE_CLIENT_IDS", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app.config as config_module
    return importlib.reload(config_module).config


def test_apple_client_ids_defaults_to_empty(monkeypatch):
    assert _fresh_config(monkeypatch).APPLE_CLIENT_IDS == ""


def test_apple_client_ids_still_configurable(monkeypatch):
    cfg = _fresh_config(monkeypatch, APPLE_CLIENT_IDS="com.example.app,com.example.svc")
    assert cfg.APPLE_CLIENT_IDS == "com.example.app,com.example.svc"


def test_apple_verification_fails_closed_without_client_ids(monkeypatch):
    """With no client ids configured, Apple token verification must reject."""
    _fresh_config(monkeypatch)
    import app.firebase_auth as firebase_auth
    importlib.reload(firebase_auth)
    assert firebase_auth.verify_apple_token("any.token.value") is None
