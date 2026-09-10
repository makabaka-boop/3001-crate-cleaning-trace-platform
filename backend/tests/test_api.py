import os
os.environ["DATABASE_URL"] = "sqlite:///./test_crate_trace.db"
from datetime import datetime
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

def crate(client, code="BX-TEST", status="dirty"):
    r = client.post("/api/crates", json={"code":code,"name":"测试箱","location":"仓库","cleaning_status":status,"notes":""})
    assert r.status_code == 201
    return r.json()

def test_crate_crud_and_validation(client):
    c = crate(client)
    assert client.post("/api/crates", json={"code":"BX-TEST","name":"重号","location":"仓库"}).status_code == 409
    assert client.post("/api/crates", json={"code":"非法 箱号","name":"测试","location":"仓库"}).status_code == 422
    assert client.put(f"/api/crates/{c['id']}", json={"location":"一号线"}).json()["location"] == "一号线"
    assert client.post(f"/api/crates/{c['id']}/deactivate").json()["active"] is False

def test_event_issues_and_duplicate(client):
    crate(client)
    payload={"event_no":"EV-1","crate_code":"BX-TEST","event_type":"issue","occurred_at":datetime.utcnow().isoformat(),"operator":"王工","description":"领用"}
    assert client.post("/api/events", json=payload).status_code == 201
    issues=client.get("/api/issues").json()
    assert {x["issue_type"] for x in issues} == {"reuse_without_wash", "expired_inspection"}
    assert client.post("/api/events", json=payload).status_code == 409
    updated=client.patch(f"/api/issues/{issues[0]['id']}", json={"status":"confirmed","resolution_note":"已核实"})
    assert updated.json()["status"] == "confirmed"

def test_clean_and_recent_inspection_avoids_issues(client):
    crate(client, status="clean")
    now=datetime.utcnow()
    for no, typ in [("EV-I","inspect"),("EV-U","issue")]:
        r=client.post("/api/events", json={"event_no":no,"crate_code":"BX-TEST","event_type":typ,"occurred_at":now.isoformat(),"operator":"王工","description":""})
        assert r.status_code == 201
    assert client.get("/api/issues").json() == []

def test_unknown_crate_and_missing_fields(client):
    assert client.post("/api/events", json={"event_no":"X","crate_code":"NONE","event_type":"wash","occurred_at":datetime.utcnow().isoformat(),"operator":"A"}).status_code == 404
    assert client.post("/api/events", json={"event_no":"X"}).status_code == 422
