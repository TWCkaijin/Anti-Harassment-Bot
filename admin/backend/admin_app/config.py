"""Deployment-only admin settings. Browser input never selects an environment."""

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    environment: str = "development"
    google_cloud_project: str = Field(
        default="anti-harassment-bot",
        validation_alias=AliasChoices(
            "FIREBASE_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "google_cloud_project"
        ),
    )
    admin_private_database_id: str = "admin-private-dev"
    admin_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5174", "http://127.0.0.1:5174"]
    )
    admin_hosting_site: str = "anti-harassment-bot-admin"
    admin_session_seconds: int = Field(default=28800, ge=300, le=86400)
    admin_idle_seconds: int = Field(default=1800, ge=60, le=28800)
    admin_cookie_secure: bool = True
    admin_login_limit: int = Field(default=5, ge=1, le=30)
    admin_login_global_limit: int = Field(default=100, ge=1, le=1000)
    admin_login_window_seconds: int = Field(default=900, ge=60, le=3600)
    admin_request_limit: int = Field(default=120, ge=1, le=1000)
    admin_read_limit: int = Field(default=600, ge=1, le=2000)
    admin_storage_bucket: str = ""
    admin_tasks_queue: str = ""
    admin_tasks_location: str = "asia-east1"
    admin_tasks_service_account: str = ""
    admin_tasks_target_url: str = ""
    admin_tasks_audience: str = ""
    admin_local_jobs: bool = False
    admin_job_lease_seconds: int = Field(default=600, ge=60, le=1800)
    admin_job_retention_seconds: int = Field(default=604800, ge=3600, le=2592000)
    admin_max_body_bytes: int = Field(default=22 * 1024 * 1024, ge=1024)

    @field_validator("admin_origins")
    @classmethod
    def exact_origins(cls, values):
        result = []
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Admin origins must be exact HTTP(S) origins")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
                raise ValueError("Non-local admin origins require HTTPS")
            result.append(value)
        return result

    @model_validator(mode="after")
    def isolate_environment(self):
        expected = {"production": "admin-private", "preview": "admin-private-dev"}.get(
            self.environment
        )
        if expected and self.admin_private_database_id != expected:
            raise ValueError("Admin private database does not match deployment environment")
        if expected and not self.admin_cookie_secure:
            raise ValueError("Deployed admin cookies must be Secure")
        for value in (self.admin_tasks_target_url, self.admin_tasks_audience):
            if value:
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or not parsed.hostname.endswith(".run.app")
                    or parsed.query
                    or parsed.fragment
                    or parsed.username
                ):
                    raise ValueError(
                        "Task endpoint and audience must use the deployed HTTPS run.app service"
                    )
        return self


@lru_cache
def get_admin_settings() -> AdminSettings:
    return AdminSettings()
