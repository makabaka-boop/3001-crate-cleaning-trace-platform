import os
os.environ["DATABASE_URL"] = "sqlite:///./test_crate_trace.db"
from datetime import date, datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from app.database import Base, engine
from app.main import app, INSPECTION_VALID_DAYS

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

BASE = date(2026, 9, 11)
VALID = INSPECTION_VALID_DAYS  # 默认 30 天有效期

def crate(client, code):
    r = client.post("/api/crates", json={"code": code, "name": f"箱{code}", "location": "仓库", "cleaning_status": "clean", "notes": ""})
    assert r.status_code == 201
    return r.json()

def inspect_at(client, code, at, no=None):
    r = client.post("/api/events", json={"event_no": no or f"EV-INSP-{code}", "crate_code": code, "event_type": "inspect",
                                         "occurred_at": at.isoformat(), "operator": "王工", "description": ""})
    assert r.status_code == 201
    return r.json()

def inspected_days_ago(client, code, days, hour=8):
    """登记一次检查，使到期日相对基准日偏移 VALID-days 天。"""
    at = datetime.combine(BASE - timedelta(days=days), datetime.min.time()).replace(hour=hour)
    return inspect_at(client, code, at)

def plan(client, base=BASE, days=7):
    r = client.get(f"/api/inspection-plan?base_date={base.isoformat()}&days_ahead={days}")
    assert r.status_code == 200
    return r.json()

def test_never_inspected_sorted_first_then_overdue_then_due_soon(client):
    crate(client, "BX-SOON"); inspected_days_ago(client, "BX-SOON", VALID - 5)   # 剩 5 天
    crate(client, "BX-NEW")                                                       # 从未检查
    crate(client, "BX-OD"); inspected_days_ago(client, "BX-OD", VALID + 10)       # 超期 10 天
    body = plan(client)
    assert [i["code"] for i in body["items"]] == ["BX-NEW", "BX-OD", "BX-SOON"]
    assert [i["category"] for i in body["items"]] == ["never_inspected", "overdue", "due_soon"]
    never, overdue, soon = body["items"]
    assert never["due_date"] is None and never["days_remaining"] is None and never["last_inspected_at"] is None
    assert overdue["due_date"] == (BASE - timedelta(days=10)).isoformat() and overdue["days_remaining"] == -10
    assert soon["due_date"] == (BASE + timedelta(days=5)).isoformat() and soon["days_remaining"] == 5
    assert body["base_date"] == BASE.isoformat() and body["days_ahead"] == 7 and body["valid_days"] == VALID
    assert body["total"] == 3

def test_boundary_classification_around_today_and_window_edge(client):
    crate(client, "BX-TODAY"); inspected_days_ago(client, "BX-TODAY", VALID)      # 今天到期 → 即将到期
    crate(client, "BX-YDAY"); inspected_days_ago(client, "BX-YDAY", VALID + 1)    # 昨天到期 → 已过期
    crate(client, "BX-EDGE"); inspected_days_ago(client, "BX-EDGE", VALID - 7)    # 第 7 天到期 → 窗口内
    crate(client, "BX-OUT"); inspected_days_ago(client, "BX-OUT", VALID - 8)      # 第 8 天到期 → 不进入计划
    items = {i["code"]: i for i in plan(client, days=7)["items"]}
    assert set(items) == {"BX-TODAY", "BX-YDAY", "BX-EDGE"}
    assert items["BX-TODAY"]["category"] == "due_soon" and items["BX-TODAY"]["days_remaining"] == 0
    assert items["BX-YDAY"]["category"] == "overdue" and items["BX-YDAY"]["days_remaining"] == -1
    assert items["BX-EDGE"]["category"] == "due_soon" and items["BX-EDGE"]["days_remaining"] == 7

def test_stable_sort_by_due_date_then_code_within_category(client):
    crate(client, "BX-N2"); crate(client, "BX-N1")                                # 从未检查按箱号
    crate(client, "BX-B"); inspected_days_ago(client, "BX-B", VALID + 5)          # 与 BX-A 同到期日
    crate(client, "BX-A"); inspected_days_ago(client, "BX-A", VALID + 5, hour=18) # 同日稍晚，到期日相同
    crate(client, "BX-C"); inspected_days_ago(client, "BX-C", VALID + 2)          # 到期日更晚
    assert [i["code"] for i in plan(client)["items"]] == ["BX-N1", "BX-N2", "BX-A", "BX-B", "BX-C"]

def test_deactivated_crates_excluded(client):
    crate(client, "BX-ACT"); inspected_days_ago(client, "BX-ACT", VALID + 3)
    gone = crate(client, "BX-GONE"); inspected_days_ago(client, "BX-GONE", VALID + 3)
    client.post(f"/api/crates/{gone['id']}/deactivate")
    body = plan(client)
    assert [i["code"] for i in body["items"]] == ["BX-ACT"]

def test_plan_query_is_read_only(client):
    crate(client, "BX-1"); inspected_days_ago(client, "BX-1", VALID + 3)
    crate(client, "BX-2")
    before = client.get("/api/crates").json()
    plan(client)
    assert client.get("/api/crates").json() == before

def test_inspection_event_updates_last_inspected_and_exits_plan(client):
    crate(client, "BX-1"); inspected_days_ago(client, "BX-1", VALID + 4)
    assert [i["code"] for i in plan(client)["items"]] == ["BX-1"]
    at = datetime.combine(BASE, datetime.min.time()).replace(hour=10)
    inspect_at(client, "BX-1", at, no="EV-RECHECK-1")
    c = [x for x in client.get("/api/crates").json() if x["code"] == "BX-1"][0]
    assert c["last_inspected_at"].startswith(at.isoformat())
    assert plan(client)["items"] == []                       # 退出当前 7 天计划
    wider = plan(client, days=VALID)["items"]                # 窗口放大到整个有效期则重新出现
    assert [(i["code"], i["category"], i["days_remaining"]) for i in wider] == [("BX-1", "due_soon", VALID)]

def test_invalid_params_return_422(client):
    for bad in ("base_date=2026-13-40", "base_date=not-a-date", "days_ahead=-1", "days_ahead=1.5", "days_ahead=366", "days_ahead=abc"):
        assert client.get(f"/api/inspection-plan?{bad}").status_code == 422, bad

def test_defaults_use_today_and_seven_days(client):
    crate(client, "BX-1")
    body = client.get("/api/inspection-plan").json()
    assert body["base_date"] == date.today().isoformat() and body["days_ahead"] == 7
    assert [i["code"] for i in body["items"]] == ["BX-1"]
