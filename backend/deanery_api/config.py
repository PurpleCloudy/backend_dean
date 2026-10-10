from functools import lru_cache
from ipaddress import ip_network
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    migration_database_url: str = ""
    job_database_url: str = ""
    access_token_key: str = Field(min_length=32)
    access_ttl_seconds: int = Field(default=900, ge=30, le=3600)
    refresh_ttl_seconds: int = Field(default=2592000, ge=60, le=7776000)
    allowed_origins: list[str] = ["*"]
    development: bool = False
    trusted_proxy_ips: list[str] = []
    login_account_limit: int = Field(default=10, ge=1, le=1000)
    login_ip_limit: int = Field(default=100, ge=1, le=10000)
    login_window_seconds: int = Field(default=300, ge=1, le=86400)
    pool_min_size: int = Field(default=1, ge=1, le=20)
    pool_max_size: int = Field(default=10, ge=1, le=100)
    statement_timeout_ms: int = Field(default=15000, ge=100, le=60000)
    delegation_secret: str = ""
    agent_url: str = ""
    backend_url: str = "http://api:8000"
    agent_timeout_seconds: int = 120
    agent_max_concurrency: int = 4
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "deanery-private"
    max_upload_bytes: int = Field(default=10000000, ge=1, le=10000000)
    qdrant_url: str = ""
    qdrant_collection: str = "deanery_documents"
    bge_url: str = ""
    clamav_host: str = ""
    clamav_port: int = 3310
    clamav_max_signature_age_hours: int = Field(default=48, ge=1, le=168)
    job_max_attempts: int = 3
    stalled_job_timeout_seconds: int = 120

    @model_validator(mode="after")
    def secure_origins(self):
        if self.pool_max_size < self.pool_min_size:
            raise ValueError("Invalid pool limits")
        if not self.development and any(x != "*" and not x.startswith("https://") for x in self.allowed_origins):
            raise ValueError("HTTPS origins required outside explicit development mode")
        for address in self.trusted_proxy_ips: ip_network(address,strict=False)
        return self


@lru_cache
def get_settings():
    return Settings()
