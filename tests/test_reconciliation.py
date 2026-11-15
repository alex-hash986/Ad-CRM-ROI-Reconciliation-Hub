import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import func, select

from app.config import SAMPLE_DATA
from app.main import create_app
from app.models import CampaignMetric, QuarantineRow
from app.schemas import RunOptions
from app.services.csv_parser import CsvInputError, parse_csv
from app.services.demo_seed import DEMOS
from app.services.reconciliation_engine import ad_crm_sync, period_compare

OPTIONS = RunOptions(name="Проверка", target_roas=Decimal(3), max_cpl=Decimal(1500), target_drr=Decimal(35))
VM_BASE, VM_NEXT = [SAMPLE_DATA / name for _, name in DEMOS[0][2]]
AD = "date,campaign,source,spend_rub,clicks,leads\n"
CRM = "date,deal_id,utm_campaign,utm_source,status,revenue_rub,cost_rub,gross_margin_rub\n"
VM_HEADER = AD.rstrip("\n") + ",revenue_rub\n"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def live_app():
    with TemporaryDirectory(prefix="reconciliation-") as directory:
        database = Path(directory, "test.db").as_posix()
        application = create_app(f"sqlite:///{database}")
        async with application.router.lifespan_context(application):
            yield application


@pytest.fixture
async def client(live_app):
    async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://test") as caller:
        yield caller


def demo_inputs():
    return [parse_csv(path.name, path.read_bytes(), role) for path, role in
            ((VM_BASE, "base"), (VM_NEXT, "comparison"))]


def company_data():
    ad = AD + "2026-10-01, Search , YANDEX ,120,40,4\n2026-10-01,orphan,vk,20,10,0\n"
    crm = CRM + (
        "2026-10-01,1,search,yandex,won,300,100,\n"
        "2026-10-01,2,SEARCH,YANDEX,in_progress,500,250,\n"
        "2026-10-01,3,search,yandex,lost,400,100,\n"
        "2026-10-01,4,referral,partner,won,100,,70\n"
    )
    return ad.encode(), crm.encode()


def test_one_original_file():
    base, _ = demo_inputs()
    kpi, campaigns = period_compare(base, None, OPTIONS)
    assert (kpi.base.spend_rub, kpi.base.clicks, kpi.base.leads, kpi.base.revenue_rub) == (
        Decimal(107500), 3340, 90, Decimal(318400),
    )
    assert kpi.comparison is kpi.changes is None
    assert (base.info.accepted, base.info.excluded) == (13, 2)
    assert [row.line for row in base.quarantine] == [12, 15]
    assert base.info.period.start == date(2026, 9, 14)
    assert base.info.period.end == date(2026, 9, 18)
    team = next(row for row in campaigns if row.campaign == "Team_Search" and row.scope == "campaign")
    assert team.audit_status == "critical"
    assert "нулевой выручке" in team.verdict


def test_two_original_files():
    base, comparison = demo_inputs()
    kpi, campaigns = period_compare(base, comparison, OPTIONS)
    assert (kpi.comparison.spend_rub, kpi.comparison.clicks, kpi.comparison.leads, kpi.comparison.revenue_rub) == (
        Decimal(115200), 3510, 95, Decimal(318400),
    )
    assert [(file.info.accepted, file.info.excluded) for file in (base, comparison)] == [(13, 2), (13, 2)]
    assert [row.line for row in comparison.quarantine] == [12, 15]
    assert kpi.changes["spend_rub"].absolute == 7700
    assert kpi.changes["revenue_rub"].relative_pct == 0
    assert kpi.changes["cr1"].absolute_unit == "percentage_points"
    team = next(row for row in campaigns if row.campaign == "Team_Search" and row.period == "comparison")
    assert team.changes["revenue_rub"].relative_pct is None
    assert team.changes["revenue_rub"].relative_reason == "Нулевое базовое значение"


