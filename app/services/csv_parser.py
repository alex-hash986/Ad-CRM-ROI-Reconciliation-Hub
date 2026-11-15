import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from app.schemas import FileInfo, Period, Quarantined

ALIASES = {
    "date": ("date", "дата", "дата создания", "created_at", "day"),
    "campaign": ("campaign", "кампания", "название кампании", "рекламная кампания",
                 "campaign_name", "utm_campaign"),
    "source": ("source", "источник", "площадка", "канал", "utm_source", "platform"),
    "spend_rub": ("spend_rub", "spend", "cost", "ad spend", "расход", "расход руб",
                  "расход всего", "затраты", "потрачено всего"),
    "clicks": ("clicks", "клики", "переходы"),
    "leads": ("leads", "заявки", "лиды", "конверсии", "conversions"),
    "revenue_rub": ("revenue_rub", "revenue", "выручка", "выручка руб", "amount",
                    "сумма сделки", "бюджет"),
    "cost_rub": ("cost_rub", "cost_of_goods", "cogs", "себестоимость", "себестоимость руб"),
    "gross_margin_rub": ("gross_margin_rub", "gross_margin", "margin", "маржа", "валовая маржа"),
    "status": ("status", "статус", "статус сделки", "этап сделки", "stage"),
    "deal_id": ("deal_id", "id", "id сделки", "lead_id", "идентификатор сделки"),
    "deals": ("deals", "сделки", "количество сделок"),
    "qualified_leads": ("qualified_leads", "квалифицированные лиды", "qualified"),
}
STATUS = {
    "won": "won", "успешно реализовано": "won", "оплачено": "won", "closed_won": "won",
    "in_progress": "in_progress", "в работе": "in_progress", "open": "in_progress",
    "lost": "lost", "close_lost": "lost", "closed_lost": "lost",
    "закрыто и не реализовано": "lost", "проиграно": "lost",
}
COMMON = ("date", "campaign", "source")
AD = ("spend_rub", "clicks", "leads")
PERIOD = (*COMMON, *AD, "revenue_rub")
INTEGERS = {"clicks", "leads", "deals", "qualified_leads"}
NUMBERS = set(AD) | INTEGERS | {"revenue_rub", "cost_rub", "gross_margin_rub"}
NUMBER = re.compile(r"[+-]?[0-9]+(?:[.,][0-9]+)?")
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class CsvInputError(ValueError):
    pass


@dataclass
class ParsedFile:
    info: FileInfo
    rows: list[dict]
    quarantine: list[Quarantined]
    groups: set[tuple[str | None, str]]


def header_key(value: str) -> str:
    return "_".join(re.findall(r"\w+", value.strip().casefold()))


HEADERS = {header_key(alias): field for field, aliases in ALIASES.items() for alias in aliases}


