from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "CONVOX_"}

    # Server
    port: int = 8000
    env: str = "development"
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    cookie_domain: str = "localhost"

    # Database
    database_url: str = "postgres://convox:convox@localhost:5432/convox?sslmode=disable"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Auth
    jwt_secret: str = "0000000000000000000000000000000000000000000000000000000000000000"
    encrypt_key: str = "0000000000000000000000000000000000000000000000000000000000000000"

    # Providers (optional)
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""
    sarvam_api_key: str = ""
    gnani_api_key: str = ""
    nvidia_api_key: str = ""

    # Telephony (optional)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""

    # Compliance
    dpdp_enabled: bool = False
    dpdp_breach_notify_email: str = ""

    # Storage (optional)
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "convox-audio"

    @property
    def is_development(self) -> bool:
        return self.env == "development"


settings = Settings()