def test_zero_denominators_and_absent_campaign():
    left = parse_csv("a", (VM_HEADER + "2026-01-01,A,vk,0,0,0,0\n").encode(), "base")
    right = parse_csv("b", (VM_HEADER + "2026-01-02,B,vk,30,5,0,0\n").encode(), "comparison")
    kpi, campaigns = period_compare(left, right, OPTIONS)
    assert kpi.base.roas is kpi.base.cpc is kpi.base.cpl is None
    assert kpi.changes["cpc"].absolute is None
    assert "Базовый период" in kpi.changes["cpc"].absolute_reason
    assert kpi.changes["spend_rub"].absolute == 30
    assert kpi.changes["spend_rub"].relative_pct is None
    assert all("отсутствует" in row.changes["spend_rub"].absolute_reason for row in campaigns)


def test_crm_sync():
    ad, crm = company_data()
    kpi, campaigns = ad_crm_sync(parse_csv("ad", ad, "ad"), parse_csv("crm", crm, "crm"), OPTIONS)
    search = next(row for row in campaigns if row.campaign == "search")
    assert (search.leads, search.qualified_leads, search.deals, search.won_deals) == (4, 3, 3, 1)
    assert (search.revenue_rub, search.gross_margin_rub) == (300, 200)
    assert (search.cpl, search.cac, search.drr, search.roas) == (30, 120, 40, Decimal("2.5"))
    assert search.romi.quantize(Decimal(".01")) == Decimal("66.67")
    assert search.cr2.quantize(Decimal(".01")) == Decimal("33.33")
    assert search.audit_status == "warning"
    assert kpi.total.revenue_rub == 400
    assert kpi.total.cac == 70
    assert kpi.lead_gap == 0
    assert [(item.source, item.campaign) for item in kpi.ad_without_crm] == [("vk", "orphan")]
    assert [(item.source, item.campaign) for item in kpi.crm_without_ad] == [("partner", "referral")]


def test_cp1251_aliases_structure_and_physical_lines():
    body = (
        "Дата;Название кампании;Источник;Расход (руб.);Клики;Заявки;Выручка\r\n"
        '2026-01-01;"Поиск\r\nбренда";Яндекс;12,54;4;1;99\r\n'
        "2026-01-01;Поиск;Яндекс;100;5;1;100\r\n"
        "2026-01-01;Поиск;Яндекс;-1;1.5;;NaN\r\n"
        "2026-02-30;;Яндекс;1;-1;3;5\r\n"
        '2026-01-06;"сломано\r\n'
        "2026-01-08;Поиск;Яндекс;10;5;1;10\r\n"
        "2026-01-09;Поиск;Яндекс;10\r\n"
    ).encode("cp1251")
    parsed = parse_csv("legacy.csv", body, "base")
    assert (parsed.info.encoding, parsed.info.delimiter) == ("cp1251", ";")
    assert parsed.rows[0]["spend_rub"] == Decimal("12.54")
    assert parsed.rows[0]["campaign"] == "Поиск\r\nбренда"
    assert parsed.info.accepted == 3
    assert parsed.info.excluded == 4
    assert parsed.info.period.end == date(2026, 1, 9)
    assert [row.line for row in parsed.quarantine] == [5, 6, 7, 9]
    assert len(parsed.quarantine[0].reasons) == 5
    assert parsed.quarantine[0].raw["Клики"] == "1.5"


def test_bom_tab_and_integer_validation():
    body = (VM_HEADER + "2026-01-01,A,vk,1.05,3.0,2.0,7.50\n").replace(",", "\t").encode("utf-8-sig")
    data = parse_csv("tab.csv", body, "base")
    assert (data.info.encoding, data.info.delimiter) == ("utf-8-sig", "\t")
    assert data.rows[0]["clicks"] == 3
    assert data.rows[0]["revenue_rub"] == Decimal("7.50")
    with pytest.raises(CsvInputError, match="строка 1"):
        parse_csv("invalid.csv", b"a,b,c\n1,2,3", "crm")


def test_crm_row_validation():
    crm = CRM.replace("gross_margin_rub", "gross_margin_rub,deals") + (
        "2026-01-01,1,x,vk,won,10,15,-5,1\n"
        "2026-01-01,2,x,vk,won,10,,,-1.5\n"
        "2026-01-01,3,x,vk,paid,NaN,,1,2\n"
        "2026-01-01,1,X,VK,lost,0,0,,1\n"
    )
    parsed = parse_csv("crm.csv", crm.encode(), "crm")
    assert parsed.info.accepted == 1
    assert parsed.rows[0]["gross_margin_rub"] == -5
    assert [len(row.reasons) for row in parsed.quarantine] == [3, 2, 1]


