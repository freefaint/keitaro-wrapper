import os
import re
import threading
from pathlib import Path
from typing import Annotated

import httpx
import pycountry
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, PositiveInt, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import CampaignSetup, FlowDraft, engine
from app.demo import DemoTracker

app = FastAPI(title="Keitaro Campaigns")
static = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=static), name="static")
edit_lock = threading.Lock()
demo_tracker = DemoTracker() if os.getenv("KEITARO_DEMO") == "1" else None
prefix = os.getenv("CAMPAIGN_PREFIX", "test_demo_")
if not re.fullmatch(r"test_[a-z0-9]+_", prefix):
    raise RuntimeError("CAMPAIGN_PREFIX must have the form test_surname_")


@app.middleware("http")
async def local_writes(request: Request, call_next):
    if request.method in {"POST", "PUT", "DELETE"} and request.headers.get("origin") not in {
        None, str(request.base_url).rstrip("/")
    }:
        return JSONResponse({"detail": "Cross-origin write rejected"}, status_code=403)
    return await call_next(request)


def tracker():
    if demo_tracker is not None:
        with httpx.Client(
            base_url="http://demo.invalid/admin_api/v1/",
            transport=httpx.MockTransport(demo_tracker.request),
        ) as client:
            yield client
        return
    key = os.getenv("KEITARO_API_KEY")
    if not key:
        raise HTTPException(503, "Заполните KEITARO_API_KEY в .env и перезапустите приложение")
    base = os.getenv("KEITARO_URL", "https://tlgk.host").rstrip("/")
    with httpx.Client(base_url=f"{base}/admin_api/v1/", headers={"Api-Key": key}, timeout=20) as client:
        yield client


Tracker = Annotated[httpx.Client, Depends(tracker)]


def upstream(client, method, path, **kwargs):
    try:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        raise HTTPException(404 if status == 404 else 502, f"Keitaro: HTTP {status} ({path})") from error
    except httpx.TimeoutException as error:
        raise HTTPException(504, f"Keitaro не ответил вовремя ({path})") from error
    except (httpx.RequestError, ValueError) as error:
        raise HTTPException(502, f"Нет корректного ответа от Keitaro ({path})") from error


def upstream_object(client, method, path, fields=(), **kwargs):
    result = upstream(client, method, path, **kwargs)
    if not isinstance(result, dict) or any(field not in result for field in fields):
        raise HTTPException(502, f"Неожиданный формат ответа Keitaro ({path})")
    return result


def upstream_rows(client, path, fields, **kwargs):
    rows = upstream(client, "GET", path, **kwargs)
    if not isinstance(rows, list) or (
        any(not isinstance(row, dict) or any(field not in row for field in fields) for row in rows)
    ):
        raise HTTPException(502, f"Неожиданный формат списка Keitaro ({path})")
    return rows


def ensure_available_offer(client, offer_id):
    offer = upstream_object(client, "GET", f"offers/{offer_id}")
    if offer.get("state") in {"deleted", "archived"}:
        raise HTTPException(422, "Этот оффер архивирован в Keitaro")


