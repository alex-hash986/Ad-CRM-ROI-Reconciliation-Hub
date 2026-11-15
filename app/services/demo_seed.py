from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import DEFAULT_MAX_CPL, DEFAULT_TARGET_DRR, DEFAULT_TARGET_ROAS, SAMPLE_DATA
from app.models import ReconciliationRun
from app.schemas import RunOptions
from app.services.runs import create_run

DEMOS = (
    (
        "Опора — Сравнение недель (14–18 сен vs 21–25 сен 2026)",
        "period_compare",
        (
            ("base", "VMvc3_Рекламная_выгрузка_Опора.csv"),
            ("comparison", "VMvc3_Рекламная_выгрузка_Опора_21-25_сентября.csv"),
        ),
    ),
    (
        "B2B SaaS & Services — Сквозная сверка Директ/VK + amoCRM (Октябрь 2026)",
        "ad_crm_sync",
        (("ad", "ad_spend.csv"), ("crm", "crm_deals.csv")),
    ),
)


def seed_demo(session: Session, *, reset: bool = False):
    if reset:
        session.execute(delete(ReconciliationRun).where(ReconciliationRun.is_demo.is_(True)))
    elif session.scalar(select(ReconciliationRun.id).limit(1)) is not None:
        return []

    return [
        create_run(
            session, mode,
            RunOptions(
                name=name, target_roas=DEFAULT_TARGET_ROAS, max_cpl=DEFAULT_MAX_CPL,
                target_drr=DEFAULT_TARGET_DRR,
            ),
            [(role, filename, (SAMPLE_DATA / filename).read_bytes()) for role, filename in files],
            demo=True,
        )
        for name, mode, files in DEMOS
    ]
