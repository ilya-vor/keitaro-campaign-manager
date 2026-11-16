"""SQLAlchemy-модели.

В Keitaro — источник истины по кампаниям/потокам/офферам. Локальная БД хранит:
  * зеркало созданных через наш сервис кампаний (чтобы показывать историю, даже если
    Keitaro недоступен, и чтобы было к чему привязывать журнал операций);
  * снимок потоков и офферов на момент последней синхронизации;
  * журнал всех обращений к Keitaro (аудит: что, когда и с каким результатом отправили);
  * кэш справочников (офферы, домены, группы, источники), чтобы автокомплит работал быстро.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str] = mapped_column(String(255), unique=True)
    geo: Mapped[str] = mapped_column(String(255), default="")  # "AU,RO"
    offer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="active")
    campaign_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    flows: Mapped[list["CampaignFlow"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="CampaignFlow.position"
    )


class CampaignFlow(Base):
    __tablename__ = "campaign_flows"
    __table_args__ = (UniqueConstraint("keitaro_stream_id", name="uq_flow_stream_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    keitaro_stream_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "google" | "offers"
    name: Mapped[str] = mapped_column(String(255))
    schema: Mapped[str] = mapped_column(String(32))
    action_type: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=1)
    target_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    filters: Mapped[list | None] = mapped_column(JSON, nullable=True)
    collect_clicks: Mapped[bool] = mapped_column(default=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    campaign: Mapped[Campaign] = relationship(back_populates="flows")
    offers: Mapped[list["FlowOffer"]] = relationship(
        back_populates="flow", cascade="all, delete-orphan", order_by="FlowOffer.id"
    )


class FlowOffer(Base):
    __tablename__ = "flow_offers"
    __table_args__ = (UniqueConstraint("flow_id", "offer_id", name="uq_flow_offer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("campaign_flows.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int] = mapped_column(Integer, index=True)
    share: Mapped[int] = mapped_column(Integer, default=100)
    state: Mapped[str] = mapped_column(String(32), default="active")
    name_snapshot: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    flow: Mapped[CampaignFlow] = relationship(back_populates="offers")


class OperationLog(Base):
    """Журнал операций с Keitaro (аудит + отладка)."""

    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    action: Mapped[str] = mapped_column(String(64))  # campaign.create, stream.update, ...
    entity: Mapped[str] = mapped_column(String(32))  # campaign | stream | offers | dictionary
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    method: Mapped[str] = mapped_column(String(8), default="POST")
    path: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error
    request_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DictionaryItem(Base):
    """Кэш справочников Keitaro: offers / domains / groups / sources."""

    __tablename__ = "dictionary_items"
    __table_args__ = (UniqueConstraint("kind", "external_id", name="uq_dict_kind_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(512))
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
