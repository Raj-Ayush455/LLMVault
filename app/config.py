import json
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./gateway.db"
    admin_api_key: str = ""
    primary_base_url: str = ""
    primary_api_key: str = ""
    allowed_models: str = "gemini-2.5-flash"
    fallback_base_url: str = ""
    fallback_api_key: str = ""
    fallback_model: str = ""
    model_pricing_usd_per_1m: str = "{}"
    provider_timeout_seconds: float = 25.0

    @property
    def allowed_model_names(self) -> set[str]:
        return {item.strip() for item in self.allowed_models.split(",") if item.strip()}

    @property
    def pricing(self) -> dict[str, dict[str, float]]:
        try:
            raw: Any = json.loads(self.model_pricing_usd_per_1m)
        except json.JSONDecodeError as exc:
            raise ValueError("MODEL_PRICING_USD_PER_1M must be valid JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("MODEL_PRICING_USD_PER_1M must be a JSON object")
        parsed: dict[str, dict[str, float]] = {}
        for model, rates in raw.items():
            if not isinstance(rates, dict):
                raise ValueError(f"Pricing for {model} must be an object")
            try:
                input_rate = float(rates["input"])
                output_rate = float(rates["output"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Pricing for {model} needs numeric input and output rates") from exc
            if input_rate < 0 or output_rate < 0:
                raise ValueError("Pricing rates cannot be negative")
            parsed[model] = {"input": input_rate, "output": output_rate}
        return parsed


settings = Settings()
