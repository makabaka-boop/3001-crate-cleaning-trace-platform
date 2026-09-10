from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class Crate(Base):
    __tablename__ = "crates"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(String(100))
    cleaning_status: Mapped[str] = mapped_column(String(20), default="dirty")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    isolated: Mapped[bool] = mapped_column(Boolean, default=False)
    last_inspected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    events: Mapped[list["Event"]] = relationship(back_populates="crate", cascade="all, delete-orphan")

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_no: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    crate_id: Mapped[int] = mapped_column(ForeignKey("crates.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    operator: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    crate: Mapped[Crate] = relationship(back_populates="events")

class Issue(Base):
    __tablename__ = "issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    crate_id: Mapped[int] = mapped_column(ForeignKey("crates.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    issue_type: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    crate: Mapped[Crate] = relationship()
