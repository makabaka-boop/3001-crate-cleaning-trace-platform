import os
os.environ["DATABASE_URL"] = "sqlite:///./test_inventory.db"
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from app.database import Base, engine
from app.main import app, ensure_schema
from app.models import Crate, InventoryCheck, InventoryCheckItem

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

def crate(client, code, location="仓库", status="dirty"):
    r = client.post("/api/crates", json={"code": code, "name": f"箱{code}", "location": location, "cleaning_status": status, "notes": ""})
    assert r.status_code == 201
    return r.json()

def bind(client, crate_id, backup_code):
    r = client.post(f"/api/crates/{crate_id}/backup-code", json={"backup_code": backup_code})
    assert r.status_code == 200
    return r.json()

def create_check(client, location="仓库"):
    return client.post("/api/inventory-checks", json={"location": location})

def complete(client, check_id, codes):
    return client.post(f"/api/inventory-checks/{check_id}/complete", json={"scanned_codes": codes})

def get_check(client, check_id):
    r = client.get(f"/api/inventory-checks/{check_id}")
    assert r.status_code == 200
    return r.json()

# ---------- 创建盘点单：在用箱体快照 ----------

def test_create_check_snapshots_active_crates_at_location(client):
    crate(client, "BX-1"); crate(client, "BX-2")
    crate(client, "BX-3", location="清洗区")                    # 其他库位不进快照
    c4 = crate(client, "BX-4")
    client.post(f"/api/crates/{c4['id']}/deactivate")           # 停用箱不进快照
    r = create_check(client)
    assert r.status_code == 201
    body = r.json()
    assert body["location"] == "仓库" and body["status"] == "in_progress"
    assert body["expected"] == ["BX-1", "BX-2"]
    assert body["scanned"] == [] and body["missing"] == [] and body["misplaced"] == []
    assert body["completed_at"] is None
    # 列表与详情均可查，进行中仅应在可见
    assert [c["id"] for c in client.get("/api/inventory-checks").json()] == [body["id"]]
    assert get_check(client, body["id"])["expected"] == ["BX-1", "BX-2"]

def test_create_check_empty_location_404(client):
    crate(client, "BX-1")                                       # 仓库有箱，清洗区为空库位
    r = create_check(client, "清洗区")
    assert r.status_code == 404 and "清洗区" in r.json()["detail"]
    # 只有停用箱的库位同样视为空库位
    c2 = crate(client, "BX-2", location="隔离区")
    client.post(f"/api/crates/{c2['id']}/deactivate")
    r = create_check(client, "隔离区")
    assert r.status_code == 404 and "隔离区" in r.json()["detail"]
    assert client.get("/api/inventory-checks").json() == []

def test_create_check_blank_location_422(client):
    assert client.post("/api/inventory-checks", json={"location": "  "}).status_code == 422
    assert client.post("/api/inventory-checks", json={}).status_code == 422

# ---------- 完成盘点：缺失与错放 ----------

def test_complete_check_distinguishes_missing_and_misplaced(client):
    crate(client, "BX-1"); crate(client, "BX-2"); crate(client, "BX-3")
    crate(client, "BX-9", location="清洗区")
    check = create_check(client).json()
    r = complete(client, check["id"], ["BX-1", "BX-2", "BX-9"])
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed" and body["completed_at"]
    assert body["expected"] == ["BX-1", "BX-2", "BX-3"]         # 应在：创建时快照
    assert body["scanned"] == ["BX-1", "BX-2", "BX-9"]          # 实扫：含错放箱
    assert body["missing"] == ["BX-3"]                          # 缺失：应在而未扫到
    assert body["misplaced"] == [{"crate_code": "BX-9", "location": "清洗区"}]
    # 已完成盘点保留完整结果，供当次结果复看
    again = get_check(client, check["id"])
    assert again == body
    rows = client.get("/api/inventory-checks?status=completed").json()
    assert [c["id"] for c in rows] == [check["id"]]
    assert client.get("/api/inventory-checks?status=in_progress").json() == []

def test_backup_code_scan_counts_toward_primary_crate(client):
    c1 = crate(client, "BX-1"); crate(client, "BX-2")
    bind(client, c1["id"], "BX-1-NEW")
    check = create_check(client).json()
    r = complete(client, check["id"], ["BX-1-NEW", "BX-2"])
    assert r.status_code == 200
    body = r.json()
    # 备用编号解析到同一箱体，结果归入主编号
    assert body["scanned"] == ["BX-1", "BX-2"]
    assert body["missing"] == [] and body["misplaced"] == []

