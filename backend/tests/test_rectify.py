import os
os.environ["DATABASE_URL"] = "sqlite:///./test_rectify.db"
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from app.database import Base, engine, get_db
from app.main import app, ensure_schema
from app.models import Crate, Event, Issue

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

def crate(client, code="BX-TEST", status="dirty", **extra):
    payload = {"code": code, "name": f"箱{code}", "location": "仓库", "cleaning_status": status, "notes": ""}
    payload.update(extra)
    r = client.post("/api/crates", json=payload)
    assert r.status_code == 201
    return r.json()

def event(client, no, code, typ, at=None, issue_id=None):
    body = {"event_no": no, "crate_code": code, "event_type": typ,
            "occurred_at": (at or NOW).isoformat(), "operator": "王工", "description": ""}
    if issue_id is not None: body["issue_id"] = issue_id
    return client.post("/api/events", json=body)

def issue_of_type(client, issue_type):
    issues = client.get("/api/issues").json()
    return next(i for i in issues if i["issue_type"] == issue_type)

def confirm(client, issue_id, note="已现场核实"):
    r = client.patch(f"/api/issues/{issue_id}", json={"status": "confirmed", "resolution_note": note})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    return r.json()

def raise_risk(client, code, at=None):
    """登记一次领用：未清洗且从未检查的箱同时产生未清洗领用与检查过期问题。"""
    r = event(client, f"EV-USE-{code}", code, "issue", at=at)
    assert r.status_code == 201
    return r

def test_unwashed_issue_closed_by_wash_and_crate_updated(client):
    crate(client, "BX-A", "dirty")
    raise_risk(client, "BX-A")
    issue = confirm(client, issue_of_type(client, "reuse_without_wash")["id"])
    r = event(client, "EV-WASH-A", "BX-A", "wash", issue_id=issue["id"])
    assert r.status_code == 201
    assert r.json()["event_no"] == "EV-WASH-A"
    issues = {i["issue_type"]: i for i in client.get("/api/issues").json()}
    closed = issues["reuse_without_wash"]
    assert closed["status"] == "closed"
    assert closed["rectification_event_no"] == "EV-WASH-A"
    # 同次领用产生的检查过期问题不随清洗关闭
    assert issues["expired_inspection"]["status"] == "pending"
    assert issues["expired_inspection"]["rectification_event_no"] is None
    box = next(c for c in client.get("/api/crates").json() if c["code"] == "BX-A")
    assert box["cleaning_status"] == "clean" and box["location"] == "清洗区"

def test_expired_inspection_issue_closed_by_inspect_and_exits_plan(client):
    crate(client, "BX-B", "dirty")
    raise_risk(client, "BX-B")
    assert [i["code"] for i in client.get("/api/inspection-plan").json()["items"]] == ["BX-B"]
    issue = confirm(client, issue_of_type(client, "expired_inspection")["id"])
    r = event(client, "EV-INSP-B", "BX-B", "inspect", issue_id=issue["id"])
    assert r.status_code == 201
    closed = issue_of_type(client, "expired_inspection")
    assert closed["status"] == "closed" and closed["rectification_event_no"] == "EV-INSP-B"
    box = next(c for c in client.get("/api/crates").json() if c["code"] == "BX-B")
    assert box["last_inspected_at"] and not box["isolated"]
    # 新检查把到期日推到 30 天后，退出当前（未来 7 天）检查计划
    assert client.get("/api/inspection-plan").json()["items"] == []
    # 同箱的未清洗领用问题不随检查关闭
    assert issue_of_type(client, "reuse_without_wash")["status"] == "pending"

def test_isolated_use_issue_closed_by_inspect_and_crate_released(client):
    c = crate(client, "BX-C", "clean")
    event(client, "EV-INSP-C", "BX-C", "inspect", at=NOW - timedelta(days=1))
    event(client, "EV-ISO-C", "BX-C", "isolate")
    raise_risk(client, "BX-C")
    issue = confirm(client, issue_of_type(client, "use_while_isolated")["id"])
    r = event(client, "EV-INSP-C2", "BX-C", "inspect", issue_id=issue["id"])
    assert r.status_code == 201
    closed = issue_of_type(client, "use_while_isolated")
    assert closed["status"] == "closed" and closed["rectification_event_no"] == "EV-INSP-C2"
    box = next(x for x in client.get("/api/crates").json() if x["code"] == "BX-C")
    assert box["isolated"] is False

