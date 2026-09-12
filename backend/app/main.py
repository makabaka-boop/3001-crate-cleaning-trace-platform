from __future__ import annotations
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload
from .database import Base, engine, get_db
from .models import Crate, Event, Issue
from .schemas import (EVENT_NO_MAX_LENGTH, BackupCodeBind, BatchEventResult, CrateCreate, CrateOut, CrateUpdate, DashboardOut, EventBatchCreate, EventBatchOut,
                      EventCreate, EventOut, InspectionPlanItem, InspectionPlanOut, IssueOut, IssueUpdate)

INSPECTION_VALID_DAYS = int(os.getenv("INSPECTION_VALID_DAYS", "30"))

# 已确认问题的整改建议事件类型：未清洗领用建议清洗；检查过期或隔离领用建议检查
RECTIFICATION_EVENT_TYPES = {
    "reuse_without_wash": "wash",
    "expired_inspection": "inspect",
    "use_while_isolated": "inspect",
}

def ensure_schema():
    """建表并对旧库做增量迁移：补齐新增可空列，历史数据保留且新增字段为空。"""
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    if "issues" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("issues")}
        if "rectification_event_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE issues ADD COLUMN rectification_event_id INTEGER"))
    if "crates" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("crates")}
        if "backup_code" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE crates ADD COLUMN backup_code VARCHAR(50)"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    yield

