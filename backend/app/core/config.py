from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "LLM-Konnect"
    host: str = "127.0.0.1"
    port: int = 8756
    
    # Ollama
    ollama_host: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:3b-instruct-q4_K_M"
    llm_keep_alive: str = "0"  # frees VRAM after each request
    
    # Embeddings/vector store
    embedding_model: str = "intfloat/multilingual-e5-small"
    chroma_dir: str = "chroma"
    
    # Paths
    storage_dir: str = "data/storage"
    reports_dir: str = "reports"

    model_config = SettingsConfigDict(env_prefix="KONNECT_")

settings = Settings()
