from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "LLM-Konnect"
    host: str = "127.0.0.1"
    port: int = 8756
    
    # Ollama
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "llama3:latest"
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

    # Paths
    storage_dir: str = "data/storage"
    reports_dir: str = "reports"

    model_config = SettingsConfigDict(env_prefix="KONNECT_")

settings = Settings()