@pytest.mark.anyio
async def test_health_and_demo(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    runs = (await client.get("/api/runs")).json()
    assert len(runs) == 2
    vm = next(run for run in runs if run["mode"] == "period_compare")
    sync = next(run for run in runs if run["mode"] == "ad_crm_sync")
    assert [file["accepted"] for file in sync["files"]] == [30, 45]
    assert [file["excluded"] for file in sync["files"]] == [1, 2]
    response = await client.get(f"/api/runs/{vm['id']}")
    assert response.status_code == 200
    assert len(response.json()["quarantine"]) == 4
    assert response.json()["kpi"]["comparison"]["spend_rub"] == "115200"
    assert (await client.get("/api/runs/100000")).status_code == 404


@pytest.mark.anyio
async def test_period_api_and_demo_reset(client, live_app):
    files = {"base_file": (VM_BASE.name, VM_BASE.read_bytes()),
             "comparison_file": (VM_NEXT.name, VM_NEXT.read_bytes())}
    response = await client.post("/api/reconciliations/period-compare", files=files,
                           data={"name": "Проверка API", "target_roas": "2.1", "max_cpl": "1200"})
    assert response.status_code == 201
    run = response.json()
    assert run["options"]["target_roas"] == "2.1"
    assert run["options"]["max_cpl"] == "1200"
    assert len(run["quarantine"]) == 4
    for _ in range(2):
        assert (await client.post("/api/demo/reset")).status_code == 200
        assert len((await client.get("/api/runs")).json()) == 3
    assert (await client.get(f"/api/runs/{run['id']}")).json() == run
    assert (await client.delete(f"/api/runs/{run['id']}")).status_code == 204
    assert (await client.delete(f"/api/runs/{run['id']}")).status_code == 404
    with live_app.state.sessions() as session:
        for table in (CampaignMetric, QuarantineRow):
            assert session.scalar(select(func.count()).select_from(table).where(table.run_id == run["id"])) == 0


@pytest.mark.anyio
async def test_sync_api_and_invalid_uploads(client):
    ad, crm = company_data()
    response = await client.post("/api/reconciliations/ad-crm-sync",
                           files={"ad_file": ("ad.csv", ad), "crm_file": ("crm.csv", crm)})
    assert response.status_code == 201
    assert response.json()["kpi"]["total"]["cac"] == "70"
    period_api = "/api/reconciliations/period-compare"
    assert (await client.post(period_api, files={"base_file": ("bad.csv", b"bad")})).status_code == 422
    assert (await client.post(period_api, files={"base_file": (VM_BASE.name, VM_BASE.read_bytes())},
                       data={"target_drr": "Infinity"})).status_code == 422
    assert (await client.post(period_api)).status_code == 422
    assert len((await client.get("/api/runs")).json()) == 3


@pytest.mark.anyio
async def test_sqlite_exact_round_trip(client):
    spend = "12345678901234567890.12345678901234567890"
    row = f"2026-01-01,A,vk,{spend},1,1,0\n"
    response = await client.post("/api/reconciliations/period-compare",
                           files={"base_file": ("precise.csv", (VM_HEADER + row).encode())})
    assert response.status_code == 201
    payload = response.json()
    fetched = (await client.get(f"/api/runs/{payload['id']}")).json()
    assert fetched["kpi"]["base"]["spend_rub"] == spend
    assert fetched["campaigns"][0]["spend_rub"] == spend



@pytest.mark.parametrize(("spend", "status"), [("23", "normal"), ("23.01", "warning")])
def test_cpl_growth_boundary(spend, status):
    left = parse_csv("a", (VM_HEADER + "2026-01-01,A,vk,20,9,3,1000\n").encode(), "base")
    right = parse_csv("b", (VM_HEADER + f"2026-01-02,A,vk,{spend},9,3,1000\n").encode(), "comparison")
    options = OPTIONS.model_copy(update={"target_roas": Decimal(1000)})
    kpi, campaigns = period_compare(left, right, options)
    expected = Decimal("15") if status == "normal" else Decimal("15.05")
    assert kpi.changes["cpl"].relative_pct == expected
    assert all(row.audit_status == status for row in campaigns if row.period == "comparison")


def test_bare_quote_and_excluded_campaign_without_source():
    body = VM_HEADER + (
        '2026-01-01,A"broken,vk,1,2,3,4\n'
        "2026-01-02,MissingSource,,10,8,1,50\n"
        "2026-01-03,Accepted,vk,20,10,2,80\n"
    )
    parsed = parse_csv("quotes.csv", body.encode(), "base")
    assert [row.line for row in parsed.quarantine] == [2, 3]
    assert "кавычек" in parsed.quarantine[0].reasons[0]
    kpi, campaigns = period_compare(parsed, None, OPTIONS)
    assert kpi.base.spend_rub == 20
    missing = [row for row in campaigns if row.campaign == "MissingSource"]
    assert len(missing) == 1 and missing[0].spend_rub == 0


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["period_compare", "ad_crm_sync"])
async def test_xlsx_export(client, mode):
    run = next(item for item in (await client.get("/api/runs")).json() if item["mode"] == mode)
    response = await client.get(f"/api/runs/{run['id']}/export/xlsx")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="reconciliation_run_{run["id"]}.xlsx"'
    assert response.headers["content-type"].endswith("spreadsheetml.sheet")
    workbook = load_workbook(io.BytesIO(response.content), data_only=False)
    assert workbook.sheetnames == ["Сводка KPI", "Аудит кампаний", "Карантин данных (QA)"]
    summary, audit, qa = workbook.worksheets
    amounts = next(row for row in summary.iter_rows() if row[0].value == "Расход, ₽")
    assert "₽" in amounts[1].number_format
    assert summary["B8"].value == 0.35 and summary["B8"].number_format == "0.00%"
    assert audit["A1"].fill.fgColor.rgb[-6:] == "1E40AF"
    assert audit.freeze_panes == "B2"
    assert any(cell.fill.fgColor.rgb[-6:] == "FDE8E7" for cell in audit["E"][1:])
    assert qa["C2"].value >= 2
    expected = "Изменение, %" if mode == "period_compare" else "Сделки без расхода"
    assert any(cell.value == expected for row in summary.iter_rows() for cell in row)


