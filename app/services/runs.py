from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from app.models import CampaignMetric, QuarantineRow, ReconciliationRun
from app.schemas import CampaignResult, FileInfo, MetricChange, PeriodKPI, Quarantined, RunDetail, RunOptions, RunSummary, SyncKPI
from app.services.csv_parser import parse_csv
from app.services.reconciliation_engine import ad_crm_sync, period_compare

FILES = TypeAdapter(list[FileInfo])
CHANGES = TypeAdapter(dict[str, MetricChange])


def create_run(session: Session, mode, options: RunOptions, inputs, *, demo=False) -> ReconciliationRun:
    files = [parse_csv(filename, body, role) for role, filename, body in inputs]
    if mode == "period_compare":
        kpi, campaigns = period_compare(files[0], files[1] if len(files) == 2 else None, options)
    else:
        kpi, campaigns = ad_crm_sync(*files, options)

    run = ReconciliationRun(
        name=options.name, mode=mode, options=options.model_dump(mode="json"), is_demo=demo,
        files=[file.info.model_dump(mode="json") for file in files], kpi=kpi.model_dump(mode="json"),
        campaigns=[
            CampaignMetric(**campaign.model_dump(exclude={"changes"}),
                           changes=CHANGES.dump_python(campaign.changes, mode="json") if campaign.changes else None)
            for campaign in campaigns
        ],
        quarantine=[QuarantineRow(**row.model_dump()) for file in files for row in file.quarantine],
    )
    session.add(run)
    session.flush()
    return run


def summary(run: ReconciliationRun) -> RunSummary:
    kpi_type = PeriodKPI if run.mode == "period_compare" else SyncKPI
    return RunSummary(
        id=run.id, name=run.name, mode=run.mode, status=run.status, created_at=run.created_at, is_demo=run.is_demo,
        files=FILES.validate_python(run.files, strict=False), options=RunOptions.model_validate(run.options, strict=False),
        kpi=kpi_type.model_validate(run.kpi, strict=False),
    )


def detail(run: ReconciliationRun) -> RunDetail:
    return RunDetail(
        **vars(summary(run)),
        campaigns=[
            CampaignResult(
                **{key: getattr(row, key) for key in CampaignResult.model_fields if key != "changes"},
                changes=CHANGES.validate_python(row.changes, strict=False) if row.changes else None,
            )
            for row in run.campaigns
        ],
        quarantine=[Quarantined.model_validate(row, from_attributes=True) for row in run.quarantine],
    )
