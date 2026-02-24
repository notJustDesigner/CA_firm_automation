from pydantic import BaseModel, Field, EmailStr
from datetime import datetime, date
from typing import Any


# ─── Auth Schemas ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


# ─── Client Schemas ───────────────────────────────────────────────────────────

class ClientBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    pan: str | None = Field(None, max_length=10, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    gstin: str | None = Field(None, max_length=15)
    email: str | None = None
    phone: str | None = Field(None, max_length=20)
    address: str | None = None
    notes: str | None = None


class ClientCreate(ClientBase):
    pass


class ClientUpdate(ClientBase):
    name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None


class ClientResponse(ClientBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── TaskResult Schemas ───────────────────────────────────────────────────────

class TaskResultBase(BaseModel):
    task_type: str = Field(..., max_length=100)
    client_id: int | None = None
    prompt: str | None = None


class TaskResultCreate(TaskResultBase):
    pass


class TaskResultResponse(TaskResultBase):
    id: int
    status: str
    model_used: str | None
    result: dict | Any | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


# ─── DueDate Schemas ──────────────────────────────────────────────────────────

class DueDateBase(BaseModel):
    compliance_type: str = Field(..., max_length=200)
    description: str | None = None
    due_date: date
    period: str | None = Field(None, max_length=50)
    client_id: int | None = None
    notes: str | None = None


class DueDateCreate(DueDateBase):
    pass


class DueDateUpdate(BaseModel):
    compliance_type: str | None = Field(None, max_length=200)
    description: str | None = None
    due_date: date | None = None
    period: str | None = None
    is_completed: bool | None = None
    notes: str | None = None


class DueDateResponse(DueDateBase):
    id: int
    is_completed: bool
    is_reminder_sent: bool
    reminder_sent_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Document Schemas ─────────────────────────────────────────────────────────

class DocumentBase(BaseModel):
    original_filename: str
    document_type: str | None = None
    financial_year: str | None = Field(None, max_length=10)
    tags: str | None = None
    notes: str | None = None
    client_id: int | None = None


class DocumentResponse(DocumentBase):
    id: int
    filename: str
    file_path: str
    mime_type: str | None
    file_size_bytes: int | None
    is_processed: bool
    processing_error: str | None
    extracted_text: str | None
    uploaded_at: datetime
    processed_at: datetime | None

    model_config = {"from_attributes": True}


# ─── Generic Schemas ──────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    db: str


class MessageResponse(BaseModel):
    message: str