@pytest.mark.anyio
@pytest.mark.parametrize("kind,field", [("campaigns", "Кампания"), ("quarantine", "Строка CSV")])
async def test_csv_export(client, kind, field):
    response = await client.get("/api/runs/1/export/csv", params={"kind": kind})
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{kind}.csv"'
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    assert rows and rows[0][field]
    assert (await client.get("/api/runs/1/export/csv?kind=unknown")).status_code == 422


@pytest.mark.anyio
async def test_markdown_export_and_unknown_run(client):
    response = await client.get("/api/runs/1/export/markdown")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="reconciliation_run_1.md"'
    assert "Управленческие выводы и план действий" in response.text
    assert "Team\\_Search" in response.text
    assert "Критические" in response.text
    assert (await client.get("/api/runs/9999/export/xlsx")).status_code == 404


@pytest.mark.anyio
async def test_exports_keep_untrusted_campaign_names_as_data(client):
    sample = VM_HEADER + "2026-01-01,=2+3,vk,10,5,1,0\n"
    result = await client.post("/api/reconciliations/period-compare",
                               files={"base_file": ("campaigns.csv", sample.encode())})
    export = f"/api/runs/{result.json()['id']}/export"
    xlsx = await client.get(export + "/xlsx")
    audit = load_workbook(io.BytesIO(xlsx.content))["Аудит кампаний"]
    assert audit["A2"].data_type == "s"
    assert audit["A2"].value == "=2+3"
    response = await client.get(export + "/csv?kind=campaigns")
    assert "\n'=2+3;" in response.content.decode("utf-8-sig")
