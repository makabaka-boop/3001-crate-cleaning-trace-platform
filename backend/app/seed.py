from datetime import datetime, timedelta
from .database import Base, SessionLocal, engine
from .models import Crate, Event, Issue

def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    if db.query(Crate).count(): db.close(); return
    now = datetime.utcnow()
    crates = [
        Crate(code="BX-001", name="蓝色食品箱", location="成品仓", cleaning_status="clean", last_inspected_at=now-timedelta(days=5)),
        Crate(code="BX-002", name="原料周转箱", location="待清洗区", cleaning_status="dirty", last_inspected_at=now-timedelta(days=45)),
        Crate(code="BX-003", name="红色隔离箱", location="隔离区", cleaning_status="clean", isolated=True, last_inspected_at=now-timedelta(days=2)),
    ]
    db.add_all(crates); db.flush()
    events = [
        Event(event_no="EV-1001", crate_id=crates[0].id, event_type="wash", occurred_at=now-timedelta(days=1), operator="张师傅", description="例行清洗"),
        Event(event_no="EV-1002", crate_id=crates[1].id, event_type="issue", occurred_at=now, operator="李工", description="生产线领用"),
    ]
    db.add_all(events); db.flush()
    db.add_all([
        Issue(crate_id=crates[1].id, event_id=events[1].id, issue_type="reuse_without_wash", occurred_at=now, reason="周转箱未清洗即再次领用"),
        Issue(crate_id=crates[1].id, event_id=events[1].id, issue_type="expired_inspection", occurred_at=now, reason="最近检查已超过30天有效期"),
    ])
    db.commit(); db.close()

if __name__ == "__main__": seed()
