import csv
import html
import io
import json
import re
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.schemas import PeriodKPI, RunDetail

METRICS = {
    "spend_rub": "Расход, ₽", "clicks": "Клики", "leads": "Лиды из рекламы",
    "qualified_leads": "Квалифицированные лиды CRM", "deals": "Сделки CRM",
    "won_deals": "Оплаченные сделки", "revenue_rub": "Выручка, ₽",
    "gross_margin_rub": "Валовая маржа, ₽", "cpc": "CPC, ₽", "cr1": "CR1, %",
    "cr2": "CR2, %", "cpl": "CPL, ₽", "cac": "CAC, ₽", "drr": "ДРР, %",
    "roas": "ROAS, ×", "romi": "ROMI, %",
}
PERCENT = {"cr1", "cr2", "drr", "romi"}
PERIOD_ONLY = ("spend_rub", "clicks", "leads", "revenue_rub", "cpc", "cr1", "cpl", "drr", "roas")
MONEY = {key for key, label in METRICS.items() if label.endswith("₽")}
FORMAT_MONEY = '#,##0.00 "₽"'
FORMAT_PERCENT = "0.00%"
STATUS = {
    "critical": ("Критические", "FDE8E7", "9F211C"),
    "warning": ("Требуют внимания", "FFF1D6", "795109"),
    "scale": ("Масштабирование", "DEF5E8", "16603C"),
    "normal": ("Норма", "EEF2F7", "37465A"),
}
ROLES = {"base": "Базовый период", "comparison": "Сравниваемый период", "sync": "Реклама + CRM",
         "ad": "Реклама", "crm": "CRM"}
DELIMITERS = {",": "Запятая", ";": "Точка с запятой", "\t": "Табуляция"}
QUARANTINE_HEADERS = ["Файл", "Роль", "Строка CSV", "Причины исключения", "Исходные поля"]


def metric_keys(run):
    return PERIOD_ONLY if isinstance(run.kpi, PeriodKPI) else METRICS


def metadata(run):
    return [
        ("ID запуска", run.id), ("Сверка", run.name),
        ("Режим", "Сравнение периодов" if isinstance(run.kpi, PeriodKPI) else "Реклама + CRM"),
        ("Время запуска (UTC)", run.created_at),
        ("Целевой ROAS, ×", run.options.target_roas),
        ("Макс. CPL, ₽", run.options.max_cpl),
        ("Целевой ДРР, %", run.options.target_drr),
        ("Принято строк", sum(file.accepted for file in run.files)),
        ("В карантине", sum(file.excluded for file in run.files)),
    ]


def kpi_table(run):
    kpi = run.kpi
    if isinstance(kpi, PeriodKPI):
        headers = ["Показатель", "Базовый период"]
        if kpi.comparison is not None:
            headers += ["Сравниваемый период", "Разница", "Изменение, %", "Пояснение"]
        rows = []
        for key in metric_keys(run):
            change = (kpi.changes or {}).get(key)
            row = [METRICS[key], getattr(kpi.base, key)]
            if kpi.comparison is not None:
                reasons = list(dict.fromkeys(filter(None, [
                    kpi.base.undefined.get(key), kpi.comparison.undefined.get(key),
                    change.absolute_reason if change else None, change.relative_reason if change else None,
                ])))
                row += [getattr(kpi.comparison, key), change.absolute if change else None,
                        change.relative_pct if change else None, ". ".join(reasons)]
            if kpi.comparison is None and key in kpi.base.undefined:
                row[1] = kpi.base.undefined[key]
            rows.append(row)
    else:
        headers = ["Показатель", "Итого", "Пояснение"]
        rows = [[METRICS[key], getattr(kpi.total, key), kpi.total.undefined.get(key)] for key in METRICS]
        rows.append(["Расхождение лидов", kpi.lead_gap, "Лиды из рекламы минус квалифицированные лиды CRM"])
    return headers, rows


def utm_table(kpi):
    return ["Расхождение", "Источник", "Кампания"], [
        [label, key.source, key.campaign]
        for label, keys in [("Расход без сделок", kpi.ad_without_crm), ("Сделки без расхода", kpi.crm_without_ad)]
        for key in keys
    ]


