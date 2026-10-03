from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/priority_notify.db"
    SECRET_KEY: str = "change-me-to-a-random-secret"
    ALLOWED_HOSTS: str = "notifications.osmosis.page,localhost,127.0.0.1"

    AUTHENTIK_ISSUER_URL: str = "https://auth.osmosis.page"
    AUTHENTIK_CLIENT_ID: str = ""
    AUTHENTIK_CLIENT_SECRET: str = ""
    AUTHENTIK_REDIRECT_URI: str = "https://notifications.osmosis.page/auth/callback"

    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = ""

    # Externally visible base URL; used as the OAuth issuer and MCP resource identifier.
    PUBLIC_URL: str = "https://notifications.osmosis.page"

    # MCP server at /api/mcp. With only this on, clients authenticate with an API token.
    MCP_ENABLED: bool = False
    # Built-in OAuth 2.1 authorization server, for assistants (Claude, ChatGPT) that sign in
    # interactively. Clients register via CIMD (Client ID Metadata Documents); only metadata
    # URLs on these hosts are accepted.
    OAUTH_SERVER_ENABLED: bool = False
    OAUTH_CIMD_ALLOWED_HOSTS: str = ""
    # Dynamic Client Registration (RFC 7591) at /oauth/register, for clients without CIMD
    # support (LiteLLM, Cursor, ...). Off while empty; otherwise the only hosts registered
    # redirect_uris may point at (list localhost/127.0.0.1 to allow native loopback clients).
    OAUTH_DCR_ALLOWED_REDIRECT_HOSTS: str = ""

    # Firebase Cloud Messaging (HTTP v1) push delivery to registered devices. Off while
    # either value is empty; SSE/polling delivery is unaffected either way. Set both to
    # the Firebase project id and the path of its service-account JSON key to enable.
    FCM_PROJECT_ID: str = ""
    FCM_SERVICE_ACCOUNT_FILE: str = ""

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @property
    def public_url(self) -> str:
        return self.PUBLIC_URL.rstrip("/")

    @property
    def cimd_allowed_hosts_list(self) -> list[str]:
        return [h.strip().lower() for h in self.OAUTH_CIMD_ALLOWED_HOSTS.split(",") if h.strip()]

    @property
    def dcr_allowed_redirect_hosts_list(self) -> list[str]:
        hosts = self.OAUTH_DCR_ALLOWED_REDIRECT_HOSTS.split(",")
        return [h.strip().lower() for h in hosts if h.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def push_enabled(self) -> bool:
        return bool(self.FCM_PROJECT_ID and self.FCM_SERVICE_ACCOUNT_FILE)


@lru_cache
def get_settings() -> Settings:
    return Settings()
