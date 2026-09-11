import os
os.environ["DATABASE_URL"] = "sqlite:///./test_backup_code.db"
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from app.database import Base, engine
from app.main import app, ensure_schema
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

def bind(client, crate_id, backup_code):
    return client.post(f"/api/crates/{crate_id}/backup-code", json={"backup_code": backup_code})

def event(client, no, code, typ, at=None):
    return client.post("/api/events", json={"event_no": no, "crate_code": code, "event_type": typ,
                                            "occurred_at": (at or NOW).isoformat(), "operator": "王工", "description": ""})

def batch(client, codes, batch_no="PCH-B", typ="wash"):
    return client.post("/api/events/batch", json={"batch_no": batch_no, "event_type": typ,
                                                  "occurred_at": NOW.isoformat(), "operator": "张师傅",
                                                  "description": "", "crate_codes": codes})

def crates_by_code(client):
    return {c["code"]: c for c in client.get("/api/crates").json()}

# ---------- 绑定入口 ----------

def test_bind_backup_code_persists_and_returns_primary(client):
    c = crate(client, "BX-1")
    r = bind(client, c["id"], "BX-1-NEW")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "BX-1" and body["backup_code"] == "BX-1-NEW"
    # 台账列表持久化编号归属，未绑定箱体该字段为空
    crates = crates_by_code(client)
    assert crates["BX-1"]["backup_code"] == "BX-1-NEW"
    c2 = crate(client, "BX-2")
    assert crates_by_code(client)["BX-2"]["backup_code"] is None
    # 重复绑定同一编号幂等成功
    assert bind(client, c["id"], "BX-1-NEW").status_code == 200

def test_bind_conflict_with_existing_primary_code_fails(client):
    c1 = crate(client, "BX-1"); crate(client, "BX-2")
    r = bind(client, c1["id"], "BX-2")
    assert r.status_code == 409
    assert "占用" in r.json()["detail"] and "BX-2" in r.json()["detail"]
    assert crates_by_code(client)["BX-1"]["backup_code"] is None

def test_bind_conflict_with_existing_backup_code_fails(client):
    c1 = crate(client, "BX-1"); c2 = crate(client, "BX-2")
    assert bind(client, c1["id"], "BX-OLD").status_code == 200
    r = bind(client, c2["id"], "BX-OLD")
    assert r.status_code == 409 and "占用" in r.json()["detail"]
    assert crates_by_code(client)["BX-2"]["backup_code"] is None

def test_bind_own_primary_code_rejected(client):
    c = crate(client, "BX-1")
    r = bind(client, c["id"], "BX-1")
    assert r.status_code == 409 and "占用" in r.json()["detail"]

def test_bind_unknown_crate_404_and_invalid_format_422(client):
    assert bind(client, 99999, "BX-OLD").status_code == 404
    c = crate(client, "BX-1")
    assert bind(client, c["id"], "非法 编号").status_code == 422
    assert crates_by_code(client)["BX-1"]["backup_code"] is None

def test_create_crate_code_colliding_with_backup_code_rejected(client):
    c = crate(client, "BX-1")
    assert bind(client, c["id"], "BX-OLD").status_code == 200
    r = client.post("/api/crates", json={"code": "BX-OLD", "name": "新箱", "location": "仓库"})
    assert r.status_code == 409 and "占用" in r.json()["detail"]

# ---------- 登记链路解析 ----------

def test_register_wash_with_backup_code_updates_original_crate(client):
    c = crate(client, "BX-1", "dirty")
    assert bind(client, c["id"], "BX-1-NEW").status_code == 200
    r = event(client, "EV-W1", "BX-1-NEW", "wash")
    assert r.status_code == 201
    assert r.json()["crate_id"] == c["id"]                      # 事件落在原箱体
    box = crates_by_code(client)["BX-1"]
    assert box["cleaning_status"] == "clean" and box["location"] == "清洗区"
    # 事件列表仍按主编号检索与返回
    events = client.get("/api/events?crate_code=BX-1").json()
    assert [e["event_no"] for e in events] == ["EV-W1"]
    assert events[0]["crate_id"] == c["id"]

def test_risk_identification_with_backup_code_lands_on_original_crate(client):
    c = crate(client, "BX-1", "dirty")                          # 未清洗且从未检查
    assert bind(client, c["id"], "BX-1-NEW").status_code == 200
    r = event(client, "EV-U1", "BX-1-NEW", "issue")
    assert r.status_code == 201
    issues = client.get("/api/issues").json()
    assert {i["issue_type"] for i in issues} == {"reuse_without_wash", "expired_inspection"}
    assert all(i["crate_code"] == "BX-1" and i["crate_id"] == c["id"] for i in issues)

def test_single_event_unknown_code_still_404(client):
    crate(client, "BX-1")
    r = event(client, "EV-X", "BX-NONE", "wash")
    assert r.status_code == 404
    assert client.get("/api/events").json() == []

# ---------- 批量登记 ----------