def test_unknown_issue_rejected_without_changes(client):
    crate(client, "BX-A", "dirty")
    before_events = client.get("/api/events").json()
    r = event(client, "EV-WASH-X", "BX-A", "wash", issue_id=99999)
    assert r.status_code == 404 and "问题不存在" in r.json()["detail"]
    assert client.get("/api/events").json() == before_events
    box = next(c for c in client.get("/api/crates").json() if c["code"] == "BX-A")
    assert box["cleaning_status"] == "dirty" and box["location"] == "仓库"

def test_unconfirmed_issue_rejected_and_kept_open(client):
    crate(client, "BX-A", "dirty")
    raise_risk(client, "BX-A")
    issue = issue_of_type(client, "reuse_without_wash")      # 仍为 pending
    r = event(client, "EV-WASH-A", "BX-A", "wash", issue_id=issue["id"])
    assert r.status_code == 409 and "尚未确认" in r.json()["detail"]
    after = issue_of_type(client, "reuse_without_wash")
    assert after["status"] == "pending" and after["rectification_event_no"] is None
    assert [e["event_no"] for e in client.get("/api/events").json()] == [f"EV-USE-BX-A"]

def test_wrong_crate_rejected_event_and_issue_unchanged(client):
    crate(client, "BX-A", "dirty"); crate(client, "BX-B", "dirty")
    raise_risk(client, "BX-A")
    issue = confirm(client, issue_of_type(client, "reuse_without_wash")["id"])
    r = event(client, "EV-WASH-B", "BX-B", "wash", issue_id=issue["id"])
    assert r.status_code == 409 and "箱体不一致" in r.json()["detail"]
    assert [e["event_no"] for e in client.get("/api/events").json()] == ["EV-USE-BX-A"]
    untouched = next(c for c in client.get("/api/crates").json() if c["code"] == "BX-B")
    assert untouched["cleaning_status"] == "dirty" and untouched["location"] == "仓库"
    after = issue_of_type(client, "reuse_without_wash")
    assert after["status"] == "confirmed" and after["rectification_event_no"] is None

def test_wrong_rectification_type_rejected_without_changes(client):
    crate(client, "BX-A", "dirty")
    raise_risk(client, "BX-A")
    issue = confirm(client, issue_of_type(client, "reuse_without_wash")["id"])
    # 未清洗领用必须清洗，登记检查不符合整改建议
    r = event(client, "EV-INSP-A", "BX-A", "inspect", issue_id=issue["id"])
    assert r.status_code == 409 and "整改建议" in r.json()["detail"]
    assert [e["event_no"] for e in client.get("/api/events").json()] == ["EV-USE-BX-A"]
    box = next(c for c in client.get("/api/crates").json() if c["code"] == "BX-A")
    assert box["cleaning_status"] == "dirty" and box["last_inspected_at"] is None
    after = issue_of_type(client, "reuse_without_wash")
    assert after["status"] == "confirmed" and after["rectification_event_no"] is None
    # 过期检查问题要求检查，登记清洗同样拒绝
    expired = confirm(client, issue_of_type(client, "expired_inspection")["id"])
    r2 = event(client, "EV-WASH-A2", "BX-A", "wash", issue_id=expired["id"])
    assert r2.status_code == 409

def test_duplicate_event_no_with_issue_rolls_back_closure(client):
    crate(client, "BX-A", "dirty")
    raise_risk(client, "BX-A")
    issue = confirm(client, issue_of_type(client, "reuse_without_wash")["id"])
    assert event(client, "EV-DUP", "BX-A", "wash").status_code == 201
    r = event(client, "EV-DUP", "BX-A", "wash", issue_id=issue["id"])
    assert r.status_code == 409
    after = issue_of_type(client, "reuse_without_wash")
    assert after["status"] == "confirmed" and after["rectification_event_no"] is None

def test_single_event_without_issue_keeps_original_behavior(client):
    crate(client, "BX-A", "dirty")
    r = event(client, "EV-WASH-A", "BX-A", "wash")
    assert r.status_code == 201 and "issue_id" not in r.json()
    box = next(c for c in client.get("/api/crates").json() if c["code"] == "BX-A")
    assert box["cleaning_status"] == "clean" and box["location"] == "清洗区"
    raise_risk(client, "BX-A")
    issues = client.get("/api/issues").json()
    assert all(i["rectification_event_no"] is None for i in issues)
    # 原有问题状态手工修改仍可用
    i = issues[0]["id"]
    patched = client.patch(f"/api/issues/{i}", json={"status": "false_positive", "resolution_note": "误报"})
    assert patched.json()["status"] == "false_positive"

