"""Pydantic v2 schemas for the API — request/response models."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ListingOut(BaseModel):
    id: int
    gs_listing_id: str
    name: str
    category: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    opening_hours: Optional[str] = None
    website_url: Optional[str] = None
    is_paid: bool = False
    is_verifiable: bool = True
    last_checked: Optional[datetime] = None
    next_check: Optional[datetime] = None
    check_interval_days: Optional[float] = 7
    consecutive_unchanged: Optional[int] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FieldObs(BaseModel):
    id: int
    listing_id: int
    field: str
    value: Optional[str] = None
    is_present: bool
    source: str
    source_url: Optional[str] = None
    source_page: Optional[str] = None
    extraction_confidence: Optional[float] = None
    observed_at: Optional[datetime] = None


class VersionOut(BaseModel):
    id: int
    listing_id: int
    batch_id: Optional[int] = None
    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    intent_confidence: Optional[float] = None
    decision: Optional[str] = None
    signals: Optional[Any] = None
    reasoning: Optional[str] = None
    applied_at: Optional[datetime] = None
    applied_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    created_at: Optional[datetime] = None


class BatchCreate(BaseModel):
    phases: list[int] = Field(default=[1, 2, 3, 4, 6])
    test_mode: bool = False


class BatchStatus(BaseModel):
    id: int
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    listings_processed: int = 0
    changes_proposed: int = 0
    changes_auto_applied: int = 0
    changes_review_queue: int = 0
    changes_discarded: int = 0
    llm_calls: int = 0
    cost_eur: float = 0.0
    anomaly_flagged: bool = False
    anomaly_reason: Optional[str] = None


class ReviewDecision(BaseModel):
    reason: Optional[str] = None


class PaginatedListings(BaseModel):
    items: list[ListingOut]
    total: int


class PaginatedVersions(BaseModel):
    items: list[VersionOut]
    total: int


class PaginatedBatches(BaseModel):
    items: list[BatchStatus]
    total: int