def test_crate_moved_after_creation_settles_by_original_snapshot(client):
    crate(client, "BX-1"); crate(client, "BX-2")
    c3 = crate(client, "BX-3", location="清洗区")
    check = create_check(client).json()
    assert check["expected"] == ["BX-1", "BX-2"]
    # 盘点创建后移动箱体：BX-2 搬离仓库，BX-3 搬入仓库
    crates = {c["code"]: c for c in client.get("/api/crates").json()}
    client.put(f"/api/crates/{crates['BX-2']['id']}", json={"location": "待清洗区"})
    client.put(f"/api/crates/{c3['id']}", json={"location": "仓库"})
    r = complete(client, check["id"], ["BX-1", "BX-3"])
    assert r.status_code == 200
    body = r.json()
    # 仍按原快照结算：BX-2 缺失（即使已搬离），BX-3 错放（即使已搬入）
    assert body["expected"] == ["BX-1", "BX-2"]
    assert body["scanned"] == ["BX-1", "BX-3"]
    assert body["missing"] == ["BX-2"]
    assert body["misplaced"] == [{"crate_code": "BX-3", "location": "仓库"}]

def test_completed_check_immutable_after_later_crate_changes(client):
    crate(client, "BX-1"); c2 = crate(client, "BX-2")
    check = create_check(client).json()
    body = complete(client, check["id"], ["BX-1"]).json()
    assert body["missing"] == ["BX-2"]
    # 完成后的箱体位置变更与停用不改写历史结论
    client.put(f"/api/crates/{c2['id']}", json={"location": "隔离区"})
    client.post(f"/api/crates/{c2['id']}/deactivate")
    again = get_check(client, check["id"])
    assert again["expected"] == ["BX-1", "BX-2"]
    assert again["scanned"] == ["BX-1"] and again["missing"] == ["BX-2"] and again["misplaced"] == []

# ---------- 业务反馈：未知编号 / 重复扫描 / 停用箱 / 重复完成 ----------

def test_unknown_code_rejected_check_stays_open_then_retry(client):
    crate(client, "BX-1"); crate(client, "BX-2")
    check = create_check(client).json()
    r = complete(client, check["id"], ["BX-1", "BX-NONE"])
    assert r.status_code == 404 and "BX-NONE" in r.json()["detail"]
    # 不完成盘点：单仍为进行中，无差异结果落库
    kept = get_check(client, check["id"])
    assert kept["status"] == "in_progress"
    assert kept["scanned"] == [] and kept["missing"] == [] and kept["misplaced"] == []
    # 修正后重试成功
    r = complete(client, check["id"], ["BX-1", "BX-2"])
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert r.json()["missing"] == []

def test_duplicate_scan_rejected_check_stays_open_then_retry(client):
    c1 = crate(client, "BX-1"); crate(client, "BX-2")
    bind(client, c1["id"], "BX-1-NEW")
    check = create_check(client).json()
    # 同一编号重复扫描
    r = complete(client, check["id"], ["BX-1", "BX-1", "BX-2"])
    assert r.status_code == 409 and "BX-1" in r.json()["detail"]
    # 同一箱体以主、备编号混入
    r = complete(client, check["id"], ["BX-1", "BX-1-NEW", "BX-2"])
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "BX-1" in detail and "BX-1-NEW" in detail
    assert get_check(client, check["id"])["status"] == "in_progress"
    # 修正后重试成功
    r = complete(client, check["id"], ["BX-1-NEW", "BX-2"])
    assert r.status_code == 200 and r.json()["scanned"] == ["BX-1", "BX-2"]

def test_deactivated_crate_scan_rejected_check_stays_open(client):
    crate(client, "BX-1"); c2 = crate(client, "BX-2")
    c9 = crate(client, "BX-9", location="清洗区")
    check = create_check(client).json()
    client.post(f"/api/crates/{c9['id']}/deactivate")
    # 扫到已停用箱（不在快照内）→ 409 指出具体箱号
    r = complete(client, check["id"], ["BX-1", "BX-9"])
    assert r.status_code == 409 and "BX-9" in r.json()["detail"]
    # 快照内的箱在盘点期间被停用，扫到同样拒绝
    client.post(f"/api/crates/{c2['id']}/deactivate")
    r = complete(client, check["id"], ["BX-1", "BX-2"])
    assert r.status_code == 409 and "BX-2" in r.json()["detail"]
    assert get_check(client, check["id"])["status"] == "in_progress"
    # 修正扫描内容后可完成：停用箱未扫到，按快照记为缺失
    r = complete(client, check["id"], ["BX-1"])
    assert r.status_code == 200 and r.json()["missing"] == ["BX-2"]

