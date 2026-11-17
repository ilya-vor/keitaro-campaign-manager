"""Тесты HTTP-слоя: /api/health должен показывать реальное состояние ключа, а не «ключ не пустой»."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_session
from app.keitaro import KeitaroError
from app.main import app, get_client
from tests.test_services import FakeKeitaro


class UnauthorizedKeitaro(FakeKeitaro):
    """Keitaro отвечает 401 на любой запрос — как при неверном/отозванном ключе."""

    def _fail(self, method: str, path: str):
        raise KeitaroError("HTTP 401: Invalid API key", status_code=401, method=method, path=path)

    def list_campaigns(self, limit=None, offset=None):
        self._fail("GET", "/campaigns")

    def list_offers(self):
        self._fail("GET", "/offers")

    def list_domains(self):
        self._fail("GET", "/domains")

    def list_groups(self, group_type="campaigns"):
        self._fail("GET", "/groups")

    def list_traffic_sources(self):
        self._fail("GET", "/traffic_sources")


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def session_override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    def make_client(fake):
        app.dependency_overrides[get_session] = session_override
        app.dependency_overrides[get_client] = lambda: fake
        return TestClient(app)

    clients = []

    def factory(fake=None):
        client = make_client(fake or FakeKeitaro())
        clients.append(client)
        return client

    try:
        yield factory
    finally:
        for client in clients:
            client.close()
        app.dependency_overrides.clear()


def test_health_ok_when_key_works(api):
    response = api().get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["keitaro_ok"] is True
    assert body["keitaro_error"] is None
    assert "campaigns_visible" in body
    assert body["api_key_hint"].startswith("…")


def test_health_reports_invalid_key(api):
    body = api(UnauthorizedKeitaro()).get("/api/health").json()

    assert body["keitaro_configured"] is True  # ключ есть...
    assert body["keitaro_ok"] is False          # ...но Keitaro его не принимает
    assert body["keitaro_status_code"] == 401
    assert "Invalid API key" in body["keitaro_error"]


def test_health_without_key(api, monkeypatch):
    monkeypatch.setattr(settings, "keitaro_api_key", "")

    body = api().get("/api/health").json()

    assert body["keitaro_configured"] is False
    assert body["keitaro_ok"] is False
    assert "KEITARO_API_KEY" in body["keitaro_error"]


def test_campaigns_401_returns_actionable_hint(api):
    response = api(UnauthorizedKeitaro()).get("/api/campaigns")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Invalid API key" in detail
    assert "KEITARO_API_KEY" in detail
    assert "перезапущен" in detail
