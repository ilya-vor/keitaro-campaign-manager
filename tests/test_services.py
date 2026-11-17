"""Тесты бизнес-логики на фейковом клиенте Keitaro (без сети)."""
from __future__ import annotations

import itertools

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.keitaro import KeitaroError
from app.models import Campaign, FlowOffer
from app.services import (
    add_offer,
    campaign_detail,
    create_campaign,
    list_campaigns,
    remove_offer,
    set_stream_offers,
)


class FakeKeitaro:
    """Минимальная имитация Admin API v1 — только то, что использует сервис."""

    def __init__(self, fail_on_stream_number: int | None = None):
        self.campaigns: dict[int, dict] = {}
        self.streams: dict[int, dict] = {}
        self._ids = itertools.count(1000)
        self._stream_ids = itertools.count(5000)
        self.fail_on_stream_number = fail_on_stream_number
        self.created_streams: list[dict] = []
        self.deleted_campaigns: list[int] = []
        self.offers = [
            {"id": 25327, "name": "Flexacil Peru"},
            {"id": 24800, "name": "Flexacil Mexico"},
            {"id": 10184, "name": "Flexacil [JOINT]"},
        ]
        # готовые ответы /report/build: ключ — набор dimensions
        self.reports: dict[tuple[str, ...], dict] = {}

    # --- справочники
    def list_offers(self):
        return list(self.offers)

    def list_domains(self):
        return [{"id": 1813, "name": "kylovoa.com"}]

    def list_groups(self, group_type="campaigns"):
        return [{"id": 9799, "name": "VZ"}]

    def list_traffic_sources(self):
        return [{"id": 836, "name": "FORTESTS"}]

    # --- кампании
    def create_campaign(self, payload):
        campaign_id = next(self._ids)
        campaign = {
            "id": campaign_id,
            "name": payload["name"],
            "alias": payload["alias"],
            "state": "active",
            "domain": "https://kylovoa.com",
            "group": "VZ",
            "domain_id": payload.get("domain_id"),
            "group_id": payload.get("group_id"),
            "traffic_source_id": payload.get("traffic_source_id"),
        }
        self.campaigns[campaign_id] = campaign
        return campaign

    def get_campaign(self, campaign_id):
        return self.campaigns[campaign_id]

    def delete_campaign(self, campaign_id):
        self.deleted_campaigns.append(campaign_id)
        self.campaigns.pop(campaign_id, None)
        return {"deleted": True}

    def list_campaigns(self, limit=None, offset=None):
        return list(self.campaigns.values())

    # --- потоки
    def create_stream(self, payload):
        self.created_streams.append(payload)
        if self.fail_on_stream_number and len(self.created_streams) == self.fail_on_stream_number:
            raise KeitaroError("HTTP 400: stream validation failed")
        stream_id = next(self._stream_ids)
        stream = {
            "id": stream_id,
            "campaign_id": payload["campaign_id"],
            "name": payload.get("name"),
            "type": payload.get("type", "regular"),
            "position": payload.get("position"),
            "schema": payload.get("schema"),
            "action_type": payload.get("action_type"),
            "action_options": payload.get("action_options"),
            "filters": payload.get("filters") or [],
            "offers": payload.get("offers") or [],
            "collect_clicks": payload.get("collect_clicks", False),
            "state": "active",
        }
        self.streams[stream_id] = stream
        return stream

    def list_streams(self, campaign_id):
        return [stream for stream in self.streams.values() if stream["campaign_id"] == campaign_id]

    def get_stream(self, stream_id):
        return self.streams[stream_id]

    def update_stream(self, stream_id, payload):
        stream = self.streams[stream_id]
        stream.update({key: value for key, value in payload.items() if key != "id"})
        return stream

    # --- отчёты
    def build_report(self, *, dimensions, metrics, filters=None, interval="all_time", sort=None):
        return self.reports.get(tuple(dimensions), {"rows": [], "total": 0})

    # --- вспомогательное
    def add_foreign_campaign(self, campaign_id: int, name: str) -> dict:
        """Кампания, созданная не нашим сервисом (для проверки флага created_here)."""
        campaign = {
            "id": campaign_id,
            "name": name,
            "alias": f"foreign{campaign_id}",
            "state": "active",
            "domain": "https://kylovoa.com",
            "group": "VZ",
            "domain_id": 1813,
            "group_id": 9799,
            "traffic_source_id": 836,
        }
        self.campaigns[campaign_id] = campaign
        return campaign


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    return FakeKeitaro()


