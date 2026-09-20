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
    DB_POOL_SIZE: int = int(os.environ.get("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW: int = int(os.environ.get("DB_MAX_OVERFLOW", "5"))
    DB_POOL_TIMEOUT: int = int(os.environ.get("DB_POOL_TIMEOUT", "5"))
    DB_POOL_RECYCLE: int = int(os.environ.get("DB_POOL_RECYCLE", "300"))
    APP_ENV: str = os.environ.get("APP_ENV", "development")

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
    # Required from the environment (see workspace/.env.example) — no default
    # project baked into source, so a misconfigured deployment fails loudly
    # (empty string) instead of silently talking to whichever project used to
    # be hardcoded here.
    SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
    SUPABASE_ANON_KEY: str = os.environ.get("SUPABASE_ANON_KEY", "")

    # Failed sign-ins allowed against ONE username per hour, before that
    # account is locked out (per process, sliding window). Keyed by username,
    # not by client address: the attack this stops is a brute force against a
    # single account, and an address key would instead punish every student
    # behind one school NAT or reverse proxy for each other's typos.
    SIGN_IN_USERNAME_MAX_ATTEMPTS_PER_HOUR: int = int(
        os.environ.get("SIGN_IN_USERNAME_MAX_ATTEMPTS_PER_HOUR", "20")
    )

    # Loose per-source backstop for the two unauthenticated auth endpoints,
    # covering what a per-username limit cannot: password spraying (one common
    # password against many accounts) and username enumeration. Deliberately
    # generous, because behind a proxy or a campus NAT this is shared by every
    # student at once — it is a ceiling on abuse, not a login quota.
    AUTH_MAX_REQUESTS_PER_SOURCE_PER_HOUR: int = int(
        os.environ.get("AUTH_MAX_REQUESTS_PER_SOURCE_PER_HOUR", "120")
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
    WORKSPACE_ENDPOINT: str = os.environ.get("WORKSPACE_ENDPOINT", "")

    # Redis is an optional cache/PubSub accelerator. Short timeouts keep
    # PostgreSQL-backed requests responsive during Redis outages.
    REDIS_URL: str = os.environ.get("REDIS_URL", "").strip()
    REDIS_CONNECT_TIMEOUT: float = float(os.environ.get("REDIS_CONNECT_TIMEOUT", "0.5"))
    REDIS_SOCKET_TIMEOUT: float = float(os.environ.get("REDIS_SOCKET_TIMEOUT", "0.5"))

    # Agent offline timeout in seconds
    AGENT_TIMEOUT_SECONDS: int = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "60"))

    # Reject uncredentialed /v1/leave and /v1/heartbeat. Parsed the other way
    # round from the usual flag: anything that isn't an explicit "false"/"0"/"no"
    # enforces, so an unset, empty or misspelled env var fails CLOSED. Setting
    # it falsey is the deliberate, temporary escape hatch for a legacy fleet.
    ENFORCE_AGENT_LIFECYCLE_AUTH: bool = os.environ.get(
        "ENFORCE_AGENT_LIFECYCLE_AUTH", "true"
    ).strip().lower() not in ("false", "0", "no")

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
    # Memory extraction (app/memory/extractor.py). Each falls back to the
    # matching PAI_* value, so extraction works with no extra configuration —
    # but extraction is a cheap structured-output task that runs on every turn,
    # so it can be moved to a smaller/faster model independently of Counselor.
    MEMORY_EXTRACTOR_PROVIDER: str = os.environ.get("MEMORY_EXTRACTOR_PROVIDER", "")
    MEMORY_EXTRACTOR_MODEL: str = os.environ.get("MEMORY_EXTRACTOR_MODEL", "")
    MEMORY_EXTRACTOR_API_KEY: str = os.environ.get("MEMORY_EXTRACTOR_API_KEY", "")
    MEMORY_EXTRACTOR_BASE_URL: str = os.environ.get("MEMORY_EXTRACTOR_BASE_URL", "")

    # ---- Memory retrieval index (app/memory/index_qdrant.py) --------------
    # Qdrant is a DERIVED index. Losing it costs a reindex, never data.
    # Unset backend -> NullMemoryIndex, and retrieval degrades to the existing
    # structured/lexical paths.
    MEMORY_VECTOR_BACKEND: str = os.environ.get("MEMORY_VECTOR_BACKEND", "")
    QDRANT_URL: str = os.environ.get("QDRANT_URL", "")
    QDRANT_API_KEY: str = os.environ.get("QDRANT_API_KEY", "")
    QDRANT_COLLECTION: str = os.environ.get("QDRANT_COLLECTION", "pai_memory")

    # Embeddings. Deliberately NOT defaulted to the PAI chat credentials: a
    # chat-model key/endpoint does not necessarily serve an embeddings route,
    # and silently pointing at one turns a config mistake into a runtime error
    # on every indexing job. Fallback happens only when PAI is explicitly an
    # OpenAI-compatible endpoint (see embeddings.resolve_config).
    MEMORY_EMBEDDING_PROVIDER: str = os.environ.get("MEMORY_EMBEDDING_PROVIDER", "openai")
    MEMORY_EMBEDDING_MODEL: str = os.environ.get(
        "MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"
    )
    MEMORY_EMBEDDING_API_KEY: str = os.environ.get("MEMORY_EMBEDDING_API_KEY", "")
    MEMORY_EMBEDDING_BASE_URL: str = os.environ.get("MEMORY_EMBEDDING_BASE_URL", "")
    # Dimensions of the configured model. Stored alongside each indexed point
    # so a model change is detectable and can trigger a reindex rather than
    # silently mixing incompatible vector spaces.
    MEMORY_EMBEDDING_DIM: int = int(os.environ.get("MEMORY_EMBEDDING_DIM", "1536"))
    # Sparse (lexical) encoder. Qdrant/bm25 via fastembed, with the collection's
    # sparse vector configured with Modifier.IDF so Qdrant computes real BM25
    # scoring server-side rather than us approximating it.
    MEMORY_SPARSE_MODEL: str = os.environ.get("MEMORY_SPARSE_MODEL", "Qdrant/bm25")

    # Retrieval shape. Fetch a wide candidate pool, rerank, return few.
    MEMORY_RETRIEVAL_CANDIDATES: int = int(
        os.environ.get("MEMORY_RETRIEVAL_CANDIDATES", "40")
    )
    MEMORY_RETRIEVAL_LIMIT: int = int(os.environ.get("MEMORY_RETRIEVAL_LIMIT", "8"))
    MEMORY_RERANKER: str = os.environ.get("MEMORY_RERANKER", "")

    # ---- Foreground memory injection (app/memory/foreground.py) -----------
    # Hard ceiling on the rendered student-context block. MemoryContextService
    # already caps per section; this is the backstop so pathological values
    # (a very long free-text Vault field) cannot expand the system prompt.
    # Conservative on purpose — this is context, not the conversation.
    PAI_MEMORY_CONTEXT_MAX_CHARS: int = int(
        os.environ.get("PAI_MEMORY_CONTEXT_MAX_CHARS", "6000")
    )
    # Retrieval is on the response-critical path. Past this, PAI drops to the
    # PostgreSQL-only fallback rather than making the student wait.
    PAI_MEMORY_CONTEXT_TIMEOUT_MS: int = int(
        os.environ.get("PAI_MEMORY_CONTEXT_TIMEOUT_MS", "1500")
    )
    # Master switch for automatic FOREGROUND injection into PAI Counselor.
    #
    # ON by default: persistent student context is available across conversations.
    # Retrieval is bounded and remains optional through this setting.
    # Pilot rollout — see docs/pai-memory-rollout.md:
    #   PAI_MEMORY_CONTEXT_ENABLED=true   (+ MEMORY_VECTOR_BACKEND=qdrant for hybrid)
    PAI_MEMORY_CONTEXT_ENABLED: bool = os.environ.get(
        "PAI_MEMORY_CONTEXT_ENABLED", "true"
    ).lower() in ("true", "1", "yes")
    # Foreground retrieval runs on its own small thread pool so a stalled
    # PostgreSQL cannot block the event loop (see foreground_executor.py).
    # Threads cannot be killed, so MAX_INFLIGHT — not the pool size — is what
    # bounds abandoned DB work when the database is slow.
    PAI_MEMORY_FOREGROUND_WORKERS: int = int(
        os.environ.get("PAI_MEMORY_FOREGROUND_WORKERS", "4")
    )
    PAI_MEMORY_FOREGROUND_MAX_INFLIGHT: int = int(
        os.environ.get("PAI_MEMORY_FOREGROUND_MAX_INFLIGHT", "8")
    )
    # Provider-neutral web search. Disabled unless both fields are configured;
    # credentials remain backend-only and are never included in tool results.
    WEB_SEARCH_PROVIDER: str = os.environ.get("WEB_SEARCH_PROVIDER", "")
    WEB_SEARCH_API_KEY: str = os.environ.get("WEB_SEARCH_API_KEY", "")
    WEB_SEARCH_BASE_URL: str = os.environ.get("WEB_SEARCH_BASE_URL", "")

    # Google OAuth (for "Sign in with Google" Gemini integration)
    GOOGLE_OAUTH_CLIENT_ID: str = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_SECRET: str = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    GOOGLE_OAUTH_REDIRECT_URI: str = os.environ.get(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://api.placement-ai.com/v1/cloud-agents/google/callback",
    )

    # Transactional email. Delivery goes through Resend when a key is
    # configured (otherwise sends are logged no-ops).
    # The application origin, for links the backend puts in email and in
    # integration redirects. Placement AI's canonical hosted app.
    FRONTEND_BASE_URL: str = os.environ.get("FRONTEND_BASE_URL", "https://app.placement-ai.com")
    RESEND_API_KEY: str = os.environ.get("RESEND_API_KEY", "")
    # Must be a domain verified in Resend, or delivery fails.
    EMAIL_FROM: str = os.environ.get("EMAIL_FROM", "Placement AI <noreply@placement-ai.com>")

    # Chat-platform integrations (Slack / Telegram bridges). The public base
    # URL is what external platforms call back to — Telegram setWebhook and
    # the Slack Events API URL both derive from it.
    PUBLIC_API_BASE: str = os.environ.get(
        "PUBLIC_API_BASE", "https://api.placement-ai.com"
    )
    # The official "OpenAgents" Slack app (one-click Add to Slack). All three
    # come from the app's Basic Information page; when unset, the UI falls
    # back to the bring-your-own-app flow (docs/slack-app-setup.md).
    SLACK_CLIENT_ID: str = os.environ.get("SLACK_CLIENT_ID", "")
    SLACK_CLIENT_SECRET: str = os.environ.get("SLACK_CLIENT_SECRET", "")
    SLACK_SIGNING_SECRET: str = os.environ.get("SLACK_SIGNING_SECRET", "")

    # In-app feedback forwarding. Feedback rows always land in the DB; when
    # this is set they are also emailed (via Resend) to the team.
    FEEDBACK_EMAIL_TO: str = os.environ.get("FEEDBACK_EMAIL_TO", "")

    # Server
    HOST: str = os.environ.get("HOST", "0.0.0.0")
    PORT: int = int(os.environ.get("PORT", "8000"))

    def validate_startup(self) -> None:
        """Reject incomplete production configuration before serving traffic."""
        if self.APP_ENV.lower() != "production":
            return
        if not os.environ.get("DATABASE_URL", "").strip():
            raise RuntimeError("DATABASE_URL is required in production")
        if self.CORS_ORIGINS.strip() in ("", "*"):
            raise RuntimeError("Production CORS_ORIGINS must be an explicit origin list")
        if self.FILE_STORAGE_BACKEND == "s3" and not self.S3_BUCKET.strip():
            raise RuntimeError("S3_BUCKET is required when FILE_STORAGE_BACKEND=s3")


config = Config()