def decode(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1251"), "cp1251"


def records(body: str, delimiter: str):
    stream = io.StringIO(body, newline="")
    reader = csv.reader(stream, delimiter=delimiter, strict=True)
    line = 1
    separator = re.escape(delimiter)
    field = rf'(?:"(?:[^"]|"")*"|[^{separator}"\r\n]*)'
    quoted_record = re.compile(rf"{field}(?:{separator}{field})*(?:\r\n|\r|\n)?")
    while True:
        start = stream.tell()
        first_line = line
        try:
            values = next(reader)
        except StopIteration:
            return
        except csv.Error as error:
            stream.seek(start)
            raw = stream.readline()
            reader = csv.reader(stream, delimiter=delimiter, strict=True)
            line += 1
            yield first_line, None, raw, str(error)
        else:
            end = stream.tell()
            stream.seek(start)
            raw = stream.read(end - start)
            line += len(re.findall(r"\r\n|\r|\n", raw))
            if '"' in raw and not quoted_record.fullmatch(raw):
                yield first_line, None, raw, "Неверное оформление кавычек"
            else:
                yield first_line, values, raw, None


def parse_csv(filename: str, data: bytes, role: str) -> ParsedFile:
    try:
        body, encoding = decode(data)
    except UnicodeDecodeError as error:
        raise CsvInputError(f"{filename} — не удалось определить кодировку") from error

    required = {
        "base": PERIOD, "comparison": PERIOD, "ad": (*COMMON, *AD),
        "crm": (*COMMON, "status", "revenue_rub"),
    }[role]
    candidates = []
    for delimiter in (",", ";", "\t"):
        iterator = records(body, delimiter)
        _, header, _, error = next(iterator, (1, None, "", None))
        if header and not error:
            mapped = [HEADERS.get(header_key(value)) for value in header]
            candidates.append((len(set(required).intersection(mapped)), delimiter, header, mapped, iterator))
    if not candidates:
        raise CsvInputError(f"{filename}, строка 1 — заголовок CSV отсутствует или повреждён")
    _, delimiter, header, mapped, iterator = max(candidates, key=lambda candidate: candidate[0])
    missing = [field for field in required if field not in mapped]
    duplicates = sorted({field for field in mapped if field and mapped.count(field) > 1})
    if missing or duplicates or len(set(header)) != len(header):
        raise CsvInputError(
            f"{filename}, строка 1 — отсутствуют поля: {', '.join(missing) or '—'}. "
            f"Повторяющиеся поля: {', '.join(duplicates) or '—'}"
        )

    dates, accepted, quarantine = [], [], []
    keys: dict[tuple, int] = {}
    groups: set[tuple[str | None, str]] = set()
    for line, values, raw, error in iterator:
        reasons = []
        fields = dict(zip(header, values or []))
        if error:
            reasons.append(f"Неверная структура CSV — {error}")
            fields["_record"] = raw
        else:
            if len(values) != len(header):
                reasons.append(f"Число полей {len(values)}, ожидается {len(header)}")
                fields["_record"] = raw
            row = validate_row(mapped, values, required, role, reasons)
            if row.get("date"):
                dates.append(row["date"])
            if role in {"base", "comparison"} and row.get("campaign"):
                groups.add((row.get("source"), row["campaign"]))
            if row.get("campaign") and row.get("source"):
                if role == "crm":
                    key = ("deal_id", row["deal_id"]) if row.get("deal_id") else (
                        "record", *(row.get(field) for field in sorted(set(mapped) - {None}))
                    )
                else:
                    key = (row.get("date"), row["campaign"], row["source"]) if row.get("date") else None
                if key is not None and key in keys:
                    reasons.append(f"Дубликат ключа принятой строки {keys[key]}")
                if key is not None and not reasons:
                    keys[key] = line
            if not reasons:
                accepted.append(row)
        if reasons:
            quarantine.append(Quarantined(filename=filename, role=role, line=line, raw=fields, reasons=reasons))

    info = FileInfo(
        filename=filename, role=role, encoding=encoding, delimiter=delimiter,
        period=Period(start=min(dates, default=None), end=max(dates, default=None)),
        accepted=len(accepted), excluded=len(quarantine), records=len(accepted) + len(quarantine),
    )
    return ParsedFile(info, accepted, quarantine, groups)


def validate_row(mapped, values, required, role, reasons):
    raw = {field: value for field, value in zip(mapped, values) if field}
    row = {}
    for field in dict.fromkeys((*required, *raw)):
        original = raw.get(field, "")
        value = original.strip()
        if not value:
            if field in required:
                reasons.append(f"{field} — пустое обязательное поле")
            continue
        if field == "date":
            try:
                if not ISO_DATE.fullmatch(original):
                    raise ValueError()
                row[field] = date.fromisoformat(original)
            except ValueError:
                reasons.append("date — ожидается календарная дата в формате YYYY-MM-DD")
        elif field in NUMBERS:
            if len(value) > 64 or not NUMBER.fullmatch(value):
                reasons.append(f"{field} — ожидается число в десятичной записи до 64 символов")
                continue
            number = Decimal(value.replace(",", "."))
            if number < 0 and field != "gross_margin_rub":
                reasons.append(f"{field} — отрицательное значение")
            if field in INTEGERS and number != number.to_integral_value():
                reasons.append(f"{field} — требуется целое число")
            else:
                row[field] = int(number) if field in INTEGERS else number
        elif field == "status":
            if value.lower() in STATUS:
                row[field] = STATUS[value.lower()]
            else:
                reasons.append("status — ожидается won, in_progress или lost")
        elif field in {"campaign", "source"}:
            row[field] = value.lower() if role in {"ad", "crm"} else original
        else:
            row[field] = value
    if role == "crm" and row.get("status") == "won":
        if row.get("gross_margin_rub") is None and row.get("cost_rub") is None:
            reasons.append("Для оплаченной сделки укажите себестоимость или валовую маржу")
        elif all(field in row for field in ("gross_margin_rub", "cost_rub", "revenue_rub")):
            with localcontext() as context:
                context.prec = 140
                if row["revenue_rub"] - row["cost_rub"] != row["gross_margin_rub"]:
                    reasons.append("Валовая маржа не совпадает с разницей выручки и себестоимости")
    return row