def campaign_table(run):
    keys = list(METRICS)
    delta_keys = list((run.kpi.changes or {}).keys()) if isinstance(run.kpi, PeriodKPI) else []
    headers = ["Кампания", "Источник", "Уровень", "Период", "Статус", *[METRICS[key] for key in keys],
               "Вердикт и рекомендация", "Неопределённые метрики"]
    headers += [f"Δ {METRICS[key]} · отн., %" for key in delta_keys]
    rows = []
    for item in run.campaigns:
        changes = item.changes or {}
        undefined = [f"{METRICS[key]}: {reason}" for key, reason in item.undefined.items()]
        undefined += [f"Δ {METRICS[key]}: {delta.relative_reason}" for key, delta in changes.items()
                      if delta.relative_reason]
        rows.append([
            item.campaign, item.source, "Кампания" if item.scope == "campaign" else "Источник",
            ROLES[item.period], item.audit_status, *[getattr(item, key) for key in keys], item.verdict,
            "\n".join(undefined), *[changes[key].relative_pct if key in changes else None for key in delta_keys],
        ])
    return headers, rows


def quarantine_table(run, *, excel=False):
    rows = []
    for item in run.quarantine:
        payload = json.dumps(item.raw, ensure_ascii=False)
        # Excel cells are limited to 32,767 characters. Leave room for UTF-16 pairs.
        fields = [payload[i:i + 16000] for i in range(0, len(payload), 16000)] if excel else [payload]
        rows.append([item.filename, ROLES[item.role], item.line, "\n".join(item.reasons), *fields])
    width = max((len(row) for row in rows), default=len(QUARANTINE_HEADERS))
    return QUARANTINE_HEADERS + [f"Исходные поля · часть {i}" for i in range(2, width - 3)], rows


def file_table(run):
    return ["Файл", "Роль", "Кодировка", "Разделитель", "Начало", "Конец", "Принято", "Карантин"], [
        [file.filename, ROLES[file.role], file.encoding, DELIMITERS[file.delimiter],
         file.period.start, file.period.end, file.accepted, file.excluded] for file in run.files
    ]


def safe_csv(value):
    if isinstance(value, Decimal):
        return str(value).replace(".", ",")
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def export_csv(run: RunDetail, kind: str) -> bytes:
    headers, rows = campaign_table(run) if kind == "campaigns" else quarantine_table(run)
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(headers)
    writer.writerows([safe_csv(value) for value in row] for row in rows)
    return output.getvalue().encode("utf-8-sig")


