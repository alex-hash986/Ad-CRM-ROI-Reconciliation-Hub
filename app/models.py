from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ExactDecimal(TypeDecorator):
    """Store finite decimals losslessly on both SQLite and PostgreSQL."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class ExactCount(TypeDecorator):
    """Keep CSV integer counts beyond the database integer range."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return str(value)

    def process_result_value(self, value, dialect):
        return int(value)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="completed")
    created_at: Mapped[str] = mapped_column(String(40), default=lambda: datetime.now(UTC).isoformat())
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    files: Mapped[list] = mapped_column(JSON)
    options: Mapped[dict] = mapped_column(JSON)
    kpi: Mapped[dict] = mapped_column(JSON)
    campaigns: Mapped[list["CampaignMetric"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="CampaignMetric.id"
    )
    quarantine: Mapped[list["QuarantineRow"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="QuarantineRow.id"
    )


class CampaignMetric(Base):
    __tablename__ = "campaign_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), index=True)
    campaign: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(10))
    period: Mapped[str] = mapped_column(String(10))
    spend_rub: Mapped[Decimal] = mapped_column(ExactDecimal)
    clicks: Mapped[int] = mapped_column(ExactCount)
    leads: Mapped[int] = mapped_column(ExactCount)
    qualified_leads: Mapped[int] = mapped_column(ExactCount)
    deals: Mapped[int] = mapped_column(ExactCount)
    won_deals: Mapped[int] = mapped_column(ExactCount)
    revenue_rub: Mapped[Decimal] = mapped_column(ExactDecimal)
    gross_margin_rub: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    cpc: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    cr1: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    cr2: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    cpl: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    cac: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    drr: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    roas: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    romi: Mapped[Decimal | None] = mapped_column(ExactDecimal)
    undefined: Mapped[dict] = mapped_column(JSON)
    audit_status: Mapped[str] = mapped_column(String(10))
    verdict: Mapped[str] = mapped_column(Text)
    changes: Mapped[dict | None] = mapped_column(JSON)


class QuarantineRow(Base):
    __tablename__ = "quarantine_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(10))
    line: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON)
