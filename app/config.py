from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_name: str = "ClickPe Loan Eligibility Engine"
    database_url: str = "postgresql://loan_engine:loan_engine@localhost:5432/loan_engine"
    database_secret_arn: str = ""
    database_host: str = ""
    operator_token_secret_arn: str = ""
    internal_token_secret_arn: str = ""
    web_origin: str = "http://localhost:5173"
    local_upload_dir: str = "./uploads"
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    retention_days: int = Field(default=7, ge=1, le=30)
    operator_token: str = "change-me-before-exposing-the-api"
    internal_api_token: str = "change-me-in-aws-parameter-store"
    s3_upload_bucket: str = ""
    aws_region: str = "ap-south-1"
    n8n_webhook_url: str = "http://localhost:5678/webhook/match-run"
    notification_mode: Literal["preview", "test"] = "preview"
    test_recipients: str = ""
    ses_sender: str = ""
    notification_run_limit: int = Field(default=3, ge=1, le=200)
    notification_daily_limit: int = Field(default=180, ge=1, le=200)
    ses_role_arn: str = ""
    ses_external_id_secret_arn: str = ""

    @property
    def test_recipient_list(self) -> set[str]:
        return {
            address.strip().lower()
            for address in self.test_recipients.split(",")
            if address.strip()
        }

    @property
    def allowed_origins(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                    "http://localhost:3000",
                    self.web_origin,
                ]
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
