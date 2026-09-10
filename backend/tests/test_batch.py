import os
os.environ["DATABASE_URL"] = "sqlite:///./test_crate_trace.db"
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from app.database import Base, engine
from app.main import app

@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

NOW = datetime.utcnow()

def crate(client, code, status="dirty"):
    r = client.post("/api/crates", json={"code": code, "name": f"箱{code}", "location": "仓库", "cleaning_status": status, "notes": ""})
    assert r.status_code == 201
    return r.json()

def event(client, no, code, typ, at=None):
    r = client.post("/api/events", json={"event_no": no, "crate_code": code, "event_type": typ,
                                         "occurred_at": (at or NOW).isoformat(), "operator": "王工", "description": ""})
    assert r.status_code == 201
    return r.json()

def batch(client, codes, batch_no="PCH-1", typ="wash", at=None):
    return client.post("/api/events/batch", json={"batch_no": batch_no, "event_type": typ,
                                                  "occurred_at": (at or NOW).isoformat(), "operator": "张师傅",
                                                  "description": "统一处理", "crate_codes": codes})

def crates_by_code(client):
    return {c["code"]: c for c in client.get("/api/crates").json()}

def test_batch_wash_all_clean(client):
    crate(client, "BX-A", "dirty"); crate(client, "BX-B", "unknown"); crate(client, "BX-C", "dirty")
    r = batch(client, ["BX-A", "BX-B", "BX-C"])
    assert r.status_code == 201
    body = r.json()
    assert body["batch_no"] == "PCH-1"
    assert [x["crate_code"] for x in body["results"]] == ["BX-A", "BX-B", "BX-C"]
    assert [x["event_no"] for x in body["results"]] == ["PCH-1-BX-A", "PCH-1-BX-B", "PCH-1-BX-C"]
    assert [x["event"]["event_no"] for x in body["results"]] == ["PCH-1-BX-A", "PCH-1-BX-B", "PCH-1-BX-C"]
    crates = crates_by_code(client)
    assert all(c["cleaning_status"] == "clean" and c["location"] == "清洗区" for c in crates.values())
    assert len(client.get("/api/events").json()) == 3

def test_batch_issue_risks_follow_each_crate_state(client):
    crate(client, "BX-DIRTY", "dirty")                       # 未清洗 + 从未检查
    crate(client, "BX-CLEAN", "clean")                       # 已清洁且刚检查 → 无风险
    crate(client, "BX-ISO", "clean")                         # 已清洁刚检查但已隔离
    event(client, "EV-INSP-1", "BX-CLEAN", "inspect")
    event(client, "EV-INSP-2", "BX-ISO", "inspect")
    event(client, "EV-ISO", "BX-ISO", "isolate")
    r = batch(client, ["BX-DIRTY", "BX-CLEAN", "BX-ISO"], batch_no="PCH-2", typ="issue")
    assert r.status_code == 201
    by_crate = {}
    for i in client.get("/api/issues").json():
        by_crate.setdefault(i["crate_code"], set()).add(i["issue_type"])
    assert by_crate.get("BX-DIRTY") == {"reuse_without_wash", "expired_inspection"}
    assert "BX-CLEAN" not in by_crate
    assert by_crate.get("BX-ISO") == {"use_while_isolated"}
    crates = crates_by_code(client)
    assert all(c["cleaning_status"] == "dirty" and c["location"] == "使用中" for c in crates.values())

def test_batch_invalid_crate_rolls_back_everything(client):
    crate(client, "BX-1", "dirty"); crate(client, "BX-2", "clean")
    before = client.get("/api/crates").json()
    r = batch(client, ["BX-1", "BX-NONE", "BX-2"], batch_no="PCH-3")
    assert r.status_code == 404
    assert "BX-NONE" in r.json()["detail"]
    assert client.get("/api/events").json() == []
    assert client.get("/api/issues").json() == []
    assert client.get("/api/crates").json() == before

def test_batch_deactivated_crate_rolls_back(client):
    crate(client, "BX-1"); c2 = crate(client, "BX-2")
    client.post(f"/api/crates/{c2['id']}/deactivate")
    r = batch(client, ["BX-1", "BX-2"], batch_no="PCH-4")
    assert r.status_code == 409
    assert "BX-2" in r.json()["detail"]
    assert client.get("/api/events").json() == []
    assert crates_by_code(client)["BX-1"]["cleaning_status"] == "dirty"

def test_batch_event_no_conflict_rolls_back(client):
    crate(client, "BX-1"); crate(client, "BX-2")
    event(client, "PCH-5-BX-2", "BX-2", "wash")
    r = batch(client, ["BX-1", "BX-2"], batch_no="PCH-5")
    assert r.status_code == 409
    assert "BX-2" in r.json()["detail"]
    assert len(client.get("/api/events").json()) == 1
    assert crates_by_code(client)["BX-1"]["cleaning_status"] == "dirty"

def test_batch_duplicate_codes_rejected(client):
    crate(client, "BX-1")
    r = batch(client, ["BX-1", "BX-1"], batch_no="PCH-6")
    assert r.status_code == 409
    assert "BX-1" in r.json()["detail"]
    assert client.get("/api/events").json() == []

def test_batch_inspect_and_isolate_updates(client):
    crate(client, "BX-1", "dirty"); crate(client, "BX-2", "dirty")
    at = NOW - timedelta(days=1)
    r = batch(client, ["BX-1", "BX-2"], batch_no="PCH-7", typ="inspect", at=at)
    assert r.status_code == 201
    crates = crates_by_code(client)
    assert all(c["last_inspected_at"] and not c["isolated"] for c in crates.values())
    r = batch(client, ["BX-1", "BX-2"], batch_no="PCH-8", typ="isolate")
    assert r.status_code == 201
    crates = crates_by_code(client)
    assert all(c["isolated"] and c["location"] == "隔离区" for c in crates.values())

def test_batch_validation_errors(client):
    crate(client, "BX-1")
    assert batch(client, [], batch_no="PCH-9").status_code == 422
    assert client.post("/api/events/batch", json={"batch_no": "非法 批次", "event_type": "wash",
                                                  "occurred_at": NOW.isoformat(), "operator": "A",
                                                  "crate_codes": ["BX-1"]}).status_code == 422
    assert client.get("/api/events").json() == []

def test_single_event_regression_after_batch(client):
    crate(client, "BX-1", "dirty")
    assert batch(client, ["BX-1"], batch_no="PCH-10").status_code == 201
    r = client.post("/api/events", json={"event_no": "EV-SOLO", "crate_code": "BX-1", "event_type": "issue",
                                         "occurred_at": NOW.isoformat(), "operator": "王工", "description": "领用"})
    assert r.status_code == 201
    assert client.post("/api/events", json={"event_no": "EV-SOLO", "crate_code": "BX-1", "event_type": "issue",
                                            "occurred_at": NOW.isoformat(), "operator": "王工"}).status_code == 409
    assert {i["issue_type"] for i in client.get("/api/issues").json()} == {"expired_inspection"}
