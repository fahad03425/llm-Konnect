from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "LLM-Konnect"
    host: str = "127.0.0.1"
    port: int = 8756
    
    # Ollama
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:3b-instruct-q4_K_M"
    llm_keep_alive: str = "0"  # frees VRAM after each request
    llm_keep_alive_chat: str = "5m"  # keeps model in VRAM for interactive chat
    llm_num_predict: int = 256
    llm_chat_history_size: int = 3
    llm_temperature: float = 0.2
    
    # Embeddings/vector store
    embedding_model: str = "intfloat/multilingual-e5-small"
    chroma_dir: str = "chroma"
    collection_name: str = "llm_konnect_kb"
    chunk_size: int = 512
    chunk_overlap: float = 0.15
    retrieval_top_k: int = 5
    
    # Analytics / KPI engine (Module 6.6)
    analytics_currency: str = "PKR"
    analytics_top_n: int = 10  # rows kept in top-N breakdowns (e.g. revenue by product)
    analytics_max_provenance_rows: int = 500  # cap on source_row ids stored per KPI result

    # Trend & forecasting (domain-agnostic, on the 6.6 engine)
    forecast_granularity: str = "monthly"            # default period size
    forecast_granularities: List[str] = ["daily", "weekly", "monthly"]
    forecast_horizon: int = 3                        # periods predicted ahead
    forecast_tier1_min_periods: int = 4              # below this we refuse to forecast
    forecast_tier2_min_periods: int = 12             # statsmodels only above this
    forecast_min_observed_ratio: float = 0.6         # reject history full of gaps
    forecast_baseline_window: int = 3                # periods averaged for the Tier-1 level
    forecast_band_multiplier: float = 1.96           # volatility band width (~95% if normal)
    forecast_min_band_ratio: float = 0.05            # band is never narrower than +/-5% of the estimate
    forecast_enable_tier2: bool = True               # gate for the optional statsmodels tier
    trend_moving_average_window: int = 3             # smoothing window for trend series
    trend_movers_window: int = 1                     # periods compared for rising/declining
    trend_movers_top_n: int = 5

    # Pharmacy expiry analytics (domain-pack KPIs on the 6.6 engine)
    expiry_buckets_days: List[int] = [30, 60, 90]  # banded: 0-30, 31-60, 61-90 days
    expiry_value_basis: str = "cost"  # "cost" (money lost) or "mrp" (revenue foregone)
    expiry_breakdown_top_n: int = 25  # at-risk items listed per bucket breakdown

    # Paths
    storage_dir: str = "data/storage"
    reports_dir: str = "reports"

    model_config = SettingsConfigDict(env_prefix="KONNECT_")

settings = Settings()