def write_table(sheet, headers, rows):
    header_row = sheet.max_row + 1 if sheet["A1"].value is not None else 1
    for column, value in enumerate(headers, 1):
        cell = sheet.cell(header_row, column, value)
        cell.fill = PatternFill("solid", fgColor="1E40AF")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[header_row].height = 32
    for row_index, row in enumerate(rows, header_row + 1):
        for column, value in enumerate(row, 1):
            cell = sheet.cell(row_index, column)
            if isinstance(value, str):
                cell.value = ILLEGAL_CHARACTERS_RE.sub("\uFFFD", value)
                cell.data_type = "s"
            else:
                cell.value = value
            cell.fill = PatternFill("solid", fgColor="F1F5FB" if row_index % 2 == 0 else "FFFFFF")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            width = max((len(line) for line in str(value or "").splitlines()), default=0)
            dimension = sheet.column_dimensions[cell.column_letter]
            dimension.width = min(55, max(dimension.width, width + 2))
            if isinstance(value, str) and (len(value) > 55 or "\n" in value):
                lines = sum(max(1, (len(line) + 49) // 50) for line in value.splitlines())
                sheet.row_dimensions[row_index].height = min(120, max(
                    sheet.row_dimensions[row_index].height or 16, lines * 15,
                ))
    for column, header in enumerate(headers, 1):
        dimension = sheet.column_dimensions[get_column_letter(column)]
        dimension.width = min(55, max(dimension.width, len(header) + 3))
    return header_row


def number_format(cell, format_code):
    if isinstance(cell.value, (int, Decimal)):
        if format_code == FORMAT_PERCENT:
            cell.value = Decimal(cell.value) / 100
        cell.number_format = format_code


def metric_format(key):
    if key in MONEY:
        return FORMAT_MONEY
    if key in PERCENT:
        return FORMAT_PERCENT
    return "0.00" if key == "roas" else "#,##0"


def export_xlsx(run: RunDetail) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Сводка KPI"
    campaigns = workbook.create_sheet("Аудит кампаний")
    quarantine = workbook.create_sheet("Карантин данных (QA)")
    for sheet in workbook:
        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0

    write_table(summary, ["Параметр запуска", "Значение"], metadata(run))
    number_format(summary["B7"], FORMAT_MONEY)
    number_format(summary["B8"], FORMAT_PERCENT)
    headers, rows = kpi_table(run)
    first = write_table(summary, headers, rows) + 1
    for index, key in enumerate(metric_keys(run), first):
        number_format(summary.cell(index, 2), metric_format(key))
        if isinstance(run.kpi, PeriodKPI) and run.kpi.comparison is not None:
            number_format(summary.cell(index, 3), metric_format(key))
            delta_format = '0.00 "п.п."' if key in PERCENT else metric_format(key)
            number_format(summary.cell(index, 4), delta_format)
            number_format(summary.cell(index, 5), FORMAT_PERCENT)
    write_table(summary, *file_table(run))
    if not isinstance(run.kpi, PeriodKPI):
        write_table(summary, *utm_table(run.kpi))

    headers, rows = campaign_table(run)
    write_table(campaigns, headers, rows)
    for row in campaigns.iter_rows(min_row=2, max_row=len(rows) + 1):
        status = row[4]
        label, fill, font = STATUS[status.value]
        status.value = label
        status.fill = PatternFill("solid", fgColor=fill)
        status.font = Font(bold=True, color=font)
        for cell, key in zip(row[5:], METRICS):
            number_format(cell, metric_format(key))
        for cell in row[7 + len(METRICS):]:
            number_format(cell, FORMAT_PERCENT)
    write_table(quarantine, *quarantine_table(run, excel=True))
    for sheet in (campaigns, quarantine):
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.print_title_rows = "1:1"

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def markdown_cell(value):
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        return f"{value:,.2f}".replace(",", "\u202f").replace(".", ",")
    escaped = html.escape(str(value).replace("\r", " ").replace("\n", " "), quote=False)
    return re.sub(r"([\\\x60*_\[\]|#~])", r"\\\1", escaped)


def markdown_table(headers, rows):
    def line(values):
        return "| " + " | ".join(map(markdown_cell, values)) + " |"

    return [line(headers), line(["---"] * len(headers)), *(line(row) for row in rows)]


def export_markdown(run: RunDetail) -> bytes:
    lines = [f"# {markdown_cell(run.name)}", "", *markdown_table(["Параметр", "Значение"], metadata(run)),
             "", "## Сводка KPI", "", *markdown_table(*kpi_table(run)),
             "", "## Качество данных", "", *markdown_table(*file_table(run))]
    if not isinstance(run.kpi, PeriodKPI):
        headers, rows = utm_table(run.kpi)
        lines += ["", "## Несопоставленные UTM-метки", "",
                  *(markdown_table(headers, rows) if rows else ["Все UTM-метки сопоставлены."])]
    lines += ["", "## Управленческие выводы и план действий", ""]
    period = "sync" if not isinstance(run.kpi, PeriodKPI) else "comparison" if run.kpi.comparison else "base"
    active = [item for item in run.campaigns if item.period == period and (
        item.scope == "campaign" or period == "sync"
    )]
    for status in ("critical", "warning", "scale"):
        items = [item for item in active if item.audit_status == status]
        lines += [f"### {STATUS[status][0]} ({len(items)})", ""]
        for item in items:
            name = f"{item.campaign} / {item.source}" if item.source else item.campaign
            lines.append(f"- **{markdown_cell(name)}** — {markdown_cell(item.verdict)}")
        if not items:
            lines.append("Кампаний с таким статусом нет.")
        lines.append("")
    return "\n".join(lines).encode("utf-8")