app = FastAPI(title="周转箱清洁追溯平台 API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
def health(): return {"status": "ok"}

def resolve_crate(db, code):
    """主编号或备用编号解析为同一箱体。绑定与新增箱体时保证编号在主、备全集中唯一，因此至多命中一条。"""
    return db.scalar(select(Crate).where((Crate.code == code) | (Crate.backup_code == code)))

def occupied_409(code):
    return HTTPException(409, f"编号已被占用: {code}")

@app.get("/api/crates", response_model=list[CrateOut])
def list_crates(code: Optional[str] = None, location: Optional[str] = None, cleaning_status: Optional[str] = None, active: Optional[bool] = None, db: Session = Depends(get_db)):
    q = select(Crate).order_by(Crate.code)
    if code: q = q.where(Crate.code.contains(code) | Crate.backup_code.contains(code))
    if location: q = q.where(Crate.location.contains(location))
    if cleaning_status: q = q.where(Crate.cleaning_status == cleaning_status)
    if active is not None: q = q.where(Crate.active == active)
    return db.scalars(q).all()

@app.post("/api/crates", response_model=CrateOut, status_code=201)
def create_crate(data: CrateCreate, db: Session = Depends(get_db)):
    # 新箱主编号不得撞上已绑定的备用编号，否则登记解析会产生歧义
    if db.scalar(select(Crate).where(Crate.backup_code == data.code)):
        raise occupied_409(data.code)
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

@app.post("/api/crates/{crate_id}/backup-code", response_model=CrateOut)
def bind_backup_code(crate_id: int, data: BackupCodeBind, db: Session = Depends(get_db)):
    """为箱体绑定可选备用编号；编号在主、备编号全集中必须唯一，冲突返回占用提示。"""
    crate = db.get(Crate, crate_id)
    if not crate: raise HTTPException(404, "周转箱不存在")
    code = data.backup_code
    owner = resolve_crate(db, code)
    if owner and (owner.id != crate.id or crate.code == code):
        raise occupied_409(code)
    crate.backup_code = code
    try: db.commit()
    except IntegrityError:
        # 并发绑定同一备用编号：唯一约束兜底，后提交方收到占用提示
        db.rollback(); raise occupied_409(code)
    db.refresh(crate); return crate

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
    crate = resolve_crate(db, data.crate_code)
    if not crate: raise HTTPException(404, "周转箱编号不存在")
    if not crate.active: raise HTTPException(409, "已停用周转箱不能登记事件")
    issue = None
    if data.issue_id is not None:
        issue = db.get(Issue, data.issue_id)
        if not issue: raise HTTPException(404, "问题不存在")
        if issue.status != "confirmed": raise HTTPException(409, "问题尚未确认，不能登记整改")
        if issue.crate_id != crate.id: raise HTTPException(409, "整改事件的周转箱与问题所属箱体不一致")
        expected = RECTIFICATION_EVENT_TYPES.get(issue.issue_type)
        if expected != data.event_type: raise HTTPException(409, "事件类型与该问题的整改建议不符")
    event = Event(crate=crate, **data.model_dump(exclude={"crate_code", "issue_id"}))
    db.add(event)
    try: db.flush()
    except IntegrityError:
        db.rollback(); raise HTTPException(409, "事件编号已存在")
    apply_event(db, crate, event)
    if issue is not None:
        # 与状态推导、事件落库同一事务：关联整改事件并关闭原问题
        issue.rectification_event_id = event.id
        issue.status = "closed"
    db.commit(); db.refresh(event); return event

@app.post("/api/events/batch", response_model=EventBatchOut, status_code=201)
def create_events_batch(data: EventBatchCreate, db: Session = Depends(get_db)):
    # 主编号与备用编号都解析到同一箱体；按解析后的箱体去重，同一箱体不得以两个编号进入同一批次
    crates: list[Crate] = []
    seen: dict[int, str] = {}
    for code in data.crate_codes:
        crate = resolve_crate(db, code)
        if not crate: raise HTTPException(404, f"周转箱编号不存在: {code}")
        if crate.id in seen:
            raise HTTPException(409, f"批次内周转箱编号重复: {crate.code}（{seen[crate.id]} 与 {code} 解析为同一箱体）")
        seen[crate.id] = code
        if not crate.active: raise HTTPException(409, f"周转箱已停用，不能登记事件: {crate.code}")
        crates.append(crate)
    # 事件编号按“批次编号-主编号”生成，响应与列表始终返回主编号
    event_nos = [f"{data.batch_no}-{crate.code}" for crate in crates]
    for no in event_nos:
        if len(no) > EVENT_NO_MAX_LENGTH: raise HTTPException(422, f"事件编号超长（批次编号+连接符+箱号不超过{EVENT_NO_MAX_LENGTH}字符）: {no}")
    existing = set(db.scalars(select(Event.event_no).where(Event.event_no.in_(event_nos))).all())
    if existing:
        raise conflict_409(data.batch_no, sorted(existing)[0])
    results = []
    try:
        for crate, event_no in zip(crates, event_nos):
            event = Event(event_no=event_no, crate=crate, event_type=data.event_type,
                          occurred_at=data.occurred_at, operator=data.operator, description=data.description)
            db.add(event); db.flush()
            apply_event(db, crate, event)
            results.append(BatchEventResult(crate_code=crate.code, event_no=event_no, event=EventOut.model_validate(event)))
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
    q = select(Event).join(Crate).options(selectinload(Event.crate)).order_by(Event.occurred_at.desc())
    # 主编号或已绑定备用编号命中同一箱体即返回，记录仍以主编号展示
    if crate_code: q = q.where(Crate.code.contains(crate_code) | Crate.backup_code.contains(crate_code))
    if event_type: q = q.where(Event.event_type == event_type)
    return db.scalars(q).all()

# 同类内的稳定排序：先到期日（从未检查无到期日，仅按箱号），再箱号
PLAN_CATEGORY_ORDER = {"never_inspected": 0, "overdue": 1, "due_soon": 2}

@app.get("/api/inspection-plan", response_model=InspectionPlanOut)
def inspection_plan(base_date: Optional[date] = None, days_ahead: int = Query(7, ge=0, le=365), db: Session = Depends(get_db)):
    """只读检查计划：按基准日期与配置的有效天数计算到期日、剩余天数与分类，不写回箱体状态。"""
    base = base_date or date.today()
    items = []
    for c in db.scalars(select(Crate).where(Crate.active == True)).all():  # 停用箱不进入计划
        if c.last_inspected_at is None:
            items.append(InspectionPlanItem(crate_id=c.id, code=c.code, name=c.name, location=c.location,
                                            last_inspected_at=None, due_date=None, days_remaining=None, category="never_inspected"))
            continue
        due = (c.last_inspected_at + timedelta(days=INSPECTION_VALID_DAYS)).date()
        remaining = (due - base).days
        if remaining < 0: category = "overdue"
        elif remaining <= days_ahead: category = "due_soon"
        else: continue  # 到期日超出未来窗口，暂不进入计划
        items.append(InspectionPlanItem(crate_id=c.id, code=c.code, name=c.name, location=c.location,
                                        last_inspected_at=c.last_inspected_at, due_date=due, days_remaining=remaining, category=category))
    items.sort(key=lambda x: (PLAN_CATEGORY_ORDER[x.category], x.due_date or date.min, x.code))
    return InspectionPlanOut(base_date=base, days_ahead=days_ahead, valid_days=INSPECTION_VALID_DAYS, total=len(items), items=items)

def issue_dict(i):
    return {"id": i.id, "crate_id": i.crate_id, "crate_code": i.crate.code, "crate_name": i.crate.name, "issue_type": i.issue_type, "occurred_at": i.occurred_at, "reason": i.reason, "status": i.status, "resolution_note": i.resolution_note, "rectification_event_no": i.rectification_event.event_no if i.rectification_event_id else None}

@app.get("/api/issues", response_model=list[IssueOut])
def list_issues(crate_code: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    q = select(Issue).join(Crate).order_by(Issue.occurred_at.desc())
    # 问题归属原箱体：按备用编号筛选同样命中该箱体名下的风险记录
    if crate_code: q = q.where(Crate.code.contains(crate_code) | Crate.backup_code.contains(crate_code))
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