def test_batch_with_backup_code_returns_primary_and_updates_crate(client):
    c1 = crate(client, "BX-1", "dirty"); crate(client, "BX-2", "dirty")
    assert bind(client, c1["id"], "BX-1-NEW").status_code == 200
    r = batch(client, ["BX-1-NEW", "BX-2"], batch_no="PCH-B1")
    assert r.status_code == 201
    body = r.json()
    # 响应与事件编号均使用主编号，顺序与提交一致
    assert [x["crate_code"] for x in body["results"]] == ["BX-1", "BX-2"]
    assert [x["event_no"] for x in body["results"]] == ["PCH-B1-BX-1", "PCH-B1-BX-2"]
    crates = crates_by_code(client)
    assert all(c["cleaning_status"] == "clean" and c["location"] == "清洗区" for c in crates.values())
    assert len(client.get("/api/events").json()) == 2

def test_batch_mixed_primary_and_backup_same_crate_rolls_back(client):
    c1 = crate(client, "BX-1", "dirty"); crate(client, "BX-2", "dirty")
    assert bind(client, c1["id"], "BX-1-NEW").status_code == 200
    before = client.get("/api/crates").json()
    r = batch(client, ["BX-1", "BX-1-NEW", "BX-2"], batch_no="PCH-B2")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "BX-1" in detail and "BX-1-NEW" in detail
    # 整批不落库：无事件、无问题，箱体状态保持原样
    assert client.get("/api/events").json() == []
    assert client.get("/api/issues").json() == []
    assert client.get("/api/crates").json() == before

def test_batch_unknown_code_rolls_back(client):
    crate(client, "BX-1", "dirty")
    r = batch(client, ["BX-1", "BX-NONE"], batch_no="PCH-B3")
    assert r.status_code == 404
    assert "BX-NONE" in r.json()["detail"]
    assert client.get("/api/events").json() == []
    assert crates_by_code(client)["BX-1"]["cleaning_status"] == "dirty"

# ---------- 兼容性回归 ----------

def test_primary_code_single_and_batch_registration_unchanged(client):
    c1 = crate(client, "BX-1", "dirty"); crate(client, "BX-2", "dirty")
    assert bind(client, c1["id"], "BX-1-NEW").status_code == 200   # 绑定备用编号不影响主编号链路
    r = event(client, "EV-P1", "BX-1", "wash")
    assert r.status_code == 201 and r.json()["crate_id"] == c1["id"]
    assert crates_by_code(client)["BX-1"]["cleaning_status"] == "clean"
    r = batch(client, ["BX-1", "BX-2"], batch_no="PCH-P1")
    assert r.status_code == 201
    assert [x["crate_code"] for x in r.json()["results"]] == ["BX-1", "BX-2"]
    assert [x["event_no"] for x in r.json()["results"]] == ["PCH-P1-BX-1", "PCH-P1-BX-2"]
    crates = crates_by_code(client)
    assert all(c["cleaning_status"] == "clean" for c in crates.values())

def test_crate_list_filters_unchanged_without_backup_code(client):
    crate(client, "BX-1", "clean"); crate(client, "BX-2", "dirty")
    rows = client.get("/api/crates?code=BX-1&cleaning_status=clean").json()
    assert [c["code"] for c in rows] == ["BX-1"]
    assert rows[0]["backup_code"] is None

# ---------- 旧库增量迁移 ----------

def test_legacy_db_migration_adds_backup_code_preserving_crates_and_events():
    # 模拟旧库：crates 表缺少 backup_code 列，且已有箱体与流转事件
    legacy = create_engine("sqlite:///:memory:")
    with legacy.begin() as conn:
        conn.execute(text("""CREATE TABLE crates (
            id INTEGER NOT NULL PRIMARY KEY,
            code VARCHAR(50) NOT NULL,
            name VARCHAR(100) NOT NULL,
            location VARCHAR(100) NOT NULL,
            cleaning_status VARCHAR(20) NOT NULL,
            notes TEXT NOT NULL,
            active BOOLEAN NOT NULL,
            isolated BOOLEAN NOT NULL,
            last_inspected_at DATETIME,
            created_at DATETIME NOT NULL
        )"""))
        conn.execute(text("""INSERT INTO crates (id,code,name,location,cleaning_status,notes,active,isolated,last_inspected_at,created_at)
            VALUES (1,'BX-OLD','旧箱','仓库','dirty','',1,0,NULL,:t)"""), {"t": NOW})
    Base.metadata.create_all(legacy)   # 其余表按当前模型补齐（checkfirst 跳过已存在的 crates）
    with legacy.begin() as conn:
        conn.execute(text("""INSERT INTO events (event_no,crate_id,event_type,occurred_at,operator,description)
            VALUES ('EV-OLD',1,'issue',:t,'李工','领用')"""), {"t": NOW})
    assert "backup_code" not in {c["name"] for c in inspect(legacy).get_columns("crates")}
    saved_engine = ensure_schema.__globals__["engine"]
    ensure_schema.__globals__["engine"] = legacy
    try:
        ensure_schema()      # 增量迁移
        ensure_schema()      # 幂等：再次启动不报错
    finally:
        ensure_schema.__globals__["engine"] = saved_engine
    assert "backup_code" in {c["name"] for c in inspect(legacy).get_columns("crates")}
    sess = sessionmaker(bind=legacy)()
    try:
        old = sess.get(Crate, 1)
        assert old is not None and old.code == "BX-OLD" and old.backup_code is None
        assert sess.query(Event).count() == 1 and sess.query(Event).first().event_no == "EV-OLD"
    finally:
        sess.close()
