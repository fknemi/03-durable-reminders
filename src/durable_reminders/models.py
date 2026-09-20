"""Pydantic request/response models and shared enums."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReminderStatus(StrEnum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL_STATUSES = {
    ReminderStatus.DELIVERED,
    ReminderStatus.CANCELLED,
    ReminderStatus.FAILED,
}


class CreateReminderRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    fire_at_local: str = Field(description="Naive local datetime, e.g. 2024-11-03T01:30:00")
    timezone: str = Field(description="IANA timezone, e.g. America/New_York")


class EditReminderRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    fire_at_local: str | None = None
    timezone: str | None = None


class DeliveryAttemptOut(BaseModel):
    attempt_number: int
    attempted_at: datetime
    outcome: str
    error: str | None = None


class ReminderOut(BaseModel):
    id: str
    content: str
    timezone: str
    local_requested: str
    scheduled_at_utc: datetime
    tz_policy: str
    status: ReminderStatus
    version: int
    delivery_key: str
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None
    delivered_at: datetime | None = None
    last_error: str | None = None
    attempts: list[DeliveryAttemptOut] = []
