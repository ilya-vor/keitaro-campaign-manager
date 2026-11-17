"""Конфигурация приложения (читается из переменных окружения / .env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Заглушки, которые нельзя считать ключом (частая причина «401 Invalid API key»).
PLACEHOLDER_API_KEYS = {
    "put-your-api-key-here",
    "your-api-key",
    "your_api_key",
    "changeme",
    "api-key",
    "test",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Keitaro ---
    keitaro_base_url: str = "https://tlgk.host/admin_api/v1"
    keitaro_api_key: str = ""
    keitaro_timeout: float = 20.0

    # Значения по умолчанию для создаваемых кампаний (то, что есть в тестовом аккаунте).
    default_domain_id: int | None = 1813
    default_group_id: int | None = 9799
    default_traffic_source_id: int | None = 836

    # Префикс названий кампаний по требованиям ТЗ.
    campaign_prefix: str = "test_vorobev_"

    # Куда уходит «первый поток».
    google_url: str = "https://www.google.com/"

    # --- БД ---
    database_url: str = "sqlite:///./keitaro_wrapper.sqlite3"

    # Кэш справочников Keitaro (офферы/домены/группы/источники) в локальной БД.
    dictionary_ttl_seconds: int = 600

    @field_validator("keitaro_api_key", "keitaro_base_url", mode="before")
    @classmethod
    def _clean_str(cls, value):
        """Убираем кавычки/пробелы, которые часто приезжают вместе с ключом из панели."""
        if isinstance(value, str):
            return value.strip().strip('"').strip("'").strip()
        return value

    @field_validator("keitaro_base_url", mode="after")
    @classmethod
    def _clean_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator(
        "default_domain_id",
        "default_group_id",
        "default_traffic_source_id",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @property
    def keitaro_configured(self) -> bool:
        """Ключ есть и это не заглушка из `.env.example`."""
        key = self.keitaro_api_key.strip()
        return bool(key) and key.lower() not in PLACEHOLDER_API_KEYS

    @property
    def api_key_is_placeholder(self) -> bool:
        return self.keitaro_api_key.strip().lower() in PLACEHOLDER_API_KEYS

    @property
    def api_key_hint(self) -> str:
        """Подсказка, какой ключ загружен — без полного значения (видно в /api/health)."""
        key = self.keitaro_api_key.strip()
        if not key:
            return "не задан"
        tail = key[-4:] if len(key) > 4 else ""
        return f"…{tail} (длина {len(key)})"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