def test_double_completion_rejected_and_result_unchanged(client):
    crate(client, "BX-1"); crate(client, "BX-2")
    check = create_check(client).json()
    assert complete(client, check["id"], ["BX-1"]).status_code == 200
    r = complete(client, check["id"], ["BX-1", "BX-2"])
    assert r.status_code == 409 and "重复完成" in r.json()["detail"]
    again = get_check(client, check["id"])
    assert again["scanned"] == ["BX-1"] and again["missing"] == ["BX-2"]

def test_unknown_check_404(client):
    assert client.get("/api/inventory-checks/99999").status_code == 404
    assert complete(client, 99999, ["BX-1"]).status_code == 404

def test_complete_validation_errors(client):
    crate(client, "BX-1")
    check = create_check(client).json()
    assert complete(client, check["id"], []).status_code == 422
    assert complete(client, check["id"], ["BX 1"]).status_code == 422
    assert client.post(f"/api/inventory-checks/{check['id']}/complete", json={}).status_code == 422
    assert get_check(client, check["id"])["status"] == "in_progress"

def test_list_checks_newest_first(client):
    crate(client, "BX-1"); crate(client, "BX-2", location="清洗区")
    first = create_check(client).json()
    second = create_check(client, "清洗区").json()
    complete(client, first["id"], ["BX-1"])
    rows = client.get("/api/inventory-checks").json()
    assert [c["id"] for c in rows] == [second["id"], first["id"]]
    assert [c["status"] for c in rows] == ["in_progress", "completed"]

# ---------- 兼容性回归：既有行为不变 ----------

def test_existing_ledger_event_and_backup_behaviors_unchanged(client):
    c1 = crate(client, "BX-1")
    bind(client, c1["id"], "BX-1-NEW")
    r = client.post("/api/events", json={"event_no": "EV-1", "crate_code": "BX-1-NEW", "event_type": "wash",
                                         "occurred_at": NOW.isoformat(), "operator": "王工", "description": ""})
    assert r.status_code == 201 and r.json()["crate_code"] == "BX-1"
    box = next(c for c in client.get("/api/crates").json() if c["code"] == "BX-1")
    assert box["cleaning_status"] == "clean" and box["location"] == "清洗区"
    assert client.get("/api/events?crate_code=BX-1-NEW").json()[0]["crate_code"] == "BX-1"
    # 盘点快照不影响台账筛选与流转登记
    check = create_check(client, "清洗区").json()
    assert check["expected"] == ["BX-1"]
    rows = client.get("/api/crates?code=BX-1-NEW").json()
    assert [c["code"] for c in rows] == ["BX-1"]

# ---------- 旧库增量迁移 ----------

def test_legacy_db_migration_creates_inventory_tables_preserving_data():
    # 模拟旧库：只有 crates/events/issues 三类旧表且已有数据，没有盘点表
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
    assert "inventory_checks" not in inspect(legacy).get_table_names()
    saved_engine = ensure_schema.__globals__["engine"]
    ensure_schema.__globals__["engine"] = legacy
    try:
        ensure_schema()      # 增量迁移：补齐盘点表
        ensure_schema()      # 幂等：再次启动不报错
    finally:
        ensure_schema.__globals__["engine"] = saved_engine
    assert {"inventory_checks", "inventory_check_items"} <= set(inspect(legacy).get_table_names())
    sess = sessionmaker(bind=legacy)()
    try:
        old = sess.get(Crate, 1)
        assert old is not None and old.code == "BX-OLD" and old.location == "仓库"
        # 旧库上可直接建立盘点单并固化结果
        check = InventoryCheck(location="仓库")
        sess.add(check); sess.flush()
        sess.add(InventoryCheckItem(check_id=check.id, crate_id=old.id, crate_code=old.code, location=old.location, result="matched"))
        sess.commit()
        assert sess.query(InventoryCheck).count() == 1
        assert sess.query(InventoryCheckItem).first().crate_code == "BX-OLD"
    finally:
        sess.close()
