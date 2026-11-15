from collections import defaultdict
from decimal import Context, Decimal, localcontext

from app.schemas import CampaignResult, MetricChange, Metrics, PeriodKPI, RunOptions, SyncKPI, UTMKey
from app.services.csv_parser import ParsedFile

# Keep period cross-products exact for CSV inputs up to 64 characters per number.
PRECISION = Context(prec=300)
PERIOD_RATIOS = {
    "cpc": ("spend_rub", "clicks", 1),
    "cr1": ("leads", "clicks", 100),
    "cpl": ("spend_rub", "leads", 1),
    "roas": ("revenue_rub", "spend_rub", 1),
}
PERIOD_METRICS = ("spend_rub", "clicks", "leads", "revenue_rub", *PERIOD_RATIOS)


def calculate(ad: list[dict], crm: list[dict] | None = None) -> Metrics:
    spend = sum((row["spend_rub"] for row in ad), Decimal(0))
    clicks = sum(row["clicks"] for row in ad)
    leads = sum(row["leads"] for row in ad)
    qualified = deals = won_deals = 0
    margin = None
    if crm is None:
        revenue = sum((row["revenue_rub"] for row in ad), Decimal(0))
    else:
        won = [row for row in crm if row["status"] == "won"]
        qualified = sum(row.get("qualified_leads", row.get("deals", 1)) for row in crm)
        deals = sum(row.get("deals", 1) for row in crm)
        won_deals = sum(row.get("deals", 1) for row in won)
        revenue = sum((row["revenue_rub"] for row in won), Decimal(0))
        margin = sum((
            row["gross_margin_rub"] if "gross_margin_rub" in row else row["revenue_rub"] - row["cost_rub"]
            for row in won
        ), Decimal(0))

    undefined = {}

    def ratio(metric, numerator, denominator, label, multiplier=1):
        if denominator == 0:
            undefined[metric] = f"Нулевой знаменатель — {label}"
            return None
        return Decimal(numerator) * multiplier / denominator

    result = Metrics(
        spend_rub=spend, clicks=clicks, leads=leads, qualified_leads=qualified, deals=deals,
        won_deals=won_deals, revenue_rub=revenue, gross_margin_rub=margin,
        cpc=ratio("cpc", spend, clicks, "клики"), cr1=ratio("cr1", leads, clicks, "клики", 100),
        cpl=ratio("cpl", spend, leads, "заявки"),
        roas=ratio("roas", revenue, spend, "расход"),
        drr=ratio("drr", spend, revenue, "выручка", 100),
    )
    if crm is not None:
        result.cr2 = ratio("cr2", won_deals, qualified, "квалифицированные лиды CRM", 100)
        result.cac = ratio("cac", spend, won_deals, "оплаченные сделки")
        result.romi = ratio("romi", margin - spend, spend, "расход", 100)
    result.undefined = undefined
    return result


def compare(base: Metrics | None, next_period: Metrics | None) -> dict[str, MetricChange]:
    changes = {}
    for metric in PERIOD_METRICS:
        unit = "percentage_points" if metric == "cr1" else None
        if base is None or next_period is None:
            period = "базовом" if base is None else "сравниваемом"
            reason = f"Кампания или источник отсутствует в {period} файле"
            changes[metric] = MetricChange(absolute_unit=unit, absolute_reason=reason, relative_reason=reason)
            continue
        previous, current = getattr(base, metric), getattr(next_period, metric)
        if previous is None or current is None:
            reason = ". ".join(
                f"{label} — {item.undefined[metric]}" for label, item in
                (("Базовый период", base), ("Сравниваемый период", next_period)) if metric in item.undefined
            )
            changes[metric] = MetricChange(absolute_unit=unit, absolute_reason=reason, relative_reason=reason)
        else:
            if metric in PERIOD_RATIOS:
                numerator, denominator, scale = PERIOD_RATIOS[metric]
                base_num = Decimal(getattr(base, numerator))
                base_den = Decimal(getattr(base, denominator))
                next_num = Decimal(getattr(next_period, numerator))
                next_den = Decimal(getattr(next_period, denominator))
                difference = next_num * base_den - base_num * next_den
                absolute = difference * scale / (next_den * base_den)
                relative = difference * 100 / (next_den * base_num) if base_num else None
            else:
                absolute = Decimal(current) - Decimal(previous)
                relative = absolute * 100 / previous if previous else None
            changes[metric] = MetricChange(
                absolute=absolute, absolute_unit=unit,
                relative_pct=relative,
                relative_reason=None if previous else "Нулевое базовое значение",
            )
    return changes


