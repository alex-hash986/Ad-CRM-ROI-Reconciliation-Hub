from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import DEFAULT_MAX_CPL, DEFAULT_TARGET_DRR, DEFAULT_TARGET_ROAS, MAX_UPLOAD_BYTES
from app.database import lock_demo
from app.models import ReconciliationRun
from app.schemas import Health, RunDetail, RunOptions, RunSummary
from app.services.csv_parser import CsvInputError
from app.services.demo_seed import seed_demo
from app.services.exporter import export_csv, export_markdown, export_xlsx
from app.services.runs import create_run, detail, summary

router = APIRouter(prefix="/api")


def get_session(request: Request):
    with request.app.state.sessions() as session:
        yield session


Db = Annotated[Session, Depends(get_session)]
Upload = Annotated[UploadFile, File()]


def options(
    name: Annotated[str, Form()] = "Untitled reconciliation",
    target_roas: Annotated[str, Form()] = str(DEFAULT_TARGET_ROAS),
    max_cpl: Annotated[str, Form()] = str(DEFAULT_MAX_CPL),
    target_drr: Annotated[str, Form()] = str(DEFAULT_TARGET_DRR),
) -> RunOptions:
    values = {}
    for field, value in (("target_roas", target_roas), ("max_cpl", max_cpl), ("target_drr", target_drr)):
        try:
            values[field] = Decimal(value) if len(value) <= 64 else Decimal("NaN")
        except InvalidOperation:
            raise HTTPException(422, f"{field} — ожидается десятичное число") from None
    try:
        return RunOptions(name=name, **values)
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_url=False, include_context=False, include_input=False)) from error


Options = Annotated[RunOptions, Depends(options)]


def uploaded(file: UploadFile, role: str):
    body = file.file.read(MAX_UPLOAD_BYTES + 1)
    name = (file.filename or role).replace("\\", "/").rsplit("/", 1)[-1] or role
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name} — размер превышает 10 МиБ")
    return role, name, body


def save_upload(session, mode, run_options, inputs):
    try:
        run = create_run(session, mode, run_options, inputs)
        result = detail(run)
        session.commit()
        return result
    except CsvInputError as error:
        raise HTTPException(422, str(error)) from error


def get_run(session, run_id):
    run = session.get(ReconciliationRun, run_id)
    if run is None:
        raise HTTPException(404, "Запуск не найден")
    return run


@router.get("/health", response_model=Health)
def health(session: Db, response: Response):
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = 503
        return Health(status="unavailable", database="unavailable")
    return Health(status="ok", database="ok")


@router.get("/runs", response_model=list[RunSummary])
def list_runs(session: Db):
    runs = session.scalars(select(ReconciliationRun).order_by(ReconciliationRun.id.desc()))
    return [summary(run) for run in runs]


@router.get("/runs/{run_id}", response_model=RunDetail)
def run_detail(run_id: int, session: Db):
    return detail(get_run(session, run_id))


@router.post("/reconciliations/period-compare", response_model=RunDetail, status_code=201)
def upload_period(session: Db, run_options: Options, base_file: Upload,
                  comparison_file: Annotated[UploadFile | None, File()] = None):
    inputs = [uploaded(base_file, "base")]
    if comparison_file is not None:
        inputs.append(uploaded(comparison_file, "comparison"))
    return save_upload(session, "period_compare", run_options, inputs)


@router.post("/reconciliations/ad-crm-sync", response_model=RunDetail, status_code=201)
def upload_sync(session: Db, run_options: Options, ad_file: Upload, crm_file: Upload):
    inputs = [uploaded(ad_file, "ad"), uploaded(crm_file, "crm")]
    return save_upload(session, "ad_crm_sync", run_options, inputs)


@router.post("/demo/reset", response_model=list[RunSummary])
def reset_demo(session: Db):
    lock_demo(session)
    demos = [summary(run) for run in seed_demo(session, reset=True)]
    session.commit()
    return demos


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int, session: Db):
    session.delete(get_run(session, run_id))
    session.commit()
    return Response(status_code=204)




def download(body, filename, media_type):
    return Response(body, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })


@router.get("/runs/{run_id}/export/xlsx", response_class=Response)
def xlsx_report(run_id: int, session: Db):
    return download(
        export_xlsx(detail(get_run(session, run_id))), f"reconciliation_run_{run_id}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/runs/{run_id}/export/csv", response_class=Response)
def csv_report(run_id: int, session: Db, kind: Literal["campaigns", "quarantine"]):
    return download(export_csv(detail(get_run(session, run_id)), kind), f"{kind}.csv", "text/csv")


@router.get("/runs/{run_id}/export/markdown", response_class=Response)
def markdown_report(run_id: int, session: Db):
    return download(
        export_markdown(detail(get_run(session, run_id))), f"reconciliation_run_{run_id}.md", "text/markdown",
    )
