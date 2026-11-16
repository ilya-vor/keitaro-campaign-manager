"""Pydantic-схемы запросов/ответов HTTP-слоя."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CampaignCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=180, description="Имя кампании; префикс добавится сам")
    geo: str | list[str] = Field(description="ISO-3166 alpha-2: 'AU' или ['AU', 'RO']")
    offer_id: int = Field(ge=1)
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None


class OfferIn(BaseModel):
    offer_id: int = Field(ge=1)
    share: int | None = Field(default=None, ge=0, le=100)


class OffersPutIn(BaseModel):
    offers: list[OfferIn] = Field(default_factory=list)


class OfferAddIn(BaseModel):
    offer_id: int = Field(ge=1)
    share: int | None = Field(default=None, ge=0, le=100)


class DictionaryCreateIn(BaseModel):
    kind: str = Field(pattern="^(groups|sources)$")
    name: str = Field(min_length=1, max_length=120)
