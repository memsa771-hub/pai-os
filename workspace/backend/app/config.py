# -*- coding: utf-8 -*-
"""
Workspace backend configuration.

All settings are loaded from environment variables.
"""

import os


class Config:
    """Application configuration loaded from environment variables."""

    # Database
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:dev@localhost:5432/openagents_workspace",
    )

    # Auth mode: "workspace_token" (self-hosted) or "firebase" (hosted)
    AUTH_MODE: str = os.environ.get("AUTH_MODE", "workspace_token")

    # Firebase service account credentials, the whole JSON key file as a
    # single-line string. Firebase is no longer a human-login provider here
    # (see app.firebase_auth / Supabase below) — this is kept only because
    # services/fcm_client.py sends mobile push through Firebase Cloud
    # Messaging, which is a separate, authenticated API call that shares the
    # same Admin SDK app (_init_firebase()). Without it, push is silently off.
    FIREBASE_CREDENTIALS_JSON: str = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")

    # Supabase Auth — the sole human-identity provider for web/desktop (and,
    # later, mobile). SUPABASE_ANON_KEY is the public/publishable key that also
    # ships in every client bundle, so it is not a secret; there is
    # deliberately no service-role key or JWT signing secret here (see
    # app.firebase_auth.verify_supabase_claims, which verifies tokens via
    # Supabase's own JWKS/introspection instead of a shared secret).
    #
    # TODO(deploy): require these public values from environment configuration.
    # They are intentionally defaults only for the current development phase.
    SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "https://qhrzlmfzdeulhdzpadtn.supabase.co")
    SUPABASE_ANON_KEY: str = os.environ.get("SUPABASE_ANON_KEY", "sb_publishable_W_ITKg52Rr3G0eeoi4wPLQ_AdSTiKPD")

    # Blast-radius cap for POST /v1/auth/sign-in-username (per process, sliding
    # hour, keyed by client IP) — mirrors PILOT_MAX_GRANTS_PER_HOUR below.
    SIGN_IN_USERNAME_MAX_ATTEMPTS_PER_HOUR: int = int(
        os.environ.get("SIGN_IN_USERNAME_MAX_ATTEMPTS_PER_HOUR", "20")
    )

    # Sign in with Apple. Native ("Sign in with Apple" on the iOS app) issues an
    # identity token whose `aud` is the app's bundle id; web/services flows use
    # the Services ID instead. Accept a comma-separated allowlist so both work.
    #
    # Empty by default: a bundle id baked in here is an identity tenant every
    # deployment would trust. Set it in the environment for the deployment
    # that owns that bundle id.
    APPLE_CLIENT_IDS: str = os.environ.get("APPLE_CLIENT_IDS", "")

    # Apple push used to be sent direct to APNs from here (APNS_AUTH_KEY /
    # APNS_KEY_ID / APNS_TEAM_ID / APNS_BUNDLE_ID / APNS_ENVIRONMENT). It now
    # goes through FCM like Android does, so those vars are gone: upload the
    # .p8 key to the Firebase console (Project settings → Cloud Messaging →
    # APNs Authentication Key) instead, and set FIREBASE_CREDENTIALS_JSON here.

    # Identity mode: "standalone" (own agent table) or "shared" (external agent_ids)
    IDENTITY_MODE: str = os.environ.get("IDENTITY_MODE", "standalone")

    # Agent offline timeout in seconds
    AGENT_TIMEOUT_SECONDS: int = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "60"))

    # Reject /v1/leave and /v1/heartbeat calls that lack valid workspace
    # credentials. Off by default for one release (warn-and-accept) so any
    # client that never sent a token keeps working while offenders are logged.
    ENFORCE_AGENT_LIFECYCLE_AUTH: bool = os.environ.get("ENFORCE_AGENT_LIFECYCLE_AUTH", "false").lower() in ("true", "1", "yes")

    # CORS origins (comma-separated)
    CORS_ORIGINS: str = os.environ.get("CORS_ORIGINS", "*")

    # File storage
    FILE_STORAGE_BACKEND: str = os.environ.get("FILE_STORAGE_BACKEND", "local")  # "local" or "s3"
    FILE_STORAGE_PATH: str = os.environ.get("FILE_STORAGE_PATH", "/tmp/openagents_files")
    S3_BUCKET: str = os.environ.get("S3_BUCKET", "")
    S3_REGION: str = os.environ.get("S3_REGION", "us-east-1")
    MAX_FILE_SIZE: int = int(os.environ.get("MAX_FILE_SIZE", str(50 * 1024 * 1024)))  # 50MB

    # Mobile app releases (served by /v1/app/version).
    #
    # The build number is what the app compares — it is the `+N` half of the
    # Flutter version (`1.0.1+25`) and must increase with every release.
    # MIN_BUILD is the forced-update floor: a client below it blocks itself
    # until the user updates, so raise it only for a release older clients
    # genuinely cannot run against. Left at 0, nothing is ever forced.
    # A LATEST_BUILD of 0 means "not configured" and offers no update at all.
    APP_ANDROID_LATEST_VERSION: str = os.environ.get("APP_ANDROID_LATEST_VERSION", "")
    APP_ANDROID_LATEST_BUILD: int = int(os.environ.get("APP_ANDROID_LATEST_BUILD", "0"))
    APP_ANDROID_MIN_BUILD: int = int(os.environ.get("APP_ANDROID_MIN_BUILD", "0"))
    APP_ANDROID_UPDATE_URL: str = os.environ.get("APP_ANDROID_UPDATE_URL", "")
    APP_ANDROID_RELEASE_NOTES: str = os.environ.get("APP_ANDROID_RELEASE_NOTES", "")

    APP_IOS_LATEST_VERSION: str = os.environ.get("APP_IOS_LATEST_VERSION", "")
    APP_IOS_LATEST_BUILD: int = int(os.environ.get("APP_IOS_LATEST_BUILD", "0"))
    APP_IOS_MIN_BUILD: int = int(os.environ.get("APP_IOS_MIN_BUILD", "0"))
    APP_IOS_UPDATE_URL: str = os.environ.get("APP_IOS_UPDATE_URL", "")
    APP_IOS_RELEASE_NOTES: str = os.environ.get("APP_IOS_RELEASE_NOTES", "")

    # LLM Router — uses a small model to decide agent turn-taking in multi-agent threads
    # Provider: "anthropic" (default) or "openai" (any OpenAI-compatible endpoint)
    ROUTER_LLM_ENABLED: bool = os.environ.get("ROUTER_LLM_ENABLED", "true").lower() in ("true", "1", "yes")
    ROUTER_LLM_PROVIDER: str = os.environ.get("ROUTER_LLM_PROVIDER", "anthropic")  # "anthropic" or "openai"
    ROUTER_LLM_MODEL: str = os.environ.get("ROUTER_LLM_MODEL", "")  # auto-detected from provider if empty
    ROUTER_LLM_API_KEY: str = os.environ.get("ROUTER_LLM_API_KEY", "")  # universal key (checked first)
    ROUTER_LLM_BASE_URL: str = os.environ.get("ROUTER_LLM_BASE_URL", "")  # custom endpoint for openai provider
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")  # fallback for anthropic provider

    # Cloud agents
    CLOUD_AGENT_MAX_CONTEXT_MESSAGES: int = int(os.environ.get("CLOUD_AGENT_MAX_CONTEXT_MESSAGES", "100"))
    # Whole-request char budget (system prompt + history + trigger message).
    # Chars are a rough token proxy and the ratio varies by language (CJK text
    # can approach 1 token per char) — the default assumes frontier models
    # with 200K+ windows and leaves output-token headroom; lower it when
    # targeting small custom models.
    CLOUD_AGENT_MAX_CONTEXT_CHARS: int = int(os.environ.get("CLOUD_AGENT_MAX_CONTEXT_CHARS", "60000"))
    CLOUD_AGENT_MAX_DEPTH: int = int(os.environ.get("CLOUD_AGENT_MAX_DEPTH", "3"))

    # PAI Counselor — Placement AI's primary education counselor (auto-added
    # to every workspace). Its credentials are SERVER-HELD and shared across all
    # workspaces: never persisted per-workspace and never exposed to the frontend.
    # PAI Counselor is only provisioned when enabled AND a key is configured, so
    # self-hosted deployments without a key simply don't get it.
    PAI_ENABLED: bool = os.environ.get("PAI_ENABLED", "false").lower() in ("true", "1", "yes")
    PAI_API_KEY: str = os.environ.get("PAI_API_KEY", "")
    PAI_BASE_URL: str = os.environ.get("PAI_BASE_URL", "https://api.openai.com/v1")
    # minimax-m2.5: fastest reliable tool-looper on the gateway (2026-08-27
    # screen of all 23 models: ~7s/2-turn loop, 4/4 valid reps, all quality
    # probes passed; deepseek-4-flash had degraded to >40s continuation turns).
    PAI_MODEL: str = os.environ.get("PAI_MODEL", "gpt-5.4-mini")
    # Safety cap on the tool-calling loop per user message.
    PAI_MAX_TOOL_ITERATIONS: int = int(os.environ.get("PAI_MAX_TOOL_ITERATIONS", "6"))

    # Google OAuth (for "Sign in with Google" Gemini integration)
    GOOGLE_OAUTH_CLIENT_ID: str = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_SECRET: str = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    GOOGLE_OAUTH_REDIRECT_URI: str = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://workspace-endpoint.openagents.org/v1/cloud-agents/google/callback",
    )

    # Invitations & transactional email. Invite links point at the workspace
    # frontend; email delivery goes through Resend when a key is configured
    # (otherwise invites are created but the email step is skipped).
    FRONTEND_BASE_URL: str = os.environ.get("FRONTEND_BASE_URL", "https://workspace.openagents.org")
    RESEND_API_KEY: str = os.environ.get("RESEND_API_KEY", "")
    EMAIL_FROM: str = os.environ.get("EMAIL_FROM", "OpenAgents <noreply@openagents.org>")
    INVITE_TTL_DAYS: int = int(os.environ.get("INVITE_TTL_DAYS", "7"))

    # Chat-platform integrations (Slack / Telegram bridges). The public base
    # URL is what external platforms call back to — Telegram setWebhook and
    # the Slack Events API URL both derive from it.
    PUBLIC_API_BASE: str = os.environ.get(
        "PUBLIC_API_BASE", "https://workspace-endpoint.openagents.org"
    )
    # The official "OpenAgents" Slack app (one-click Add to Slack). All three
    # come from the app's Basic Information page; when unset, the UI falls
    # back to the bring-your-own-app flow (docs/slack-app-setup.md).
    SLACK_CLIENT_ID: str = os.environ.get("SLACK_CLIENT_ID", "")
    SLACK_CLIENT_SECRET: str = os.environ.get("SLACK_CLIENT_SECRET", "")
    SLACK_SIGNING_SECRET: str = os.environ.get("SLACK_SIGNING_SECRET", "")

    # API credits campaign — grants free model-gateway credits as users hit
    # onboarding milestones (see app/services/campaign.py). DISABLED by
    # default: self-hosted deployments have no gateway master key and should
    # never see the campaign UI. The official deployment enables it via env.
    CAMPAIGN_ENABLED: bool = os.environ.get("CAMPAIGN_ENABLED", "false").lower() in ("true", "1", "yes")
    CAMPAIGN_GATEWAY_URL: str = os.environ.get("CAMPAIGN_GATEWAY_URL", "https://api-gateway.openagents.org")
    CAMPAIGN_GATEWAY_MASTER_KEY: str = os.environ.get("CAMPAIGN_GATEWAY_MASTER_KEY", "")
    CAMPAIGN_TOTAL_CAP_USD: float = float(os.environ.get("CAMPAIGN_TOTAL_CAP_USD", "100"))
    CAMPAIGN_DAILY_GRANT_USD: float = float(os.environ.get("CAMPAIGN_DAILY_GRANT_USD", "10"))

    # Pilot User Program admin console (internal.openagents.org/pages/pilot-console).
    # Endpoints under /v1/admin/pilot are enabled ONLY when PILOT_ADMIN_SECRET is
    # set; every call must present it in X-Admin-Secret. Amount is fixed
    # server-side (never client-supplied). Eligibility = a launcher/CLI agent
    # connected + >= PILOT_MIN_ACTIVE_DAYS distinct UTC days (not necessarily
    # consecutive) with a human message AND a qualifying agent reply in the
    # same owned workspace, within the last PILOT_WINDOW_DAYS.
    PILOT_ADMIN_SECRET: str = os.environ.get("PILOT_ADMIN_SECRET", "")
    PILOT_GRANT_USD: float = float(os.environ.get("PILOT_GRANT_USD", "300"))
    PILOT_MIN_ACTIVE_DAYS: int = int(os.environ.get("PILOT_MIN_ACTIVE_DAYS", "3"))
    PILOT_WINDOW_DAYS: int = int(os.environ.get("PILOT_WINDOW_DAYS", "30"))
    # Blast-radius cap for the grant endpoint (per process, sliding hour).
    PILOT_MAX_GRANTS_PER_HOUR: int = int(os.environ.get("PILOT_MAX_GRANTS_PER_HOUR", "30"))

    # In-app feedback forwarding. Feedback rows always land in the DB; when
    # this is set they are also emailed (via Resend) to the team.
    FEEDBACK_EMAIL_TO: str = os.environ.get("FEEDBACK_EMAIL_TO", "")

    # Server
    HOST: str = os.environ.get("HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("PORT", "8000"))


config = Config()
