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
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