class NewCampaign(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    country: str = Field(min_length=2, max_length=2)
    offer_id: PositiveInt
    domain_id: PositiveInt
    group_id: PositiveInt
    traffic_source_id: PositiveInt

    @field_validator("country")
    @classmethod
    def country_code(cls, value):
        code = value.upper()
        if pycountry.countries.get(alpha_2=code) is None:
            raise ValueError("Укажите код страны ISO 3166-1 alpha-2")
        return code


def owned_campaign(client, campaign_id):
    campaign = upstream_object(client, "GET", f"campaigns/{campaign_id}", ("name",))
    if campaign.get("state") in {"deleted", "archived"}:
        raise HTTPException(404, "Кампания архивирована в Keitaro")
    if not isinstance(campaign["name"], str) or not campaign["name"].startswith(prefix):
        raise HTTPException(403, f"Доступны только кампании с префиксом {prefix}")
    return campaign


@app.get("/")
def index():
    return FileResponse(static / "index.html")


@app.get("/api/catalog")
def catalog(client: Tracker):
    result = {}
    for key, path, params in (
        ("domains", "domains", {}),
        ("groups", "groups", {"type": "campaigns"}),
        ("sources", "traffic_sources", {}),
        ("offers", "offers", {}),
    ):
        items = upstream_rows(client, path, ("id", "name"), params=params)
        result[key] = [
            {"id": item["id"], "name": item["name"]} for item in items
            if item.get("state") not in {"deleted", "archived"}
        ]
    return {**result, "prefix": prefix, "demo": demo_tracker is not None}


@app.get("/api/campaigns")
def campaigns(client: Tracker):
    offset = 0
    rows = {}
    while True:
        batch = upstream_rows(
            client, "campaigns", ("id", "name"), params={"offset": offset, "limit": 100}
        )
        if not batch:
            break
        if all(row["id"] in rows for row in batch):
            raise HTTPException(502, "Keitaro вернул повторную страницу кампаний")
        rows.update((row["id"], row) for row in batch)
        offset += len(batch)

    return [
        {"id": row["id"], "name": row["name"], "state": row.get("state")}
        for row in rows.values()
        if row["name"].startswith(prefix) and row.get("state") not in {"deleted", "archived"}
    ]


@app.post("/api/campaigns", status_code=201)
def create_campaign(data: NewCampaign, client: Tracker):
    name = data.name if data.name.startswith(prefix) else prefix + data.name
    alias = name.lower()
    ensure_available_offer(client, data.offer_id)
    with Session(engine) as db:
        setup = CampaignSetup(alias=alias, phase="sending_campaign")
        db.add(setup)
        try:
            db.commit()
        except IntegrityError as error:
            db.rollback()
            raise HTTPException(409, "Кампания с таким именем уже создана или требует проверки") from error

        try:
            campaign = upstream_object(client, "POST", "campaigns", ("id",), json={
                "name": name, "alias": alias, "type": "position", "state": "disabled",
                "domain_id": data.domain_id, "group_id": str(data.group_id),
                "traffic_source_id": data.traffic_source_id,
            })
            setup.remote_id = campaign["id"]
            setup.phase = "creating_flows"
            db.commit()
            shared = {"campaign_id": setup.remote_id, "state": "active", "collect_clicks": True}
            upstream(client, "POST", "streams", json={
                **shared, "name": f"{data.country} → Google", "position": 1, "type": "regular",
                "schema": "redirect", "action_type": "http", "action_payload": "https://www.google.com/",
                "filters": [{"name": "country", "mode": "accept", "payload": [data.country]}],
            })
            upstream(client, "POST", "streams", json={
                **shared, "name": "Офферы", "position": 2, "type": "default",
                "schema": "landings", "action_type": "do_nothing",
                "offers": [{"offer_id": data.offer_id, "state": "active", "share": 100}],
            })
            setup.phase = "activating"
            db.commit()
            upstream(client, "PUT", f"campaigns/{setup.remote_id}", json={"state": "active"})
            setup.phase = "ready"
            db.commit()
        except HTTPException as error:
            details = f" ID кампании: {setup.remote_id}." if setup.remote_id else ""
            raise HTTPException(error.status_code, f"{error.detail}.{details} Проверьте кампанию {alias} в Keitaro перед повтором.") from error
        return {"id": setup.remote_id, "name": name}


def campaign_flows(client, campaign_id):
    return [
        flow for flow in upstream_rows(client, f"campaigns/{campaign_id}/streams", ("id", "name", "schema"))
        if flow.get("state") != "deleted"
    ]


def remote_offers(flow):
    return sorted(
        [
            {"offer_id": row["offer_id"], "share": row["share"], "state": row.get("state", "active")}
            for row in flow.get("offers", []) if row.get("state") not in {"deleted", "archived"}
        ],
        key=lambda row: row["offer_id"],
    )


def synced_rows(flow, previous):
    remote = remote_offers(flow)
    by_id = {row["offer_id"]: row for row in previous}
    current = {row["offer_id"]: row for row in remote}
    rows = []
    for offer_id in dict.fromkeys([*by_id, *current]):
        latest = current.get(offer_id)
        if latest is None:
            rows.append({"offer_id": offer_id, "share": 0, "state": "removed", "pinned": False})
        else:
            old = by_id.get(offer_id, {})
            pinned = old.get("pinned", False) and old.get("share") == latest["share"] and latest["state"] == "active"
            rows.append({**latest, "pinned": pinned})
    return rows


def published_payload(rows):
    return sorted(
        [{key: row[key] for key in ("offer_id", "share", "state")} for row in rows if row["state"] != "removed"],
        key=lambda row: row["offer_id"],
    )


def local_flow(db, flow):
    saved = db.get(FlowDraft, flow["id"])
    if saved is None:
        saved = FlowDraft(flow_id=flow["id"], published=synced_rows(flow, []))
        db.add(saved)
    elif saved.draft is None:
        saved.published = synced_rows(flow, saved.published)
    return saved


@app.get("/api/campaigns/{campaign_id}")
def campaign_details(campaign_id: int, client: Tracker):
    campaign = owned_campaign(client, campaign_id)
    with edit_lock, Session(engine) as db:
        result = []
        for flow in campaign_flows(client, campaign_id):
            editable = flow["schema"] == "landings"
            saved = local_flow(db, flow) if editable else None
            result.append({
                "id": flow["id"], "name": flow["name"], "editable": editable,
                "dirty": saved is not None and saved.draft is not None,
                "offers": (saved.draft if saved.draft is not None else saved.published) if saved else [],
            })
        db.commit()
    return {"id": campaign_id, "name": campaign["name"], "flows": result}


def rebalance(offers):
    active = [offer for offer in offers if offer["state"] == "active"]
    free = [offer for offer in active if not offer.get("pinned")]
    available = 100 - sum(offer["share"] for offer in active if offer.get("pinned"))
    if not active:
        return
    if available < len(free) or (not free and available != 0):
        raise HTTPException(422, "Не хватает доли для распределения. Снимите закрепление с оффера.")
    if free:
        share, remainder = divmod(available, len(free))
        for index, offer in enumerate(free):
            offer["share"] = share + (index < remainder)


class DraftAction(BaseModel):
    action: str = Field(pattern=r"^(add|remove|restore|pin|unpin|publish|cancel)$")
    offer_id: PositiveInt | None = None


@app.post("/api/campaigns/{campaign_id}/flows/{flow_id}/draft")
def update_draft(campaign_id: int, flow_id: int, data: DraftAction, client: Tracker):
    with edit_lock, Session(engine) as db:
        owned_campaign(client, campaign_id)
        flow = next((f for f in campaign_flows(client, campaign_id) if f["id"] == flow_id), None)
        if flow is None or flow["schema"] != "landings":
            raise HTTPException(404, "Поток офферов не найден в кампании")
        saved = local_flow(db, flow)
        if data.action == "cancel":
            saved.draft = None
            saved.published = synced_rows(flow, saved.published)
        elif data.action == "publish":
            if saved.draft is not None:
                remote = remote_offers(flow)
                payload = published_payload(saved.draft)
                if remote not in (published_payload(saved.published), payload):
                    raise HTTPException(409, "Поток изменился в Keitaro. Отмените черновик и обновите данные.")
                if remote != payload:
                    upstream(client, "PUT", f"streams/{flow_id}", json={"offers": payload})
                refreshed = upstream_object(client, "GET", f"streams/{flow_id}")
                if remote_offers(refreshed) != payload:
                    raise HTTPException(502, "Keitaro вернул другие доли или статусы. Черновик сохранён.")
                saved.published = synced_rows(refreshed, saved.draft)
                saved.draft = None
        else:
            if data.offer_id is None:
                raise HTTPException(422, "Укажите ID оффера")
            rows = [row.copy() for row in (saved.draft if saved.draft is not None else saved.published)]
            offer = next((row for row in rows if row["offer_id"] == data.offer_id), None)
            if data.action == "add" and offer is None:
                ensure_available_offer(client, data.offer_id)
                rows.append({"offer_id": data.offer_id, "share": 0, "state": "active", "pinned": False})
            elif data.action == "restore" and offer and offer["state"] == "removed":
                ensure_available_offer(client, data.offer_id)
                offer["state"] = "active"
            elif data.action == "remove" and offer and offer["state"] != "removed":
                offer.update(state="removed", share=0, pinned=False)
            elif data.action in {"pin", "unpin"} and offer and offer["state"] == "active":
                offer["pinned"] = data.action == "pin"
            else:
                raise HTTPException(409, "Действие недоступно для этого оффера")
            if data.action not in {"pin", "unpin"}:
                rebalance(rows)
            saved.draft = rows
        db.commit()
        return {"dirty": saved.draft is not None}


@app.get("/api/campaigns/{campaign_id}/stats")
def campaign_stats(campaign_id: int, client: Tracker):
    owned_campaign(client, campaign_id)
    report = upstream_object(client, "POST", "report/build", ("rows",), json={
        "range": {"interval": "all_time", "timezone": os.getenv("REPORT_TIMEZONE", "Europe/Moscow")},
        "dimensions": ["stream_id", "offer_id"],
        "measures": ["clicks", "conversions", "revenue", "cost", "profit"],
        "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
    })
    if not isinstance(report["rows"], list) or any(not isinstance(row, dict) for row in report["rows"]):
        raise HTTPException(502, "Неожиданный формат отчёта Keitaro")
    return {"rows": report["rows"]}
