from __future__ import annotations
from datetime import date, datetime
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

CleaningStatus = Literal["clean", "dirty", "unknown"]
EventType = Literal["inbound", "issue", "return", "wash", "inspect", "isolate"]
IssueStatus = Literal["pending", "confirmed", "false_positive", "closed"]
# 计划分类：从未检查优先，其次已过期，最后即将到期
PlanCategory = Literal["never_inspected", "overdue", "due_soon"]

# 周转箱编号格式：与 CrateBase.code 一致，批量登记时逐箱校验
CrateCode = Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")]
# 事件编号上限：批次编号(50) + "-" + 箱号(50)
EVENT_NO_MAX_LENGTH = 101

class CrateBase(BaseModel):
    code: CrateCode
    name: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=100)
    cleaning_status: CleaningStatus = "unknown"
    notes: str = ""

class CrateCreate(CrateBase): pass
class CrateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    location: Optional[str] = Field(None, min_length=1, max_length=100)
    cleaning_status: Optional[CleaningStatus] = None
    notes: Optional[str] = None
    active: Optional[bool] = None

class BackupCodeBind(BaseModel):
    # 备用编号与主编号同一格式约束；绑定时校验在主、备编号全集中唯一
    backup_code: CrateCode

class CrateOut(CrateBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    active: bool
    isolated: bool
    # 可空备用编号：仅追加字段，未绑定的箱体为空
    backup_code: Optional[str] = None
    last_inspected_at: Optional[datetime]
    created_at: datetime

class EventCreate(BaseModel):
    event_no: str = Field(min_length=1, max_length=60)
    crate_code: str = Field(min_length=1)
    event_type: EventType
    occurred_at: datetime
    operator: str = Field(min_length=1, max_length=80)
    description: str = ""
    # 可选：在已确认问题上“登记整改”时带上，后端校验同箱体且事件类型符合整改建议，提交后关闭该问题
    issue_id: Optional[int] = None

class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_no: str
    crate_id: int
    # 箱体主编号：备用编号登记的事件同样返回主编号
    crate_code: str
    event_type: EventType
    occurred_at: datetime
    operator: str
    description: str

class EventBatchCreate(BaseModel):
    batch_no: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    event_type: EventType
    occurred_at: datetime
    operator: str = Field(min_length=1, max_length=80)
    description: str = ""
    crate_codes: list[CrateCode] = Field(min_length=1, max_length=500)

class BatchEventResult(BaseModel):
    crate_code: str
    event_no: str
    event: EventOut

class EventBatchOut(BaseModel):
    batch_no: str
    results: list[BatchEventResult]

class IssueUpdate(BaseModel):
    status: IssueStatus
    resolution_note: str = ""

class IssueOut(BaseModel):
    id: int
    crate_id: int
    crate_code: str
    crate_name: str
    issue_type: str
    occurred_at: datetime
    reason: str
    status: IssueStatus
    resolution_note: str
    # 可空整改事件编号：仅追加字段，旧问题无整改关联时为空
    rectification_event_no: Optional[str] = None

class DashboardOut(BaseModel):
    total_crates: int
    active_crates: int
    clean_crates: int
    pending_issues: int
    isolated_crates: int

class InspectionPlanItem(BaseModel):
    crate_id: int
    code: str
    name: str
    location: str
    last_inspected_at: Optional[datetime]
    due_date: Optional[date]        # 从未检查的箱无到期日
    days_remaining: Optional[int]   # 负数表示已超期
    category: PlanCategory

class InspectionPlanOut(BaseModel):
    base_date: date
    days_ahead: int
    valid_days: int
    total: int
    items: list[InspectionPlanItem]