def test_create_campaign_builds_two_flows(session, client):
    detail = create_campaign(
        session, client, name="campaign1", geo="au", offer_id=25327
    )

    assert detail["name"] == "test_vorobev_campaign1"
    assert detail["alias"] == "test_vorobev_campaign1"
    assert detail["url"] == "https://kylovoa.com/test_vorobev_campaign1"

    google, offers = client.created_streams
    assert google["campaign_id"] == detail["id"]
    assert google["schema"] == "redirect"
    assert google["action_type"] == "http"
    assert google["action_options"] == {"url": "https://www.google.com/"}
    assert google["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert google["position"] == 1

    assert offers["schema"] == "landings"
    assert offers["offers"] == [{"offer_id": 25327, "share": 100}]
    assert offers["position"] == 2


def test_create_campaign_mirrors_to_database(session, client):
    detail = create_campaign(session, client, name="campaign2", geo=["AU", "RO"], offer_id=24800)

    local = session.scalar(select(Campaign).where(Campaign.keitaro_id == detail["id"]))
    assert local is not None
    assert local.geo == "AU,RO"
    assert local.offer_id == 24800
    assert len(local.flows) == 2
    offers_flow = next(flow for flow in local.flows if flow.schema == "landings")
    assert [offer.offer_id for offer in offers_flow.offers] == [24800]


def test_create_campaign_rolls_back_when_stream_fails(session):
    client = FakeKeitaro(fail_on_stream_number=2)

    with pytest.raises(KeitaroError):
        create_campaign(session, client, name="broken", geo="AU", offer_id=25327)

    assert client.deleted_campaigns, "недособранная кампания должна удаляться"
    assert session.scalars(select(Campaign)).all() == []


def test_geo_validation(session, client):
    with pytest.raises(ValueError):
        create_campaign(session, client, name="badgeo", geo="AUS", offer_id=25327)


def test_unknown_offer_is_rejected(session, client):
    with pytest.raises(ValueError):
        create_campaign(session, client, name="nooffer", geo="AU", offer_id=999999)


def test_set_stream_offers_normalizes_shares(session, client):
    detail = create_campaign(session, client, name="shares", geo="AU", offer_id=25327)
    offers_flow = next(flow for flow in detail["flows"] if flow["schema"] == "landings")

    updated = set_stream_offers(
        session,
        client,
        campaign_id=detail["id"],
        stream_id=offers_flow["id"],
        offers=[
            {"offer_id": 25327, "share": 50},
            {"offer_id": 24800, "share": 30},
        ],
    )

    shares = {offer["offer_id"]: offer["share"] for offer in updated["flows"][1]["offers"]}
    assert shares == {25327: 63, 24800: 37}
    assert sum(shares.values()) == 100
    # в Keitaro ушёл именно нормализованный payload
    assert client.streams[offers_flow["id"]]["offers"] == [
        {"offer_id": 25327, "share": 63},
        {"offer_id": 24800, "share": 37},
    ]


def test_add_offer_takes_remaining_share(session, client):
    detail = create_campaign(session, client, name="add", geo="AU", offer_id=25327)
    offers_flow = next(flow for flow in detail["flows"] if flow["schema"] == "landings")

    updated = add_offer(
        session,
        client,
        campaign_id=detail["id"],
        stream_id=offers_flow["id"],
        offer_id=24800,
    )
    shares = {offer["offer_id"]: offer["share"] for offer in updated["flows"][1]["offers"]}
    assert shares == {25327: 50, 24800: 50}

    local_flow = next(flow for flow in session.scalars(select(Campaign)).one().flows
                      if flow.schema == "landings")
    assert {row.offer_id for row in local_flow.offers} == {25327, 24800}


def test_remove_offer_rewrites_stream(session, client):
    detail = create_campaign(session, client, name="remove", geo="AU", offer_id=25327)
    offers_flow = next(flow for flow in detail["flows"] if flow["schema"] == "landings")
    add_offer(session, client, campaign_id=detail["id"], stream_id=offers_flow["id"], offer_id=24800)

    updated = remove_offer(
        session,
        client,
        campaign_id=detail["id"],
        stream_id=offers_flow["id"],
        offer_id=25327,
    )
    offers = updated["flows"][1]["offers"]
    assert [(offer["offer_id"], offer["share"]) for offer in offers] == [(24800, 100)]


def test_cannot_remove_last_offer(session, client):
    detail = create_campaign(session, client, name="last", geo="AU", offer_id=25327)
    offers_flow = next(flow for flow in detail["flows"] if flow["schema"] == "landings")

    with pytest.raises(ValueError):
        remove_offer(
            session,
            client,
            campaign_id=detail["id"],
            stream_id=offers_flow["id"],
            offer_id=25327,
        )


def test_duplicate_offer_rejected(session, client):
    detail = create_campaign(session, client, name="dup", geo="AU", offer_id=25327)
    offers_flow = next(flow for flow in detail["flows"] if flow["schema"] == "landings")

    with pytest.raises(ValueError):
        add_offer(
            session, client, campaign_id=detail["id"], stream_id=offers_flow["id"], offer_id=25327
        )


def test_offers_cannot_be_edited_in_redirect_flow(session, client):
    detail = create_campaign(session, client, name="wrongflow", geo="AU", offer_id=25327)
    google_flow = next(flow for flow in detail["flows"] if flow["schema"] == "redirect")

    with pytest.raises(ValueError):
        set_stream_offers(
            session,
            client,
            campaign_id=detail["id"],
            stream_id=google_flow["id"],
            offers=[{"offer_id": 25327, "share": 100}],
        )


def test_mirror_removes_deleted_offer_row(session, client):
    detail = create_campaign(session, client, name="mirror", geo="AU", offer_id=25327)
    offers_flow = next(flow for flow in detail["flows"] if flow["schema"] == "landings")
    add_offer(session, client, campaign_id=detail["id"], stream_id=offers_flow["id"], offer_id=24800)
    remove_offer(
        session, client, campaign_id=detail["id"], stream_id=offers_flow["id"], offer_id=24800
    )

    rows = session.scalars(select(FlowOffer)).all()
    assert [row.offer_id for row in rows] == [25327]


def test_multiple_geo_codes_in_google_filter(session, client):
    create_campaign(session, client, name="multigeo", geo="AU, ro;MX", offer_id=25327)

    google = client.created_streams[0]
    assert google["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU", "RO", "MX"]}]


