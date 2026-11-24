import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app import main
from app.db import Base


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "prefix", "test_demo_")
    flow = {"id": 4, "name": "Offers", "schema": "landings", "offers": [
        {"offer_id": 1, "share": 50, "state": "active"},
        {"offer_id": 2, "share": 50, "state": "active"},
    ]}
    calls = []

    def handle(request):
        path = request.url.path.removeprefix("/admin_api/v1/")
        payload = json.loads(request.content) if request.content else None
        calls.append((request.method, path, payload))
        if path == "campaigns" and request.method == "GET":
            offset = int(request.url.params["offset"])
            result = {
                0: [{"id": 11, "name": "somebody_else", "state": "active"}],
                1: [{"id": 10, "name": "test_demo_sandbox", "state": "disabled"}],
                2: [{"id": 12, "name": "test_demo_archive", "state": "archived"}],
            }.get(offset, [])
        elif path == "offers/99":
            result = {"state": "archived"}
        elif path.startswith("offers/"):
            result = {"state": "active"}
        elif path == "campaigns" and request.method == "POST":
            result = {"id": 10}
        elif path == "campaigns/10":
            result = {"id": 10, "name": "test_demo_sandbox"}
        elif path == "campaigns/11":
            result = {"id": 11, "name": "somebody_else"}
        elif path == "streams" and request.method == "POST":
            result = {"id": 5}
        elif path == "campaigns/10/streams":
            result = [{"id": 5, "schema": "redirect", "name": "Google"}, flow]
        elif path == "streams/4":
            if request.method == "PUT":
                flow["offers"] = payload["offers"]
            result = flow
        elif path == "report/build":
            result = {"rows": [{"stream_id": 4, "offer_id": 1, "clicks": 25}]}
        else:
            return httpx.Response(404)
        return httpx.Response(200, json=result)

    with httpx.Client(base_url="https://mock.test/admin_api/v1/", transport=httpx.MockTransport(handle)) as upstream:
        main.app.dependency_overrides[main.tracker] = lambda: upstream
        try:
            yield TestClient(main.app), flow, calls
        finally:
            main.app.dependency_overrides.clear()


def test_campaign_creation_sequence(tracker):
    client, _, calls = tracker
    payload = {"name": "sandbox", "country": "au", "offer_id": 1, "domain_id": 2, "group_id": 3, "traffic_source_id": 4}
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 201
    assert response.json() == {"id": 10, "name": "test_demo_sandbox"}
    _, _, campaign = calls[1]
    assert (campaign["state"], campaign["domain_id"], campaign["group_id"], campaign["traffic_source_id"]) == ("disabled", 2, "3", 4)
    first, second = (calls[index][2] for index in (2, 3))
    assert first["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert first["action_payload"] == "https://www.google.com/"
    assert second["type"] == "default" and second["offers"][0]["offer_id"] == 1
    assert calls[-1] == ("PUT", "campaigns/10", {"state": "active"})
    assert client.post("/api/campaigns", json=payload).status_code == 409
    assert client.get("/api/campaigns/11").status_code == 403


def test_draft_pin_cancel_archive_restore_and_conflict(tracker):
    client, remote, calls = tracker
    path = "/api/campaigns/10/flows/4/draft"

    def act(action, offer_id=None, status=200):
        assert client.post(path, json={"action": action, "offer_id": offer_id}).status_code == status

    def details():
        redirect, offers = client.get("/api/campaigns/10").json()["flows"]
        assert not redirect["editable"]
        return offers

    act("pin", 1)
    act("add", 3)
    assert [row["share"] for row in details()["offers"]] == [50, 25, 25]
    assert len(remote["offers"]) == 2
    act("cancel")
    assert not details()["dirty"]
    act("remove", 2)
    act("publish")
    assert [(row["offer_id"], row["share"]) for row in remote["offers"]] == [(1, 100)]
    assert details()["offers"][1]["state"] == "removed"
    act("restore", 2)
    act("publish")
    assert [row["share"] for row in remote["offers"]] == [50, 50]
    remote["offers"].pop()
    assert details()["offers"][1]["state"] == "removed"
    act("restore", 2)
    remote["offers"][0]["share"] = 70
    act("publish", status=409)
    assert details()["dirty"]
    assert client.get("/api/campaigns/10/stats").json()["rows"][0]["clicks"] == 25
    assert calls[-1][2]["filters"][0]["expression"] == 10


def test_campaign_list_reads_later_pages_and_keeps_disabled_campaigns(tracker):
    client, _, calls = tracker
    response = client.get("/api/campaigns")
    assert response.status_code == 200
    assert response.json() == [{"id": 10, "name": "test_demo_sandbox", "state": "disabled"}]
    assert [(method, path) for method, path, _ in calls] == [("GET", "campaigns")] * 4


def test_archived_offer_does_not_create_incomplete_campaign(tracker):
    client, _, calls = tracker
    response = client.post("/api/campaigns", json={
        "name": "sandbox",
        "country": "AU",
        "offer_id": 99,
        "domain_id": 2,
        "group_id": 3,
        "traffic_source_id": 4,
    })
    assert response.status_code == 422
    assert calls == [("GET", "offers/99", None)]


def test_tracker_access_rejection_explains_next_step():
    def blocked(request):
        return httpx.Response(403, text="<html>Forbidden</html>")

    with httpx.Client(base_url="https://mock.test/admin_api/v1/", transport=httpx.MockTransport(blocked)) as upstream:
        main.app.dependency_overrides[main.tracker] = lambda: upstream
        try:
            response = TestClient(main.app).get("/api/campaigns")
        finally:
            main.app.dependency_overrides.pop(main.tracker, None)
    assert response.status_code == 502
    assert "403" in response.json()["detail"]
    assert "python3 run.py --demo" in response.json()["detail"]
