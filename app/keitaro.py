"""Тонкий клиент Keitaro Admin API v1.

Документация API лежит в самой админке Keitaro (`/admin/?object=adminApi`),
спека — https://admin-api.docs.keitaro.io/openapi.yaml.
Авторизация — заголовок `Api-Key`.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)


class KeitaroError(Exception):
    """Ошибка Keitaro: сеть, HTTP-статус или бизнес-ошибка в теле ответа."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None,
                 method: str | None = None, path: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload
        self.method = method
        self.path = path

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "status_code": self.status_code,
            "payload": self.payload,
            "method": self.method,
            "path": self.path,
        }


def _extract_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:300]}"
    if isinstance(data, dict):
        for key in ("error", "message", "error_description"):
            if data.get(key):
                return f"HTTP {response.status_code}: {data[key]}"
    if isinstance(data, list) and data and isinstance(data[0], dict):
        for key in ("error", "message"):
            if data[0].get(key):
                return f"HTTP {response.status_code}: {data[0][key]}"
    return f"HTTP {response.status_code}: {response.text[:300]}"


class KeitaroClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.keitaro_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.keitaro_api_key
        self.timeout = timeout or settings.keitaro_timeout
        self._transport = transport

    # ------------------------------------------------------------------ core
    def _headers(self) -> dict[str, str]:
        return {"Api-Key": self.api_key, "Accept": "application/json"}

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
    ) -> Any:
        if not self.api_key:
            raise KeitaroError(
                "Не задан KEITARO_API_KEY. Возьмите ключ в Keitaro: Профиль → API ключи.",
                method=method,
                path=path,
            )
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.request(method, url, headers=self._headers(), json=json, params=params)
        except httpx.HTTPError as exc:  # сеть, DNS, таймаут
            raise KeitaroError(f"Не удалось соединиться с Keitaro: {exc}", method=method, path=path) from exc

        if response.status_code >= 400:
            raise KeitaroError(
                _extract_error(response),
                status_code=response.status_code,
                payload=_safe_json(response),
                method=method,
                path=path,
            )
        return _safe_json(response)

    # ----------------------------------------------------------- campaigns
    def list_campaigns(self, limit: int | None = None, offset: int | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return self.request("GET", "/campaigns", params=params or None)

    def get_campaign(self, campaign_id: int) -> dict:
        return self.request("GET", f"/campaigns/{campaign_id}")

    def create_campaign(self, payload: dict) -> dict:
        return self.request("POST", "/campaigns", json=payload)

    def delete_campaign(self, campaign_id: int) -> Any:
        return self.request("DELETE", f"/campaigns/{campaign_id}")

    # ------------------------------------------------------------- streams
    def list_streams(self, campaign_id: int) -> list[dict]:
        return self.request("GET", f"/campaigns/{campaign_id}/streams")

    def get_stream(self, stream_id: int) -> dict:
        return self.request("GET", f"/streams/{stream_id}")

    def create_stream(self, payload: dict) -> dict:
        return self.request("POST", "/streams", json=payload)

    def update_stream(self, stream_id: int, payload: dict) -> dict:
        return self.request("PUT", f"/streams/{stream_id}", json=payload)

    def delete_stream(self, stream_id: int) -> Any:
        return self.request("DELETE", f"/streams/{stream_id}")

    # --------------------------------------------------------- dictionaries
    def list_offers(self) -> list[dict]:
        return self.request("GET", "/offers")

    def list_domains(self) -> list[dict]:
        return self.request("GET", "/domains")

    def list_groups(self, group_type: str = "campaigns") -> list[dict]:
        return self.request("GET", "/groups", params={"type": group_type})

    def create_group(self, name: str, group_type: str = "campaigns") -> dict:
        return self.request("POST", "/groups", json={"name": name, "type": group_type})

    def list_traffic_sources(self) -> list[dict]:
        return self.request("GET", "/traffic_sources")

    def create_traffic_source(self, name: str) -> dict:
        return self.request("POST", "/traffic_sources", json={"name": name})

    # -------------------------------------------------------------- report
    def build_report(
        self,
        *,
        dimensions: list[str],
        metrics: list[str],
        filters: list[dict] | None = None,
        interval: str = "all_time",
        sort: list[dict] | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "range": {"interval": interval},
            "dimensions": dimensions,
            "metrics": metrics,
        }
        if filters:
            payload["filters"] = filters
        if sort:
            payload["sort"] = sort
        return self.request("POST", "/report/build", json=payload)


def _safe_json(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:2000]}
