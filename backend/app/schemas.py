from __future__ import annotations
from datetime import datetime
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

CleaningStatus = Literal["clean", "dirty", "unknown"]
EventType = Literal["inbound", "issue", "return", "wash", "inspect", "isolate"]
IssueStatus = Literal["pending", "confirmed", "false_positive", "closed"]

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

class CrateOut(CrateBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    active: bool
    isolated: bool
    last_inspected_at: Optional[datetime]
    created_at: datetime

class EventCreate(BaseModel):
    event_no: str = Field(min_length=1, max_length=60)
    crate_code: str = Field(min_length=1)
    event_type: EventType
    occurred_at: datetime
    operator: str = Field(min_length=1, max_length=80)
    description: str = ""

class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_no: str
    crate_id: int
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

class DashboardOut(BaseModel):
    total_crates: int
    active_crates: int
    clean_crates: int
    pending_issues: int
    isolated_crates: int
