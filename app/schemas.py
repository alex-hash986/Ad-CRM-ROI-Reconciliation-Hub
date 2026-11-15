from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

Mode = Literal["period_compare", "ad_crm_sync"]
AuditStatus = Literal["critical", "warning", "scale", "normal"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Threshold = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=8, allow_inf_nan=False)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunOptions(Schema):
    name: Name
    target_roas: Threshold
    max_cpl: Threshold
    target_drr: Threshold


class Period(Schema):
    start: date | None
    end: date | None


class FileInfo(Schema):
    role: Literal["base", "comparison", "ad", "crm"]
    filename: str
    encoding: str
    delimiter: str
    period: Period
    accepted: int
    excluded: int
    records: int


class Metrics(Schema):
    spend_rub: Decimal = Decimal(0)
    clicks: int = 0
    leads: int = 0
    qualified_leads: int = 0
    deals: int = 0
    won_deals: int = 0
    revenue_rub: Decimal = Decimal(0)
    gross_margin_rub: Decimal | None = None
    cpc: Decimal | None = None
    cr1: Decimal | None = None
    cr2: Decimal | None = None
    cpl: Decimal | None = None
    cac: Decimal | None = None
    drr: Decimal | None = None
    roas: Decimal | None = None
    romi: Decimal | None = None
    undefined: dict[str, str] = Field(default_factory=dict)


class MetricChange(Schema):
    absolute: Decimal | None = None
    relative_pct: Decimal | None = None
    absolute_unit: Literal["percentage_points"] | None = None
    absolute_reason: str | None = None
    relative_reason: str | None = None


class PeriodKPI(Schema):
    base: Metrics
    comparison: Metrics | None
    changes: dict[str, MetricChange] | None


class UTMKey(Schema):
    source: str
    campaign: str


class SyncKPI(Schema):
    total: Metrics
    ad_without_crm: list[UTMKey]
    crm_without_ad: list[UTMKey]
    lead_gap: int


class CampaignResult(Metrics):
    campaign: str
    source: str | None
    scope: Literal["campaign", "source"]
    period: Literal["base", "comparison", "sync"]
    audit_status: AuditStatus
    verdict: str
    changes: dict[str, MetricChange] | None = None


class Quarantined(Schema):
    filename: str
    role: Literal["base", "comparison", "ad", "crm"]
    line: int
    raw: dict[str, JsonValue]
    reasons: list[str]


class RunSummary(Schema):
    id: int
    name: str
    mode: Mode
    status: Literal["completed"]
    created_at: str
    is_demo: bool
    files: list[FileInfo]
    options: RunOptions
    kpi: PeriodKPI | SyncKPI


class RunDetail(RunSummary):
    campaigns: list[CampaignResult]
    quarantine: list[Quarantined]


class Health(Schema):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable"]
