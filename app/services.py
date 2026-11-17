"""Бизнес-логика: создание кампаний и редактор офферов поверх Keitaro API.

Схема работы:
  1. Keitaro — источник истины. Все изменения уходят туда через `KeitaroClient`.
  2. Локальная БД (SQLAlchemy) хранит зеркало кампаний/потоков/офферов, кэш справочников
     и журнал операций. Зеркало обновляется после каждого успешного вызова.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .keitaro import KeitaroClient, KeitaroError
from .models import Campaign, CampaignFlow, DictionaryItem, FlowOffer, OperationLog
from .utils import normalize_geo, normalize_shares, slugify

logger = logging.getLogger(__name__)

GOOGLE_FLOW_NAME = "Google"
OFFERS_FLOW_NAME = "Offers"


# --------------------------------------------------------------------- журнал
def log_operation(
    session: Session,
    *,
    action: str,
    entity: str,
    entity_id: str | int | None = None,
    method: str = "POST",
    path: str = "",
    status: str = "ok",
    request_body: dict | None = None,
    response_body: Any = None,
    error: str | None = None,
) -> None:
    session.add(
        OperationLog(
            action=action,
            entity=entity,
            entity_id=None if entity_id is None else str(entity_id),
            method=method,
            path=path,
            status=status,
            request_body=request_body,
            response_body=_truncate(response_body),
            error=error,
        )
    )
    session.commit()


def _truncate(payload: Any, limit: int = 4000) -> Any:
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload[:20]
    if isinstance(payload, dict):
        return {key: value for key, value in list(payload.items())[:40]}
    text = str(payload)
    return text[:limit]


# ----------------------------------------------------------------- справочники
DICTIONARY_KINDS = {
    "offers": "offers",
    "domains": "domains",
    "groups": "groups",
    "sources": "sources",
}


def _cache_is_fresh(session: Session, kind: str) -> bool:
    newest = session.scalar(
        select(DictionaryItem.synced_at)
        .where(DictionaryItem.kind == kind)
        .order_by(DictionaryItem.synced_at.desc())
        .limit(1)
    )
    if newest is None:
        return False
    return datetime.utcnow() - newest < timedelta(seconds=settings.dictionary_ttl_seconds)


def _store_dictionary(session: Session, kind: str, rows: Iterable[dict]) -> None:
    session.execute(delete(DictionaryItem).where(DictionaryItem.kind == kind))
    for row in rows:
        session.add(
            DictionaryItem(
                kind=kind,
                external_id=int(row["id"]),
                name=str(row.get("name") or row.get("id")),
                payload=_truncate(row),
            )
        )
    session.commit()


def get_dictionaries(session: Session, client: KeitaroClient, *, refresh: bool = False) -> dict:
    """Справочники Keitaro. Кэш в БД с TTL, чтобы автокомплит не дёргал API на каждый символ."""
    sources: dict[str, tuple[str, Any]] = {
        "offers": ("offers", lambda: client.list_offers()),
        "domains": ("domains", lambda: client.list_domains()),
        "groups": ("groups", lambda: client.list_groups("campaigns")),
        "sources": ("sources", lambda: client.list_traffic_sources()),
    }
    result: dict[str, list[dict]] = {}
    for key, (kind, loader) in sources.items():
        if not refresh and _cache_is_fresh(session, kind):
            items = session.scalars(
                select(DictionaryItem).where(DictionaryItem.kind == kind).order_by(DictionaryItem.name)
            ).all()
            result[key] = [item.payload or {"id": item.external_id, "name": item.name} for item in items]
            continue
        try:
            rows = loader() or []
        except KeitaroError as exc:
            # Не роняем UI из-за недоступного справочника — отдаём то, что есть в кэше.
            logger.warning("Не удалось загрузить справочник %s: %s", kind, exc.message)
            items = session.scalars(
                select(DictionaryItem).where(DictionaryItem.kind == kind).order_by(DictionaryItem.name)
            ).all()
            result[key] = [item.payload or {"id": item.external_id, "name": item.name} for item in items]
            continue
        _store_dictionary(session, kind, rows)
        result[key] = rows
    return result


def _offer_names(session: Session, client: KeitaroClient) -> dict[int, str]:
    offers = get_dictionaries(session, client).get("offers", [])
    return {int(item["id"]): str(item.get("name") or item["id"]) for item in offers}


# ------------------------------------------------------------------- кампании
def _campaign_name(raw_name: str) -> str:
    name = (raw_name or "").strip()
    if not name:
        raise ValueError("Название кампании не может быть пустым")
    prefix = settings.campaign_prefix
    if prefix and not name.lower().startswith(prefix.lower()):
        name = f"{prefix}{name}"
    return name


def _campaign_url(campaign: dict) -> str | None:
    domain = (campaign.get("domain") or "").rstrip("/")
    alias = campaign.get("alias")
    if domain and alias:
        return f"{domain}/{alias}"
    return None


def create_campaign(
    session: Session,
    client: KeitaroClient,
    *,
    name: str,
    geo: str | list[str],
    offer_id: int,
    domain_id: int | None = None,
    group_id: int | None = None,
    traffic_source_id: int | None = None,
) -> dict:
    """Создаёт кампанию с двумя потоками: гео → Google и остальные → оффер.

    Если что-то падает на середине, кампания архивируется в Keitaro, чтобы не оставлять
    «полукампанию» без потоков.
    """
    geo_codes = normalize_geo(geo)
    campaign_name = _campaign_name(name)
    alias = slugify(campaign_name)

    dictionaries = get_dictionaries(session, client)
    known_offers = {int(item["id"]) for item in dictionaries.get("offers", [])}
    if known_offers and int(offer_id) not in known_offers:
        raise ValueError(f"Оффер {offer_id} не найден в Keitaro")

    domain_id = domain_id or settings.default_domain_id
    group_id = group_id or settings.default_group_id
    traffic_source_id = traffic_source_id or settings.default_traffic_source_id

    campaign_payload = {
        "name": campaign_name,
        "alias": alias,
        "type": "position",
        "state": "active",
        "cookies_ttl": 24,
        "cost_type": "CPC",
        "cost_value": 0,
        "domain_id": domain_id,
        "group_id": group_id,
        "traffic_source_id": traffic_source_id,
    }
    campaign_payload = {key: value for key, value in campaign_payload.items() if value is not None}

    try:
        campaign = client.create_campaign(campaign_payload)
    except KeitaroError as exc:
        # Keitaro не разрешает одинаковые name/alias. Если пользователь создаёт кампанию
        # с уже занятым именем — один раз пробуем с суффиксом, чтобы кнопка «Создать» работала.
        if not (exc.status_code == 422 and "already" in (exc.message or "").lower()):
            log_operation(
                session, action="campaign.create", entity="campaign", method="POST", path="/campaigns",
                status="error", request_body=campaign_payload, error=exc.message,
            )
            raise
        suffix = secrets.token_hex(2)
        campaign_payload = dict(
            campaign_payload,
            name=f"{campaign_name} {suffix}",
            alias=f"{alias}-{suffix}",
        )
        log_operation(
            session, action="campaign.create.duplicate_retry", entity="campaign", method="POST",
            path="/campaigns", request_body=campaign_payload, error=exc.message,
        )
        try:
            campaign = client.create_campaign(campaign_payload)
        except KeitaroError as retry_exc:
            log_operation(
                session, action="campaign.create", entity="campaign", method="POST", path="/campaigns",
                status="error", request_body=campaign_payload, error=retry_exc.message,
            )
            raise

    campaign_id = int(campaign["id"])
    log_operation(
        session, action="campaign.create", entity="campaign", entity_id=campaign_id,
        method="POST", path="/campaigns", request_body=campaign_payload, response_body=campaign,
    )

    google_flow_payload = {
        "campaign_id": campaign_id,
        "name": GOOGLE_FLOW_NAME,
        "type": "regular",
        "position": 1,
        "schema": "redirect",
        "action_type": "http",
        "action_options": {"url": settings.google_url},
        "filters": [{"name": "country", "mode": "accept", "payload": geo_codes}],
        "collect_clicks": True,
        "comments": f"Гео {', '.join(geo_codes)} → {settings.google_url}",
    }
    offers_flow_payload = {
        "campaign_id": campaign_id,
        "name": OFFERS_FLOW_NAME,
        "type": "regular",
        "position": 2,
        "schema": "landings",
        "action_type": "http",
        "offers": [{"offer_id": int(offer_id), "share": 100}],
        "collect_clicks": True,
        "comments": f"Оффер {offer_id}",
    }

    flows: list[dict] = []
    try:
        google_flow = client.create_stream(google_flow_payload)
        log_operation(
            session, action="stream.create", entity="stream", entity_id=google_flow.get("id"),
            method="POST", path="/streams", request_body=google_flow_payload, response_body=google_flow,
        )
        flows.append(google_flow)

        offers_flow = client.create_stream(offers_flow_payload)
        log_operation(
            session, action="stream.create", entity="stream", entity_id=offers_flow.get("id"),
            method="POST", path="/streams", request_body=offers_flow_payload, response_body=offers_flow,
        )
        flows.append(offers_flow)
    except KeitaroError as exc:
        log_operation(
            session, action="stream.create", entity="stream", entity_id=campaign_id, method="POST",
            path="/streams", status="error", request_body=None, error=exc.message,
        )
        _rollback_campaign(session, client, campaign_id, reason=exc.message)
        raise

    _upsert_campaign(session, campaign, geo_codes=geo_codes, offer_id=offer_id, flows=flows)
    return campaign_detail(session, client, campaign_id)


def _rollback_campaign(session: Session, client: KeitaroClient, campaign_id: int, *, reason: str) -> None:
    """Убирает недособранную кампанию, чтобы не мусорить в аккаунте."""
    try:
        client.delete_campaign(campaign_id)
        log_operation(
            session, action="campaign.rollback", entity="campaign", entity_id=campaign_id,
            method="DELETE", path=f"/campaigns/{campaign_id}", status="ok",
            request_body={"reason": reason},
        )
    except KeitaroError as exc:  # pragma: no cover - защитная ветка
        logger.error("Не удалось откатить кампанию %s: %s", campaign_id, exc.message)
        log_operation(
            session, action="campaign.rollback", entity="campaign", entity_id=campaign_id,
            method="DELETE", path=f"/campaigns/{campaign_id}", status="error", error=exc.message,
        )


def _upsert_campaign(
    session: Session,
    campaign: dict,
    *,
    geo_codes: list[str] | None = None,
    offer_id: int | None = None,
    flows: list[dict] | None = None,
) -> Campaign:
    keitaro_id = int(campaign["id"])
    local = session.scalar(select(Campaign).where(Campaign.keitaro_id == keitaro_id))
    if local is None:
        local = Campaign(keitaro_id=keitaro_id, name=campaign.get("name", ""), alias=campaign.get("alias", ""))
        session.add(local)

    local.name = campaign.get("name", local.name)
    local.alias = campaign.get("alias", local.alias)
    if geo_codes is not None:
        local.geo = ",".join(geo_codes)
    if offer_id is not None:
        local.offer_id = int(offer_id)
    local.domain_id = campaign.get("domain_id", local.domain_id)
    local.group_id = campaign.get("group_id", local.group_id)
    local.traffic_source_id = campaign.get("traffic_source_id", local.traffic_source_id)
    local.state = campaign.get("state", local.state)
    local.campaign_url = _campaign_url(campaign) or local.campaign_url
    session.flush()

    if flows is not None:
        _sync_flows(session, local, flows)

    session.commit()
    return local


def _sync_flows(session: Session, campaign: Campaign, flows: list[dict]) -> None:
    existing = {flow.keitaro_stream_id: flow for flow in campaign.flows}
    seen: set[int] = set()
    for payload in flows:
        stream_id = int(payload["id"])
        seen.add(stream_id)
        flow = existing.get(stream_id)
        if flow is None:
            flow = CampaignFlow(campaign_id=campaign.id, keitaro_stream_id=stream_id, kind="offers",
                                name=payload.get("name", ""), schema=payload.get("schema", ""),
                                action_type=payload.get("action_type", ""))
            session.add(flow)
            campaign.flows.append(flow)
        flow.name = payload.get("name", flow.name)
        flow.schema = payload.get("schema", flow.schema)
        flow.action_type = payload.get("action_type", flow.action_type)
        flow.position = int(payload.get("position") or flow.position or 1)
        action_options = payload.get("action_options") or {}
        flow.target_url = action_options.get("url") or flow.target_url
        flow.filters = payload.get("filters") or []
        flow.collect_clicks = bool(payload.get("collect_clicks", flow.collect_clicks))
        flow.kind = "google" if flow.schema == "redirect" else "offers"
        session.flush()
        _sync_flow_offers(session, flow, payload.get("offers") or [])

    for stream_id, flow in existing.items():
        if stream_id not in seen:
            campaign.flows.remove(flow)
            session.delete(flow)
    session.flush()


def _sync_flow_offers(session: Session, flow: CampaignFlow, offers: list[dict]) -> None:
    names = {}
    existing = {offer.offer_id: offer for offer in flow.offers}
    seen: set[int] = set()
    for payload in offers:
        offer_id = int(payload["offer_id"] if "offer_id" in payload else payload["id"])
        seen.add(offer_id)
        row = existing.get(offer_id)
        if row is None:
            row = FlowOffer(flow_id=flow.id, offer_id=offer_id, share=int(payload.get("share") or 0))
            flow.offers.append(row)
        row.share = int(payload.get("share") or 0)
        row.state = payload.get("state", row.state)
        row.name_snapshot = names.get(offer_id, row.name_snapshot)

    for offer_id, row in existing.items():
        if offer_id not in seen:
            flow.offers.remove(row)
            session.delete(row)


# --------------------------------------------------------------- чтение данных
def _stats_by_campaign(client: KeitaroClient) -> dict[int, dict]:
    try:
        report = client.build_report(
            dimensions=["campaign_id"],
            metrics=["clicks", "campaign_unique_clicks", "bots"],
        )
    except KeitaroError as exc:
        logger.warning("Статистика кампаний недоступна: %s", exc.message)
        return {}
    result: dict[int, dict] = {}
    for row in report.get("rows", []):
        if row.get("campaign_id") is None:
            continue
        result[int(row["campaign_id"])] = {
            "clicks": int(row.get("clicks") or 0),
            "uniques": int(row.get("campaign_unique_clicks") or 0),
            "bots": int(row.get("bots") or 0),
        }
    return result


CAMPAIGNS_PAGE_SIZE = 300
CAMPAIGNS_MAX_PAGES = 10  # предохранитель: максимум 3000 кампаний


def _fetch_all_campaigns(client: KeitaroClient) -> list[dict]:
    """Admin API отдаёт кампании только страницами (`limit`/`offset`), поэтому листаем,
    пока страница не окажется неполной — иначе кампании с «дальних» страниц не находятся."""
    rows: list[dict] = []
    offset = 0
    for page_number in range(1, CAMPAIGNS_MAX_PAGES + 1):
        page = client.list_campaigns(limit=CAMPAIGNS_PAGE_SIZE, offset=offset) or []
        rows.extend(page)
        if len(page) < CAMPAIGNS_PAGE_SIZE:
            return rows
        offset += CAMPAIGNS_PAGE_SIZE
        if page_number == CAMPAIGNS_MAX_PAGES:
            logger.warning(
                "Список кампаний обрезан на %s записях — дальше не листаем", len(rows)
            )
    return rows


def list_campaigns(
    session: Session,
    client: KeitaroClient,
    *,
    search: str | None = None,
    only_ours: bool = True,
) -> list[dict]:
    remote = _fetch_all_campaigns(client)
    local_rows = {
        row.keitaro_id: row for row in session.scalars(select(Campaign)).all()
    }
    # «Создана здесь» — только то, что реально создал сервис (по журналу), а не просто открыли.
    created_ids = {
        int(entity_id)
        for entity_id in session.scalars(
            select(OperationLog.entity_id).where(
                OperationLog.action == "campaign.create",
                OperationLog.entity == "campaign",
                OperationLog.status == "ok",
            )
        )
        if entity_id and str(entity_id).isdigit()
    }
    stats = _stats_by_campaign(client)
    prefix = settings.campaign_prefix.lower()

    result: list[dict] = []
    for campaign in remote:
        name = str(campaign.get("name") or "")
        if only_ours and prefix and not name.lower().startswith(prefix):
            continue
        if search and search.strip().lower() not in name.lower():
            continue
        keitaro_id = int(campaign["id"])
        local = local_rows.get(keitaro_id)
        counts = stats.get(keitaro_id, {"clicks": 0, "uniques": 0, "bots": 0})
        result.append(
            {
                "id": keitaro_id,
                "name": name,
                "alias": campaign.get("alias"),
                "url": _campaign_url(campaign),
                "state": campaign.get("state"),
                "group": campaign.get("group"),
                "domain": campaign.get("domain"),
                "created_here": keitaro_id in created_ids,
                "geo": local.geo if local else None,
                "offer_id": local.offer_id if local else None,
                **counts,
            }
        )
    result.sort(key=lambda item: item["id"], reverse=True)
    return result


def _stream_stats(client: KeitaroClient, campaign_id: int) -> tuple[dict, dict]:
    """Статистика по потокам и офферам кампании одним-двумя запросами к /report/build."""
    per_stream: dict[int, dict] = {}
    per_offer: dict[tuple[int, int], dict] = {}
    try:
        report = client.build_report(
            dimensions=["stream_id"],
            metrics=["clicks", "campaign_unique_clicks", "bots"],
            filters=[{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
        )
        for row in report.get("rows", []):
            per_stream[int(row["stream_id"])] = {
                "clicks": int(row.get("clicks") or 0),
                "uniques": int(row.get("campaign_unique_clicks") or 0),
                "bots": int(row.get("bots") or 0),
            }
        offer_report = client.build_report(
            dimensions=["stream_id", "offer_id"],
            metrics=["clicks", "campaign_unique_clicks", "bots"],
            filters=[{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
        )
        for row in offer_report.get("rows", []):
            per_offer[(int(row["stream_id"]), int(row["offer_id"]))] = {
                "clicks": int(row.get("clicks") or 0),
                "uniques": int(row.get("campaign_unique_clicks") or 0),
                "bots": int(row.get("bots") or 0),
            }
    except KeitaroError as exc:
        logger.warning("Статистика кампании %s недоступна: %s", campaign_id, exc.message)
    return per_stream, per_offer


def _campaign_totals(client: KeitaroClient, campaign_id: int) -> dict | None:
    """Итоги кампании из отчёта по `campaign_id`.

    Именно так, а не суммой потоков: если клик не попал ни в один поток, Keitaro отдаёт его
    со `stream_id: 0`, и при суммировании по потокам такие клики терялись бы.
    """
    try:
        report = client.build_report(
            dimensions=["campaign_id"],
            metrics=["clicks", "campaign_unique_clicks", "bots"],
            filters=[{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
        )
    except KeitaroError as exc:
        logger.warning("Итоги кампании %s недоступны: %s", campaign_id, exc.message)
        return None

    rows = report.get("rows", [])
    own = [row for row in rows if row.get("campaign_id") in (None, campaign_id)]
    totals = {"clicks": 0, "uniques": 0, "bots": 0}
    for row in own:
        totals["clicks"] += int(row.get("clicks") or 0)
        totals["uniques"] += int(row.get("campaign_unique_clicks") or 0)
        totals["bots"] += int(row.get("bots") or 0)
    return totals


def campaign_detail(session: Session, client: KeitaroClient, campaign_id: int) -> dict:
    campaign = client.get_campaign(campaign_id)
    streams = client.list_streams(campaign_id) or []
    names = _offer_names(session, client)

    local = _upsert_campaign(session, campaign, flows=streams)
    per_stream, per_offer = _stream_stats(client, campaign_id)

    flows_view: list[dict] = []
    for stream in streams:
        stream_id = int(stream["id"])
        action_options = stream.get("action_options") or {}
        offers_view = []
        for offer in stream.get("offers") or []:
            offer_id = int(offer["offer_id"])
            offers_view.append(
                {
                    "offer_id": offer_id,
                    "share": int(offer.get("share") or 0),
                    "state": offer.get("state"),
                    "name": names.get(offer_id, f"offer {offer_id}"),
                    **per_offer.get((stream_id, offer_id), {"clicks": 0, "uniques": 0, "bots": 0}),
                }
            )
        flows_view.append(
            {
                "id": stream_id,
                "name": stream.get("name"),
                "schema": stream.get("schema"),
                "action_type": stream.get("action_type"),
                "position": stream.get("position"),
                "target_url": action_options.get("url"),
                "filters": stream.get("filters") or [],
                "offers": offers_view,
                "state": stream.get("state"),
                **per_stream.get(stream_id, {"clicks": 0, "uniques": 0, "bots": 0}),
            }
        )
    flows_view.sort(key=lambda item: item.get("position") or 0)
    totals = _campaign_totals(client, campaign_id) or {
        key: sum(int(flow.get(key) or 0) for flow in flows_view)
        for key in ("clicks", "uniques", "bots")
    }
    unattributed = {
        key: max(0, int(totals.get(key) or 0) - sum(int(flow.get(key) or 0) for flow in flows_view))
        for key in ("clicks", "uniques", "bots")
    }

    return {
        "id": int(campaign["id"]),
        "name": campaign.get("name"),
        "alias": campaign.get("alias"),
        "url": _campaign_url(campaign),
        "state": campaign.get("state"),
        "domain": campaign.get("domain"),
        "group": campaign.get("group"),
        "domain_id": campaign.get("domain_id"),
        "group_id": campaign.get("group_id"),
        "traffic_source_id": campaign.get("traffic_source_id"),
        "tokened_url": campaign.get("token"),
        "geo": local.geo.split(",") if local.geo else [],
        "offer_id": local.offer_id,
        "clicks": totals["clicks"],
        "uniques": totals["uniques"],
        "bots": totals["bots"],
        "unattributed": unattributed,
        "flows": flows_view,
        "offers_catalog": [
            {"id": int(item["id"]), "name": item.get("name")} for item in get_dictionaries(session, client).get("offers", [])
        ],
    }


# --------------------------------------------------------------- редактор офферов
def _resolve_offers_flow(client: KeitaroClient, campaign_id: int, stream_id: int) -> tuple[dict, list[dict]]:
    flows = client.list_streams(campaign_id) or []
    stream = next((flow for flow in flows if int(flow["id"]) == int(stream_id)), None)
    if stream is None:
        raise ValueError(f"Поток {stream_id} не относится к кампании {campaign_id}")
    if stream.get("schema") != "landings":
        raise ValueError("Офферы можно менять только в потоке с типом «landings» (в нашем случае — «Offers»)")
    current = [
        {"offer_id": int(offer["offer_id"]), "share": int(offer.get("share") or 0)}
        for offer in stream.get("offers") or []
    ]
    return stream, current


def set_stream_offers(
    session: Session,
    client: KeitaroClient,
    *,
    campaign_id: int,
    stream_id: int,
    offers: list[dict],
) -> dict:
    """Полная замена списка офферов потока (Keitaro PUT перезаписывает массив offers)."""
    stream, _current = _resolve_offers_flow(client, campaign_id, stream_id)
    if not offers:
        # Иначе PUT с offers: [] молча оставит поток без офферов — трафик потечёт в никуда.
        raise ValueError("Нельзя оставить поток без офферов — добавьте хотя бы один")
    normalized = normalize_shares(offers)
    if len(normalized) > 30:
        raise ValueError("Keitaro не позволяет больше 30 офферов в одном потоке")

    payload = {
        "name": stream.get("name"),
        "schema": stream.get("schema"),
        "action_type": stream.get("action_type") or "http",
        "collect_clicks": bool(stream.get("collect_clicks", True)),
        "offers": normalized,
    }
    try:
        updated = client.update_stream(stream_id, payload)
    except KeitaroError as exc:
        log_operation(
            session, action="stream.offers.update", entity="offers", entity_id=stream_id,
            method="PUT", path=f"/streams/{stream_id}", status="error",
            request_body=payload, error=exc.message,
        )
        raise

    log_operation(
        session, action="stream.offers.update", entity="offers", entity_id=stream_id,
        method="PUT", path=f"/streams/{stream_id}", request_body=payload, response_body=updated,
    )
    campaign = client.get_campaign(campaign_id)
    flows = client.list_streams(campaign_id) or []
    _upsert_campaign(session, campaign, flows=flows)
    return campaign_detail(session, client, campaign_id)


def add_offer(
    session: Session,
    client: KeitaroClient,
    *,
    campaign_id: int,
    stream_id: int,
    offer_id: int,
    share: int | None = None,
) -> dict:
    _stream, current = _resolve_offers_flow(client, campaign_id, stream_id)
    if any(item["offer_id"] == int(offer_id) for item in current):
        raise ValueError(f"Оффер {offer_id} уже есть в этом потоке")
    current.append({"offer_id": int(offer_id), "share": share})
    return set_stream_offers(
        session, client, campaign_id=campaign_id, stream_id=stream_id, offers=current
    )


def remove_offer(
    session: Session,
    client: KeitaroClient,
    *,
    campaign_id: int,
    stream_id: int,
    offer_id: int,
) -> dict:
    _stream, current = _resolve_offers_flow(client, campaign_id, stream_id)
    remaining = [item for item in current if item["offer_id"] != int(offer_id)]
    if len(remaining) == len(current):
        raise ValueError(f"Оффера {offer_id} нет в потоке {stream_id}")
    if not remaining:
        raise ValueError("Нельзя удалить последний оффер потока — Keitaro оставит поток пустым")
    return set_stream_offers(
        session, client, campaign_id=campaign_id, stream_id=stream_id, offers=remaining
    )


# ---------------------------------------------------- создание справочников
def create_dictionary_item(
    session: Session,
    client: KeitaroClient,
    *,
    kind: str,
    name: str,
) -> dict:
    """Создаёт группу кампаний или источник трафика в Keitaro (домены — только выбор:
    Keitaro требует подтверждения домена по DNS, это ручной шаг в панели)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Название не может быть пустым")

    if kind == "groups":
        created = client.create_group(name, "campaigns")
        path = "/groups"
    elif kind == "sources":
        created = client.create_traffic_source(name)
        path = "/traffic_sources"
    else:
        raise ValueError("Из интерфейса можно создать только группу или источник трафика")

    log_operation(
        session, action=f"dictionary.{kind}.create", entity="dictionary", entity_id=created.get("id"),
        method="POST", path=path, request_body={"name": name}, response_body=created,
    )
    # Сбрасываем кэш соответствующего справочника, чтобы новый элемент сразу появился в UI.
    session.execute(delete(DictionaryItem).where(DictionaryItem.kind == kind))
    session.commit()
    return created
