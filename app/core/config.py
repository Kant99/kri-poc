"""Application Configuration Module.

Loads and validates environment variables using Pydantic Settings.
Protects sensitive secrets from unintended logging.
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable fallbacks."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General App Config
    app_name: str = Field(default="Continuous Internal Audit KRI Engine", alias="APP_NAME")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=True, alias="DEBUG")
    port: int = Field(default=8000, alias="PORT")
    host: str = Field(default="0.0.0.0", alias="HOST")

    # Database
    database_url: str = Field(default="sqlite:///./kri_audit.db", alias="DATABASE_URL")

    # Azure OpenAI
    azure_openai_endpoint: Optional[str] = Field(default=None, alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: Optional[str] = Field(default=None, alias="AZURE_OPENAI_API_KEY")
    azure_openai_api_version: str = Field(default="2024-02-15-preview", alias="AZURE_OPENAI_API_VERSION")
    azure_openai_deployment_name: str = Field(default="gpt-4o", alias="AZURE_OPENAI_DEPLOYMENT_NAME")

    # Orchestration & LLM Settings
    use_mock_llm: bool = Field(default=True, alias="USE_MOCK_LLM")
    max_tool_calls_per_run: int = Field(default=15, alias="MAX_TOOL_CALLS_PER_RUN")
    llm_timeout_seconds: int = Field(default=60, alias="LLM_TIMEOUT_SECONDS")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    ssl_verify: bool = Field(default=False, alias="SSL_VERIFY")
    logs_dir: str = Field(default="logs/runs", alias="LOGS_DIR")

    def get_normalized_azure_endpoint(self) -> Optional[str]:
        """Extract the root https://<resource>.openai.azure.com from any endpoint string."""
        if not self.azure_openai_endpoint:
            return None
        from urllib.parse import urlparse
        parsed = urlparse(self.azure_openai_endpoint.strip())
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return self.azure_openai_endpoint.strip()

    def is_azure_configured(self) -> bool:
        """Verify if Azure OpenAI credentials are set and non-empty."""
        return bool(
            self.azure_openai_endpoint
            and self.azure_openai_api_key
            and not self.azure_openai_api_key.startswith("your-")
            and not self.azure_openai_api_key.startswith("mock-")
        )

    def sanitized_dict(self) -> dict:
        """Return configuration dictionary with masked sensitive fields."""
        data = self.model_dump()
        if data.get("azure_openai_api_key"):
            data["azure_openai_api_key"] = "********"
        return data


settings = Settings()
