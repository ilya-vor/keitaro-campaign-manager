"""Конфигурация приложения (читается из переменных окружения / .env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        return bool(self.keitaro_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
