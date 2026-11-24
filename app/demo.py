"""Local example data for trying the workspace without a tracker account."""

import json
from threading import Lock

import httpx


class DemoTracker:
    def __init__(self):
        self.lock = Lock()
        self.campaigns = {1: {"id": 1, "name": "test_demo_spring", "state": "active"}}
        self.streams = {
            1: {"id": 1, "campaign_id": 1, "name": "AU → Google", "schema": "redirect"},
            2: {"id": 2, "campaign_id": 1, "name": "Офферы", "schema": "landings", "offers": [
                {"offer_id": 1, "share": 100, "state": "active"},
            ]},
        }
        self.offers = [
            {"id": 1, "name": "Витрина · сезонная коллекция", "state": "active"},
            {"id": 2, "name": "Подписка · пробный период", "state": "active"},
            {"id": 3, "name": "Промокод · первый заказ", "state": "active"},
        ]

    def request(self, request):
        path = request.url.path.removeprefix("/admin_api/v1/").strip("/").split("/")
        payload = json.loads(request.content) if request.content else {}
        with self.lock:
            return self.dispatch(request, path, payload)

    def dispatch(self, request, path, payload):
        def response(data, status=200):
            return httpx.Response(status, json=data)

        method = request.method
        catalogs = {
            "domains": [{"id": 1, "name": "example.test"}],
            "groups": [{"id": 1, "name": "Демо"}],
            "traffic_sources": [{"id": 1, "name": "Поисковая реклама"}],
            "offers": self.offers,
        }
        if path[0] in catalogs and method == "GET":
            entries = catalogs[path[0]]
            if len(path) == 1:
                offset = int(request.url.params.get("offset", 0))
                limit = int(request.url.params.get("limit", len(entries)))
                return response(entries[offset:offset + limit])
            offer = next((row for row in entries if str(row["id"]) == path[1]), None)
            return response(offer, 200 if offer else 404)
        if path == ["campaigns"] and method == "GET":
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", len(self.campaigns)))
            return response(list(self.campaigns.values())[offset:offset + limit])
        if path in (["campaigns"], ["streams"]) and method == "POST":
            items = self.campaigns if path[0] == "campaigns" else self.streams
            item_id = max(items, default=0) + 1
            items[item_id] = {**payload, "id": item_id}
            return response(items[item_id], 201)
        if len(path) == 3 and path[0] == "campaigns" and path[2] == "streams":
            return response([row for row in self.streams.values() if str(row["campaign_id"]) == path[1]])
        if len(path) == 2 and path[0] in {"campaigns", "streams"}:
            items = self.campaigns if path[0] == "campaigns" else self.streams
            item = items.get(int(path[1])) if path[1].isdigit() else None
            if item is not None and method in {"GET", "PUT"}:
                if method == "PUT":
                    item.update(payload)
                return response(item)
        if path == ["report", "build"] and method == "POST":
            campaign_id = payload["filters"][0]["expression"]
            rows = [{"stream_id": 2, "offer_id": 1, "clicks": 126, "conversions": 8,
                     "revenue": 96, "cost": 38, "profit": 58}] if campaign_id == 1 else []
            return response({"rows": rows, "total": len(rows)})
        return response({"error": "Not found"}, 404)