def test_list_campaigns_uses_report_numbers_and_created_flag(session, client):
    ours = create_campaign(session, client, name="mine", geo="AU", offer_id=25327)
    foreign = client.add_foreign_campaign(9001, "test_ivanov_campaign1")

    client.reports[("campaign_id",)] = {
        "rows": [
            {"campaign_id": ours["id"], "clicks": 7, "campaign_unique_clicks": 3, "bots": 1},
            {"campaign_id": foreign["id"], "clicks": 2, "campaign_unique_clicks": 2, "bots": 0},
        ],
        "total": 2,
    }

    rows = {item["id"]: item for item in list_campaigns(session, client, only_ours=False)}

    assert rows[ours["id"]]["created_here"] is True
    assert rows[foreign["id"]]["created_here"] is False
    # цифры берутся из /report/build как есть
    assert (rows[ours["id"]]["clicks"], rows[ours["id"]]["uniques"], rows[ours["id"]]["bots"]) == (7, 3, 1)
    assert (rows[foreign["id"]]["clicks"], rows[foreign["id"]]["uniques"], rows[foreign["id"]]["bots"]) == (2, 2, 0)


def test_detail_shows_stats_per_flow_and_per_offer(session, client):
    detail = create_campaign(session, client, name="stats", geo="AU", offer_id=25327)
    add_offer(session, client, campaign_id=detail["id"], stream_id=detail["flows"][1]["id"], offer_id=24800)
    offers_stream = detail["flows"][1]["id"]
    google_stream = detail["flows"][0]["id"]

    client.reports[("stream_id",)] = {
        "rows": [
            {"stream_id": google_stream, "clicks": 5, "campaign_unique_clicks": 5, "bots": 4},
            {"stream_id": offers_stream, "clicks": 8, "campaign_unique_clicks": 2, "bots": 0},
        ],
        "total": 2,
    }
    client.reports[("stream_id", "offer_id")] = {
        "rows": [
            {"stream_id": offers_stream, "offer_id": 25327, "clicks": 5, "campaign_unique_clicks": 2, "bots": 0},
            {"stream_id": offers_stream, "offer_id": 24800, "clicks": 3, "campaign_unique_clicks": 0, "bots": 0},
        ],
        "total": 2,
    }

    fresh = campaign_detail(session, client, detail["id"])
    google, offers = fresh["flows"]

    assert (google["clicks"], google["uniques"], google["bots"]) == (5, 5, 4)
    assert (offers["clicks"], offers["uniques"], offers["bots"]) == (8, 2, 0)
    stats = {offer["offer_id"]: (offer["clicks"], offer["uniques"]) for offer in offers["offers"]}
    assert stats == {25327: (5, 2), 24800: (3, 0)}


def test_detail_offers_carry_share_and_name(session, client):
    detail = create_campaign(session, client, name="names", geo="AU", offer_id=25327)

    offers = detail["flows"][1]["offers"]
    assert offers[0]["name"] == "Flexacil Peru"
    assert offers[0]["share"] == 100
    catalog = {item["id"]: item["name"] for item in detail["offers_catalog"]}
    assert catalog[25327] == "Flexacil Peru"
