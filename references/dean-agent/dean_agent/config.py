from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://dean_agent:change-me@localhost:5432/dean_agent"
    deanery_read_url: str = ""
    deanery_approval_token: str = ""
    openai_base_url: str = "http://localhost:8000/v1"
    openai_api_key: str = "local-key"
    openai_model: str = "your-tool-calling-model"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "dean_regulations_bge_m3"
    bge_m3_url: str = "http://127.0.0.1:8231"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
