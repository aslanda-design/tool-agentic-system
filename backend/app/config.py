from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://investment_tracker:investment_tracker@localhost:5432/investment_tracker"
    )

    base_currency: str = "EUR"

    # --- Interactive Brokers: TWS/IB Gateway (paper account by default, port 7497) ---
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1

    # --- Interactive Brokers: Flex Web Service (full trade/transaction history) ---
    # Set up in Account Management > Reports > Flex Queries. The token is a
    # long-lived secret with read access to your statements — keep it out of git.
    ibkr_flex_token: str = ""
    ibkr_flex_query_id: str = ""

    # --- Refresh cadence (minutes) ---
    market_data_refresh_minutes: int = 15
    snapshot_build_hour_utc: int = 23

    # --- Anthropic AI advisor (not wired up yet in this phase) ---
    anthropic_api_key: str = ""

    # --- OpenFIGI (security resolver — see plans/agentic_asset_mapping.md) ---
    # Optional: raises the free-tier rate limit. Only an ISIN is ever sent
    # to OpenFIGI, never account/holding data. https://www.openfigi.com/api
    openfigi_api_key: str = ""

    # --- Security resolver background job ---
    resolver_enabled: bool = True
    resolver_interval_minutes: int = 30
    # A resolution that can't be auto-accepted goes to NEEDS_AGENT (for the
    # local-LLM agent to try) when this is true, or straight to NEEDS_REVIEW
    # (a human) when false. The agent itself isn't built yet (a later
    # phase) — leave this false until it is, or ambiguous resolutions will
    # sit in NEEDS_AGENT with nothing to pick them up.
    agent_enabled: bool = False


settings = Settings()