def test_batch_registration_keeps_original_behavior(client):
    crate(client, "BX-A", "dirty"); crate(client, "BX-B", "dirty")
    r = client.post("/api/events/batch", json={"batch_no": "PCH-1", "event_type": "wash",
                                               "occurred_at": NOW.isoformat(), "operator": "张师傅",
                                               "description": "", "crate_codes": ["BX-A", "BX-B"]})
    assert r.status_code == 201
    assert [x["event_no"] for x in r.json()["results"]] == ["PCH-1-BX-A", "PCH-1-BX-B"]
    crates = {c["code"]: c for c in client.get("/api/crates").json()}
    assert all(c["cleaning_status"] == "clean" for c in crates.values())
    # 批量登记不支持问题编号，不产生问题关闭
    assert client.get("/api/issues").json() == []

def test_closed_issue_can_still_be_listed_with_event_no(client):
    crate(client, "BX-A", "dirty")
    raise_risk(client, "BX-A")
    issue = confirm(client, issue_of_type(client, "reuse_without_wash")["id"])
    event(client, "EV-WASH-A", "BX-A", "wash", issue_id=issue["id"])
    # 过滤条件与既有字段继续可用，仅追加可空整改事件编号
    rows = client.get("/api/issues?status=closed&crate_code=BX-A").json()
    assert len(rows) == 1
    assert set(rows[0]) >= {"id", "crate_id", "crate_code", "crate_name", "issue_type", "occurred_at",
                            "reason", "status", "resolution_note", "rectification_event_no"}

def test_legacy_db_incremental_migration_preserves_history():
    # 模拟旧库：缺少 rectification_event_id 列的 issues 表及历史行
    legacy = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(legacy, tables=[t for t in Base.metadata.sorted_tables if t.name != "issues"])
    with legacy.begin() as conn:
        conn.execute(text("""CREATE TABLE issues (
            id INTEGER NOT NULL PRIMARY KEY,
            crate_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            issue_type VARCHAR(30) NOT NULL,
            occurred_at DATETIME NOT NULL,
            reason TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            resolution_note TEXT NOT NULL DEFAULT ''
        )"""))
        conn.execute(text("""INSERT INTO crates (code,name,location,cleaning_status,notes,active,isolated,last_inspected_at,created_at)
            VALUES ('BX-OLD','旧箱','待清洗区','dirty','',1,0,NULL,:t)"""), {"t": NOW})
        conn.execute(text("""INSERT INTO events (event_no,crate_id,event_type,occurred_at,operator,description)
            VALUES ('EV-OLD',1,'issue',:t,'李工','领用')"""), {"t": NOW})
        conn.execute(text("""INSERT INTO issues (id,crate_id,event_id,issue_type,occurred_at,reason,status,resolution_note)
            VALUES (1,1,1,'reuse_without_wash',:t,'周转箱未清洗即再次领用','confirmed','历史说明')"""), {"t": NOW})
    assert "rectification_event_id" not in {c["name"] for c in inspect(legacy).get_columns("issues")}
    saved_engine = ensure_schema.__globals__["engine"]
    ensure_schema.__globals__["engine"] = legacy
    try:
        ensure_schema()      # 增量迁移
        ensure_schema()      # 幂等：再次启动不报错
    finally:
        ensure_schema.__globals__["engine"] = saved_engine
    assert "rectification_event_id" in {c["name"] for c in inspect(legacy).get_columns("issues")}
    sess = sessionmaker(bind=legacy)()
    try:
        old_issue = sess.get(Issue, 1)
        assert old_issue is not None and old_issue.rectification_event_id is None
        assert old_issue.status == "confirmed" and old_issue.resolution_note == "历史说明"
        # 历史问题经响应序列化时整改事件编号为空，既有字段不变
        from app.main import issue_dict
        d = issue_dict(old_issue)
        assert d["crate_code"] == "BX-OLD" and d["issue_type"] == "reuse_without_wash"
        assert d["rectification_event_no"] is None
    finally:
        sess.close()
