from __future__ import annotations
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from .database import Base, engine, get_db
from .models import Crate, Event, Issue
from .schemas import (BatchEventResult, CrateCreate, CrateOut, CrateUpdate, DashboardOut, EventBatchCreate, EventBatchOut,
                      EventCreate, EventOut, IssueOut, IssueUpdate)

INSPECTION_VALID_DAYS = int(os.getenv("INSPECTION_VALID_DAYS", "30"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield

app = FastAPI(title="周转箱清洁追溯平台 API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
def health(): return {"status": "ok"}

@app.get("/api/crates", response_model=list[CrateOut])
def list_crates(code: Optional[str] = None, location: Optional[str] = None, cleaning_status: Optional[str] = None, active: Optional[bool] = None, db: Session = Depends(get_db)):
    q = select(Crate).order_by(Crate.code)
    if code: q = q.where(Crate.code.contains(code))
    if location: q = q.where(Crate.location.contains(location))
    if cleaning_status: q = q.where(Crate.cleaning_status == cleaning_status)
    if active is not None: q = q.where(Crate.active == active)
    return db.scalars(q).all()

@app.post("/api/crates", response_model=CrateOut, status_code=201)
def create_crate(data: CrateCreate, db: Session = Depends(get_db)):
    crate = Crate(**data.model_dump())
    db.add(crate)
    try: db.commit()
    except IntegrityError:
        db.rollback(); raise HTTPException(409, "周转箱编号已存在")
    db.refresh(crate); return crate

@app.put("/api/crates/{crate_id}", response_model=CrateOut)
def update_crate(crate_id: int, data: CrateUpdate, db: Session = Depends(get_db)):
    crate = db.get(Crate, crate_id)
    if not crate: raise HTTPException(404, "周转箱不存在")
    for k, v in data.model_dump(exclude_unset=True).items(): setattr(crate, k, v)
    db.commit(); db.refresh(crate); return crate

@app.post("/api/crates/{crate_id}/deactivate", response_model=CrateOut)
def deactivate_crate(crate_id: int, db: Session = Depends(get_db)):
    crate = db.get(Crate, crate_id)
    if not crate: raise HTTPException(404, "周转箱不存在")
    crate.active = False; db.commit(); db.refresh(crate); return crate

def add_issue(db, crate, event, issue_type, reason):
    db.add(Issue(crate_id=crate.id, event_id=event.id, issue_type=issue_type, occurred_at=event.occurred_at, reason=reason))

def conflict_409(batch_no, event_no):
    return HTTPException(409, f"事件编号已存在: {event_no}（周转箱 {event_no[len(batch_no) + 1:]}）")

def apply_event(db, crate, event):
    """单笔与批量登记共用的状态推导与风险识别。"""
    if event.event_type == "issue":
        if crate.cleaning_status != "clean": add_issue(db, crate, event, "reuse_without_wash", "周转箱未清洗即再次领用")
        if crate.isolated: add_issue(db, crate, event, "use_while_isolated", "周转箱处于隔离状态仍被领用")
        cutoff = event.occurred_at - timedelta(days=INSPECTION_VALID_DAYS)
        if not crate.last_inspected_at or crate.last_inspected_at < cutoff:
            add_issue(db, crate, event, "expired_inspection", f"最近检查已超过{INSPECTION_VALID_DAYS}天有效期")
        crate.cleaning_status = "dirty"
        crate.location = "使用中"
    elif event.event_type == "wash": crate.cleaning_status = "clean"; crate.location = "清洗区"
    elif event.event_type == "inspect": crate.last_inspected_at = event.occurred_at; crate.isolated = False
    elif event.event_type == "isolate": crate.isolated = True; crate.location = "隔离区"
    elif event.event_type == "return": crate.cleaning_status = "dirty"; crate.location = "待清洗区"
    elif event.event_type == "inbound": crate.location = "仓库"

@app.post("/api/events", response_model=EventOut, status_code=201)
def create_event(data: EventCreate, db: Session = Depends(get_db)):
    crate = db.scalar(select(Crate).where(Crate.code == data.crate_code))
    if not crate: raise HTTPException(404, "周转箱编号不存在")
    if not crate.active: raise HTTPException(409, "已停用周转箱不能登记事件")
    event = Event(crate_id=crate.id, **data.model_dump(exclude={"crate_code"}))
    db.add(event)
    try: db.flush()
    except IntegrityError:
        db.rollback(); raise HTTPException(409, "事件编号已存在")
    apply_event(db, crate, event)
    db.commit(); db.refresh(event); return event

@app.post("/api/events/batch", response_model=EventBatchOut, status_code=201)
def create_events_batch(data: EventBatchCreate, db: Session = Depends(get_db)):
    seen: set[str] = set()
    for code in data.crate_codes:
        if code in seen:
            raise HTTPException(409, f"批次内周转箱编号重复: {code}")
        seen.add(code)
    crates: dict[str, Crate] = {}
    for code in data.crate_codes:
        crate = db.scalar(select(Crate).where(Crate.code == code))
        if not crate: raise HTTPException(404, f"周转箱编号不存在: {code}")
        if not crate.active: raise HTTPException(409, f"周转箱已停用，不能登记事件: {code}")
        crates[code] = crate
    event_nos = [f"{data.batch_no}-{code}" for code in data.crate_codes]
    for no in event_nos:
        if len(no) > 60: raise HTTPException(422, f"事件编号超长（批次编号+箱号不超过60字符）: {no}")
    existing = set(db.scalars(select(Event.event_no).where(Event.event_no.in_(event_nos))).all())
    if existing:
        raise conflict_409(data.batch_no, sorted(existing)[0])
    results = []
    try:
        for code, event_no in zip(data.crate_codes, event_nos):
            crate = crates[code]
            event = Event(event_no=event_no, crate_id=crate.id, event_type=data.event_type,
                          occurred_at=data.occurred_at, operator=data.operator, description=data.description)
            db.add(event); db.flush()
            apply_event(db, crate, event)
            results.append(BatchEventResult(crate_code=code, event_no=event_no, event=EventOut.model_validate(event)))
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        # 同一批次被并发提交：预检通过后对方抢先落库。整批回滚并指出具体冲突箱号
        db.rollback()
        if isinstance(exc, OperationalError) and "locked" not in str(exc).lower(): raise
        existing = set(db.scalars(select(Event.event_no).where(Event.event_no.in_(event_nos))).all())
        conflict = next((no for no in event_nos if no in existing), None)
        if conflict: raise conflict_409(data.batch_no, conflict)
        if isinstance(exc, IntegrityError): raise conflict_409(data.batch_no, event_nos[0])
        raise HTTPException(409, "提交与其他操作冲突，请刷新后重试")
    return EventBatchOut(batch_no=data.batch_no, results=results)

@app.get("/api/events", response_model=list[EventOut])
def list_events(crate_code: Optional[str] = None, event_type: Optional[str] = None, db: Session = Depends(get_db)):
    q = select(Event).join(Crate).order_by(Event.occurred_at.desc())
    if crate_code: q = q.where(Crate.code.contains(crate_code))
    if event_type: q = q.where(Event.event_type == event_type)
    return db.scalars(q).all()

def issue_dict(i):
    return {"id": i.id, "crate_id": i.crate_id, "crate_code": i.crate.code, "crate_name": i.crate.name, "issue_type": i.issue_type, "occurred_at": i.occurred_at, "reason": i.reason, "status": i.status, "resolution_note": i.resolution_note}

@app.get("/api/issues", response_model=list[IssueOut])
def list_issues(crate_code: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    q = select(Issue).join(Crate).order_by(Issue.occurred_at.desc())
    if crate_code: q = q.where(Crate.code.contains(crate_code))
    if status: q = q.where(Issue.status == status)
    return [issue_dict(i) for i in db.scalars(q).all()]

@app.patch("/api/issues/{issue_id}", response_model=IssueOut)
def update_issue(issue_id: int, data: IssueUpdate, db: Session = Depends(get_db)):
    issue = db.get(Issue, issue_id)
    if not issue: raise HTTPException(404, "问题不存在")
    issue.status = data.status; issue.resolution_note = data.resolution_note
    db.commit(); db.refresh(issue); return issue_dict(issue)

@app.get("/api/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)):
    count = lambda *where: db.scalar(select(func.count()).select_from(Crate).where(*where)) or 0
    return {"total_crates": count(), "active_crates": count(Crate.active == True), "clean_crates": count(Crate.cleaning_status == "clean"), "isolated_crates": count(Crate.isolated == True), "pending_issues": db.scalar(select(func.count()).select_from(Issue).where(Issue.status == "pending")) or 0}
