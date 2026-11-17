"""FastAPI-приложение: HTTP-слой над `services`."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services
from .config import settings
from .db import get_session
from .keitaro import KeitaroClient, KeitaroError
from .models import Campaign, OperationLog
from .schemas import CampaignCreateIn, DictionaryCreateIn, OfferAddIn, OffersPutIn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Keitaro Campaign Manager",
    description="Тестовое задание: создание кампаний и редактор офферов через Keitaro Admin API",
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_client() -> KeitaroClient:
    return KeitaroClient()


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(KeitaroError)
async def keitaro_error_handler(request: Request, exc: KeitaroError) -> JSONResponse:
    # Ошибки валидации самого Keitaro (4xx) — это проблема ввода, а не шлюза.
    status_code = 400 if exc.status_code in {400, 401, 403, 404, 422} else 502
    detail = exc.message
    if exc.status_code == 401:
        detail = (
            f"{exc.message}. Keitaro отклонил ключ: проверьте KEITARO_API_KEY в .env "
            "(без кавычек и пробелов), что ключ активен в панели (Профиль → API ключи) "
            "и что сервер перезапущен после правки .env. Текущий ключ: "
            f"{settings.api_key_hint}, база API: {settings.keitaro_base_url}"
        )
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "keitaro": exc.as_dict()},
    )


# --------------------------------------------------------------------- страницы
@app.get("/", include_in_schema=False)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"prefix": settings.campaign_prefix, "google_url": settings.google_url},
    )


@app.get("/api/health")
def health(client: KeitaroClient = Depends(get_client)) -> dict:
    """Статус приложения + реальная проверка API-ключа (а не только «ключ не пустой»)."""
    info: dict = {
        "status": "ok",
        "keitaro_configured": settings.keitaro_configured,
        "keitaro_base_url": settings.keitaro_base_url,
        "api_key_hint": settings.api_key_hint,
        "api_key_is_placeholder": settings.api_key_is_placeholder,
        "keitaro_ok": False,
        "keitaro_error": None,
    }
    if not settings.keitaro_configured:
        if settings.api_key_is_placeholder:
            info["keitaro_error"] = (
                "В .env остался пример ключа. Вставьте настоящий KEITARO_API_KEY "
                "(Keitaro: Профиль → API ключи) и перезапустите сервер"
            )
        else:
            info["keitaro_error"] = "KEITARO_API_KEY не задан: заполните .env и перезапустите сервер"
        return info
    try:
        info["campaigns_visible"] = len(client.list_campaigns(limit=1) or [])
        info["keitaro_ok"] = True
    except KeitaroError as exc:
        info["keitaro_error"] = exc.message
        info["keitaro_status_code"] = exc.status_code
    return info


@app.get("/api/config")
def config() -> dict:
    return {
        "campaign_prefix": settings.campaign_prefix,
        "google_url": settings.google_url,
        "default_domain_id": settings.default_domain_id,
        "default_group_id": settings.default_group_id,
        "default_traffic_source_id": settings.default_traffic_source_id,
        "keitaro_configured": settings.keitaro_configured,
        "keitaro_base_url": settings.keitaro_base_url,
        "api_key_hint": settings.api_key_hint,
        "api_key_is_placeholder": settings.api_key_is_placeholder,
    }


# ---------------------------------------------------------------- справочники
@app.get("/api/dictionaries")
def dictionaries(
    refresh: bool = Query(default=False),
    session: Session = Depends(get_session),
    client: KeitaroClient = Depends(get_client),
) -> dict:
    data = services.get_dictionaries(session, client, refresh=refresh)
    return {
        "offers": data.get("offers", []),
        "domains": data.get("domains", []),
        "groups": data.get("groups", []),
        "sources": data.get("sources", []),
    }


@app.post("/api/dictionaries")
def create_dictionary_item(
    payload: DictionaryCreateIn,
    session: Session = Depends(get_session),
    client: KeitaroClient = Depends(get_client),
) -> dict:
    created = services.create_dictionary_item(session, client, kind=payload.kind, name=payload.name)
    return {"created": created}


# ------------------------------------------------------------------- кампании
@app.get("/api/campaigns")
def campaigns(
    search: str | None = Query(default=None),
    only_ours: bool = Query(default=True),
    session: Session = Depends(get_session),
    client: KeitaroClient = Depends(get_client),
) -> dict:
    items = services.list_campaigns(session, client, search=search, only_ours=only_ours)
    return {"items": items, "total": len(items)}


@app.post("/api/campaigns", status_code=201)
def create_campaign(
    payload: CampaignCreateIn,
    session: Session = Depends(get_session),
    client: KeitaroClient = Depends(get_client),
) -> dict:
    detail = services.create_campaign(
        session,
        client,
        name=payload.name,
        geo=payload.geo,
        offer_id=payload.offer_id,
        domain_id=payload.domain_id,
        group_id=payload.group_id,
        traffic_source_id=payload.traffic_source_id,
    )
    return detail


@app.get("/api/campaigns/{campaign_id}")
def campaign_detail(
    campaign_id: int,
    session: Session = Depends(get_session),
    client: KeitaroClient = Depends(get_client),
) -> dict:
    return services.campaign_detail(session, client, campaign_id)


# ------------------------------------------------------------- офферы потока
@app.put("/api/campaigns/{campaign_id}/flows/{stream_id}/offers")
def put_offers(
    campaign_id: int,
    stream_id: int,
    payload: OffersPutIn,
    session: Session = Depends(get_session),
    client: KeitaroClient = Depends(get_client),
) -> dict:
    return services.set_stream_offers(
        session,
        client,
        campaign_id=campaign_id,
        stream_id=stream_id,
        offers=[item.model_dump() for item in payload.offers],
    )


@app.post("/api/campaigns/{campaign_id}/flows/{stream_id}/offers")
def add_offer(
    campaign_id: int,
    stream_id: int,
    payload: OfferAddIn,
    session: Session = Depends(get_session),
    client: KeitaroClient = Depends(get_client),
) -> dict:
    return services.add_offer(
        session,
        client,
        campaign_id=campaign_id,
        stream_id=stream_id,
        offer_id=payload.offer_id,
        share=payload.share,
    )


@app.delete("/api/campaigns/{campaign_id}/flows/{stream_id}/offers/{offer_id}")
def remove_offer(
    campaign_id: int,
    stream_id: int,
    offer_id: int,
    session: Session = Depends(get_session),
    client: KeitaroClient = Depends(get_client),
) -> dict:
    return services.remove_offer(
        session,
        client,
        campaign_id=campaign_id,
        stream_id=stream_id,
        offer_id=offer_id,
    )


# ---------------------------------------------------------------------- журнал
@app.get("/api/logs")
def logs(
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict:
    rows = session.scalars(
        select(OperationLog).order_by(OperationLog.id.desc()).limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
                "action": row.action,
                "entity": row.entity,
                "entity_id": row.entity_id,
                "method": row.method,
                "path": row.path,
                "status": row.status,
                "request_body": row.request_body,
                "response_body": row.response_body,
                "error": row.error,
            }
            for row in rows
        ]
    }


@app.get("/api/statistics")
def statistics(session: Session = Depends(get_session)) -> dict:
    """Сводка по локальному зеркалу — показывает, что БД не декоративная."""
    campaigns_count = len(session.scalars(select(Campaign)).all())
    return {"campaigns_created_by_app": campaigns_count}
