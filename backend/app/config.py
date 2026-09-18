from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://investment_tracker:investment_tracker@localhost:5432/investment_tracker"

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
    # (a human) when false. Turning this on also requires a reachable model
    # backend (agent_provider/agent_base_url) with agent_model pulled and
    # supporting tool use — see backend/ai/common/llm.py.
    agent_enabled: bool = False

    # --- security_resolver agent model backend (see
    # plans/agentic_asset_mapping.md Phase 6 and backend/ai/common/llm.py).
    # Only used when agent_enabled is true; the resolver itself never
    # depends on any of this being reachable.
    #
    # agent_provider picks the client: "ollama" (Ollama's native API) or
    # "openai" (any OpenAI-compatible /chat/completions endpoint — Groq,
    # OpenAI itself, Together, Fireworks, a local vLLM/llama.cpp server...).
    # agent_base_url is interpreted accordingly: Ollama's root for
    # "ollama" (e.g. http://localhost:11434, or
    # http://host.docker.internal:11434 from inside docker compose), or
    # the full API root INCLUDING /v1 for "openai" (e.g.
    # https://api.groq.com/openai/v1). agent_api_key is only used by
    # "openai" (Ollama needs no key). Swapping models/providers is a
    # config change here — never a code change.
    agent_provider: str = "ollama"
    agent_base_url: str = "http://localhost:11434"
    agent_api_key: str = ""
    agent_model: str = "qwen3:8b"
    agent_max_steps: int = 8
    agent_timeout_seconds: int = 180
    agent_num_ctx: int = 8192  # Ollama only — ignored by the "openai" provider

    # --- Quant Lab (see plans/quant_lab.md) ---
    # Twelve Data (https://twelvedata.com) — a second, keyed source of daily
    # bars used only to backfill calibration history when yfinance's
    # persisted history is too short/gappy. Free tier as of writing: 800
    # requests/day, 8/min — verify current numbers before relying on them.
    # Only a symbol/MIC/date range is ever sent, never account/holding data.
    twelve_data_api_key: str = ""

    # --- Tool RAG (see plans/tool_rag.md) ---
    # Off by default: at today's ~30-tool catalog, an agent's hand-curated
    # tool allowlist (now doubling as its retrieval *ceiling* — see
    # ai/agents/portfolio_assistant/agent.py) already fits comfortably
    # under the "small models get worse with more tools" budget, so
    # retrieval has nothing to improve yet (plans/tool_rag.md section 1.1).
    # Turning this on requires the tool_index table populated first — see
    # ai/common/tool_rag/build_index.py — or every turn immediately hits
    # the "ceiling too small" fallback and behaves exactly as if this were
    # still false.
    tool_rag_enabled: bool = False

    # Embedding backend for indexing tool docs and embedding each turn's
    # query — same "provider is a config choice" shape as agent_provider
    # above, but for embeddings rather than chat (see
    # app/adapters/embeddings/factory.py). "ollama" (local, default, keeps
    # tool-doc/query text on-machine) or "openai" (any OpenAI-compatible
    # /embeddings endpoint).
    tool_rag_embedding_provider: str = "ollama"
    tool_rag_embedding_base_url: str = "http://localhost:11434"
    tool_rag_embedding_api_key: str = ""
    tool_rag_embedding_model: str = "nomic-embed-text"
    # Must match the tool_index.embedding column's fixed dimensionality
    # (768, set by migration 0006_tool_index.py) — switching to a model
    # with a different dimension needs a new migration, not just this
    # value, or every insert/search against that column fails outright.
    tool_rag_embedding_dimensions: int = 768

    # Selection policy (plans/tool_rag.md section 4.4).
    tool_rag_top_k: int = 8
    tool_rag_max_schema_tokens: int = 1500
    tool_rag_min_score: float = 0.35
    tool_rag_max_per_server: int = 4
    # A ceiling with this many tools or fewer skips retrieval entirely and
    # exposes it in full — the safety net that makes enabling this system
    # a no-op for any agent that doesn't need it yet.
    tool_rag_skip_below_tool_count: int = 12


settings = Settings()
