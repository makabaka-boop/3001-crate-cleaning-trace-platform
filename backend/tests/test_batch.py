import os
os.environ["DATABASE_URL"] = "sqlite:///./test_crate_trace.db"
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database import Base, engine, get_db, SessionLocal
from app.main import app
from app.models import Crate, Event

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

def raced_db(batch_no, code, blow_up=None):
    """模拟并发请求：本批校验通过后、写入前，另一连接抢先提交同一批次。"""
    raced = {"done": False}
    def get_raced_db():
        db = SessionLocal()
        orig_flush = db.flush
        def flush(*args, **kwargs):
            if not raced["done"]:
                raced["done"] = True
                other = SessionLocal()
                rival = other.scalar(select(Crate).where(Crate.code == code))
                other.add(Event(event_no=f"{batch_no}-{code}", crate_id=rival.id, event_type="wash",
                                occurred_at=NOW, operator="并发操作人", description=""))
                other.commit(); other.close()
                if blow_up: blow_up()
            return orig_flush(*args, **kwargs)
        db.flush = flush
        try:
            yield db
        finally:
            db.close()
    return get_raced_db

def assert_raced_batch_conflict(client, batch_no, blow_up=None):
    crate(client, "BX-1"); crate(client, "BX-2")
    app.dependency_overrides[get_db] = raced_db(batch_no, "BX-2", blow_up)
    try:
        r = batch(client, ["BX-1", "BX-2"], batch_no=batch_no)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert f"{batch_no}-BX-2" in detail and "BX-2" in detail
    assert [e["event_no"] for e in client.get("/api/events").json()] == [f"{batch_no}-BX-2"]
    crates = crates_by_code(client)
    assert crates["BX-1"]["cleaning_status"] == "dirty" and crates["BX-1"]["location"] == "仓库"

def test_batch_blank_crate_code_rejected_at_validation(client):
    crate(client, "BX-1")
    before = client.get("/api/crates").json()
    for bad in (["   "], ["BX-1", "  \t  "]):
        r = batch(client, bad, batch_no="PCH-11")
        assert r.status_code == 422
    assert client.get("/api/events").json() == []
    assert client.get("/api/crates").json() == before

def test_batch_max_length_batch_no_and_crate_code_accepted(client):
    code = "BX" + "1" * 48          # 箱号上限 50 字符
    batch_no = "PCH" + "2" * 47     # 批次编号上限 50 字符
    crate(client, code)
    r = batch(client, [code], batch_no=batch_no)
    assert r.status_code == 201
    event_no = f"{batch_no}-{code}"
    assert len(event_no) == 101
    assert r.json()["results"][0]["event_no"] == event_no
    assert [e["event_no"] for e in client.get("/api/events").json()] == [event_no]
    assert crates_by_code(client)[code]["cleaning_status"] == "clean"

def test_batch_concurrent_lock_conflict_names_crate(client):
    assert_raced_batch_conflict(client, "PCH-X")

def test_batch_concurrent_integrity_conflict_names_crate(client):
    def blow_up():
        raise IntegrityError("INSERT INTO events ...", None, Exception("UNIQUE constraint failed: events.event_no"))
    assert_raced_batch_conflict(client, "PCH-I", blow_up)

def test_single_event_regression_after_batch(client):
    crate(client, "BX-1", "dirty")
    assert batch(client, ["BX-1"], batch_no="PCH-10").status_code == 201
    r = client.post("/api/events", json={"event_no": "EV-SOLO", "crate_code": "BX-1", "event_type": "issue",
                                         "occurred_at": NOW.isoformat(), "operator": "王工", "description": "领用"})
    assert r.status_code == 201
    assert client.post("/api/events", json={"event_no": "EV-SOLO", "crate_code": "BX-1", "event_type": "issue",
                                            "occurred_at": NOW.isoformat(), "operator": "王工"}).status_code == 409
    assert {i["issue_type"] for i in client.get("/api/issues").json()} == {"expired_inspection"}