def audit(metrics: Metrics, options: RunOptions, *, cpl_growth=None, missing=None, period_mode=False):
    notes = []
    if missing == "crm":
        notes.append("Расходы есть, сделок с такой UTM-меткой в CRM нет. Проверьте передачу UTM в CRM.")
    elif missing == "ad":
        notes.append("Сделки есть, расходов с такой UTM-меткой нет. Проверьте источник и полноту выгрузки.")
    if metrics.spend_rub > 0 and (metrics.revenue_rub == 0 or metrics.leads == 0):
        if metrics.revenue_rub == 0:
            notes.append("Есть расход при нулевой выручке. Проверьте цели и воронку перед продолжением расходов.")
        else:
            notes.append("Есть расход при нуле рекламных заявок. Проверьте счётчик и передачу конверсий.")
        return "critical", " ".join(notes)

    warnings = []
    if cpl_growth is not None and cpl_growth > 15:
        warnings.append("CPL вырос более чем на 15%. Проверьте ставки, аудитории и конверсию посадочной.")
    if metrics.cpl is not None and metrics.cpl > options.max_cpl:
        warnings.append("CPL выше заданного предела. Проверьте расходы по сегментам.")
    if metrics.drr is not None and metrics.drr > options.target_drr:
        warnings.append("ДРР выше цели. Проверьте расходы и выручку по сегментам.")
    if notes or warnings:
        return "warning", " ".join(notes + warnings)
    sales = metrics.revenue_rub > 0 if period_mode else metrics.won_deals > 0
    if sales and metrics.roas is not None and metrics.roas > options.target_roas:
        return "scale", "ROAS выше цели. Увеличивайте бюджет постепенно и отслеживайте выручку."
    return "normal", "Продолжайте наблюдение по заданным порогам."


def campaign_result(metrics, key, period, options, changes=None, missing=None, cpl_growth=None):
    campaign, source = key
    status, verdict = audit(metrics, options, cpl_growth=cpl_growth, missing=missing, period_mode=period != "sync")
    return CampaignResult(
        **metrics.model_dump(), campaign=campaign, source=source,
        scope="campaign" if source is None else "source", period=period,
        audit_status=status, verdict=verdict, changes=changes,
    )


def by_utm(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["campaign"], row["source"])].append(row)
    return groups


def period_groups(file):
    groups = by_utm(file.rows)
    for source, campaign in file.groups:
        groups[(campaign, source)]
    for (campaign, source), rows in list(groups.items()):
        if source is not None:
            groups[(campaign, None)].extend(rows)
    return {key: calculate(rows) for key, rows in groups.items()}


def period_compare(base: ParsedFile, other: ParsedFile | None, options: RunOptions):
    with localcontext(PRECISION):
        base_total = calculate(base.rows)
        next_total = calculate(other.rows) if other else None
        kpi = PeriodKPI(
            base=base_total, comparison=next_total, changes=compare(base_total, next_total) if other else None,
        )
        left = period_groups(base)
        right = period_groups(other) if other else {}
        campaigns = []
        for key in sorted(left.keys() | right.keys(), key=lambda item: (item[0], item[1] or "")):
            previous, current = left.get(key), right.get(key)
            changes = compare(previous, current) if other else None
            if previous is not None:
                campaigns.append(campaign_result(previous, key, "base", options, changes if current is None else None))
            if current is not None:
                campaigns.append(campaign_result(
                    current, key, "comparison", options, changes, cpl_growth=changes["cpl"].relative_pct,
                ))
        return kpi, campaigns


def ad_crm_sync(ad: ParsedFile, crm: ParsedFile, options: RunOptions):
    with localcontext(PRECISION):
        ad_groups = by_utm(ad.rows)
        crm_groups = by_utm(crm.rows)
        ad_only = ad_groups.keys() - crm_groups.keys()
        crm_only = crm_groups.keys() - ad_groups.keys()
        total = calculate(ad.rows, crm.rows)
        kpi = SyncKPI(
            total=total, lead_gap=total.leads - total.qualified_leads,
            ad_without_crm=[UTMKey(campaign=campaign, source=source) for campaign, source in sorted(ad_only)],
            crm_without_ad=[UTMKey(campaign=campaign, source=source) for campaign, source in sorted(crm_only)],
        )
        campaigns = []
        for key in sorted(ad_groups.keys() | crm_groups.keys()):
            metrics = calculate(ad_groups.get(key, []), crm_groups.get(key, []))
            missing = "crm" if key in ad_only else "ad" if key in crm_only else None
            campaigns.append(campaign_result(metrics, key, "sync", options, missing=missing))
        return kpi, campaigns
