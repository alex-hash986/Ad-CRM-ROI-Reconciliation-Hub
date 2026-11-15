"use strict";

const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
};
const modeLabels = { period_compare: "Периоды", ad_crm_sync: "Реклама + CRM" };
const roles = { base: "Базовый", comparison: "Сравниваемый", sync: "Реклама + CRM", ad: "Реклама", crm: "CRM" };
const statuses = { critical: "Критические", warning: "Требуют внимания", scale: "Масштабирование", normal: "Норма" };
const colors = ["#3158bf", "#16806b", "#7587bb", "#b56b13"];
const num = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const time = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const fmt = (value, unit = "") => value == null ? "—" : num.format(value) + unit;
const money = (value) => fmt(value, " ₽");
const deltaText = (value) => (Number(value) > 0 ? "+" : "") + fmt(value, " %");
const charts = new Map();
let runList = [];
let active = null;
let selection = 0;
let busy = false;

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    let detail = body?.detail;
    if (Array.isArray(detail)) detail = detail.map((entry) => {
      return [entry.loc?.at(-1), entry.msg].filter(Boolean).join(" — ");
    }).join(". ");
    throw new Error(detail || "Запрос завершился ошибкой (" + response.status + ").");
  }
  return response.status === 204 ? null : response.json();
}

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
  $("notice").hidden = !message;
}

function showTab(id, focus = false) {
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    const panel = tab.getAttribute("aria-controls");
    const selected = panel === id;
    tab.setAttribute("aria-selected", selected);
    tab.tabIndex = selected ? 0 : -1;
    $(panel).hidden = !selected;
    if (selected && focus) tab.focus();
  }
  if (id === "audit") renderCharts();
}

for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener("click", () => showTab(tab.getAttribute("aria-controls")));
  tab.addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(tab);
    const next = {
      ArrowLeft: (index + tabs.length - 1) % tabs.length,
      ArrowRight: (index + 1) % tabs.length,
      Home: 0, End: tabs.length - 1,
    }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    showTab(tabs[next].getAttribute("aria-controls"), true);
  });
}

function makeTable(target, headers, rows, empty = "Нет данных.", className = "") {
  target.replaceChildren();
  if (!rows.length) {
    target.append(node("p", empty, "empty"));
    return;
  }
  const table = node("table", undefined, className);
  const header = table.createTHead().insertRow();
  for (const title of headers) {
    const cell = node("th", title);
    cell.scope = "col";
    header.append(cell);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    for (const value of row) {
      const cell = line.insertCell();
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value ?? "—";
    }
  }
  target.append(table);
}

function link(id, href) {
  const anchor = $(id);
  if (href) anchor.href = href;
  else anchor.removeAttribute("href");
  anchor.setAttribute("aria-disabled", !href);
}

function period(file) {
  const { start, end } = file.period;
  if (!start) return "Даты не определены";
  const dateLabel = (value) => value.split("-").reverse().join(".");
  return start === end ? dateLabel(start) : dateLabel(start) + "–" + dateLabel(end);
}

function addCard(label, value, note, change) {
  const card = node("article", undefined, "kpi");
  card.append(node("h2", label, "kpi-label"), node("p", value, "kpi-value"));
  const line = node("p", note, "kpi-note");
  if (change?.relative_pct != null) {
    line.append(" ", node("span", deltaText(change.relative_pct), "delta"));
  } else if (change?.relative_reason) {
    const badge = node("span", "Δ —", "delta");
    badge.title = change.relative_reason;
    line.append(" ", badge);
  }
  card.append(line);
  $("kpis").append(card);
}

function renderOverview() {
  $("kpis").replaceChildren();
  $("utm").replaceChildren();
  $("utm").hidden = true;
  $("print").hidden = !active;
  $("run-title").textContent = active?.name ?? "Нет активной сверки";
  $("run-period").textContent = active ? [
    modeLabels[active.mode], ...active.files.map((file) => roles[file.role] + " · " + period(file)),
  ].join("  /  ") : "";
  for (const [id, format] of Object.entries({
    excel: "xlsx", markdown: "markdown", "audit-csv": "csv?kind=campaigns", "qa-csv": "csv?kind=quarantine",
  })) {
    link(id, active ? "/api/runs/" + active.id + "/export/" + format : null);
  }
  if (!active) return;

  const sync = active.mode === "ad_crm_sync";
  const { kpi, files } = active;
  const current = sync ? kpi.total : kpi.comparison ?? kpi.base;
  const changes = kpi.changes ?? {};
  const report = sync ? "За весь период" : kpi.comparison ? "К базовому периоду" : "Базовый период";
  addCard("Расход", money(current.spend_rub), report, changes.spend_rub);
  addCard(sync ? "Лиды / сделки CRM" : "Лиды", integer.format(current.leads),
    sync ? integer.format(current.deals) + " сделок · " + integer.format(current.won_deals) + " оплачено" : report,
    changes.leads);
  addCard("Выручка", money(current.revenue_rub), report, changes.revenue_rub);
  addCard(sync ? "CPL / CAC" : "CPL", money(current.cpl),
    sync ? "CAC " + money(current.cac) : report, changes.cpl);
  addCard(sync ? "ROAS / ROMI" : "ROAS", fmt(current.roas, "×"),
    sync ? "ROMI " + fmt(current.romi, " %") : report, changes.roas);
  const accepted = files.reduce((sum, file) => sum + file.accepted, 0);
  const excluded = files.reduce((sum, file) => sum + file.excluded, 0);
  const qaChange = !sync && files.length === 2 && files[0].accepted ? {
    relative_pct: (files[1].accepted - files[0].accepted) / files[0].accepted * 100,
  } : null;
  addCard("Качество данных", integer.format(accepted) + " / " + integer.format(excluded),
    "Принято / карантин" + (qaChange ? " · Δ принято" : ""), qaChange);

  if (sync) {
    $("utm").hidden = false;
    const groups = [
      ["Расхождение лидов", "Реклама − CRM: " + integer.format(kpi.lead_gap)],
      ["UTM · расход без сделок", kpi.ad_without_crm],
      ["UTM · сделки без расхода", kpi.crm_without_ad],
    ];
    for (const [title, value] of groups) {
      const group = node("div");
      group.append(node("h3", title));
      if (Array.isArray(value) && value.length) {
        const list = node("ul");
        for (const item of value) list.append(node("li", item.source + " / " + item.campaign));
        group.append(list);
      } else {
        group.append(node("p", Array.isArray(value) ? "Нет расхождений" : value));
      }
      $("utm").append(group);
    }
  }
}

function withChange(row, key, unit) {
  const cell = node("div", fmt(row[key], unit), "number");
  const change = row.changes?.[key];
  if (change?.relative_pct != null) {
    cell.append(node("br"), node("span", deltaText(change.relative_pct), "delta"));
  } else if (change?.relative_reason) {
    const empty = node("span", "Δ —", "delta");
    empty.title = change.relative_reason;
    cell.append(node("br"), empty);
  }
  if (row.undefined[key]) cell.title = row.undefined[key];
  return cell;
}

function renderAudit() {
  $("analytics").hidden = !active;
  $("empty-audit").hidden = Boolean(active);
  if (!active) return;
  const query = $("search").value.trim().toLocaleLowerCase("ru");
  const status = $("status-filter").value;
  const rows = active.campaigns.filter((row) => (!status || row.audit_status === status) &&
    (!query || row.campaign.toLocaleLowerCase("ru").includes(query)));
  $("table-count").textContent = "Показано " + rows.length + " из " + active.campaigns.length;
  const sync = active.mode === "ad_crm_sync";
  const columns = [["spend_rub", "Расход", " ₽"], ["revenue_rub", "Выручка", " ₽"],
    ["leads", "Лиды", ""], ["cpl", "CPL", " ₽"], ["roas", "ROAS", "×"]];
  if (sync) columns.push(["won_deals", "Оплачено", ""], ["cac", "CAC", " ₽"], ["romi", "ROMI", " %"]);
  columns.push(["drr", "ДРР", " %"]);
  const headers = ["Кампания", "Источник", "Период", "Статус", ...columns.map((entry) => entry[1]), "Вердикт и рекомендация"];
  makeTable($("campaigns"), headers, rows.map((row) => [
    row.campaign, row.source ?? "Все источники", roles[row.period],
    node("span", statuses[row.audit_status], "badge " + row.audit_status),
    ...columns.map(([key, , unit]) => withChange(row, key, unit)), row.verdict,
  ]), "Кампаний по выбранному фильтру нет.", "campaign-table");
}

function renderQA() {
  makeTable($("files"), ["Файл", "Роль", "Кодировка", "Разделитель", "Период", "Принято", "В карантине"],
    (active?.files ?? []).map((file) => [
      file.filename, roles[file.role], file.encoding,
      { ",": "Запятая (,)", ";": "Точка с запятой (;)", "\t": "Табуляция" }[file.delimiter],
      period(file), file.accepted, file.excluded,
    ]));
  makeTable($("errors"), ["Файл", "Роль", "Строка CSV", "Причины", "Исходные поля"],
    (active?.quarantine ?? []).map((item) => {
      const reasons = node("ul");
      for (const reason of item.reasons) reasons.append(node("li", reason));
      const raw = node("details");
      raw.append(node("summary", "Показать поля"), node("pre", JSON.stringify(item.raw, null, 2)));
      return [item.filename, roles[item.role], item.line, reasons, raw];
    }), active ? "Исключённых строк нет." : "Выберите сверку.");
}

const channelFields = ["spend_rub", "revenue_rub", "leads", "won_deals", "gross_margin_rub"];
function channelTotals(rows) {
  const grouped = new Map();
  for (const row of rows) {
    if (!row.source) continue;
    if (!grouped.has(row.source)) grouped.set(row.source, Object.fromEntries(channelFields.map((key) => [key, 0])));
    const channel = grouped.get(row.source);
    for (const key of channelFields) channel[key] += Number(row[key] ?? 0);
  }
  return grouped;
}

function drawChart(id, labels, series, axis = "₽") {
  const canvas = $(id + "-chart");
  const area = canvas.parentElement;
  const details = area.nextElementSibling;
  charts.get(id)?.destroy();
  charts.delete(id);
  const fallback = !window.Chart || !labels.length;
  area.hidden = fallback;
  details.open = fallback;
  makeTable($(id + "-data"), ["Кампания / канал", ...series.map((item) => item.label)],
    labels.map((label, index) => [label, ...series.map((item) => fmt(item.values[index], item.unit))]));
  if (fallback) return;
  charts.set(id, new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: series.map((item, index) => ({
      label: item.label, data: item.values.map((value) => value == null ? null : Number(value)),
      backgroundColor: colors[index], borderRadius: 4, maxBarThickness: 46, yAxisID: item.yAxisID ?? "y",
    })) },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 9, boxHeight: 9, color: "#354662" } },
        tooltip: { callbacks: { label: (item) => item.dataset.label + " · " + fmt(item.raw, series[item.datasetIndex].unit) } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: "#53637c" } },
        y: { beginAtZero: true, ticks: { callback: (value) => fmt(value, " " + axis), color: "#53637c" },
          grid: { color: "#e8edf5" } },
        ...(series.some((item) => item.yAxisID === "right") ? {
          right: { position: "right", beginAtZero: true, grid: { display: false },
            ticks: { callback: (value) => fmt(value, " %"), color: "#53637c" } },
        } : {}),
      },
    },
  }));
}

function renderCharts() {
  if (!active || $("audit").hidden) return;
  const sync = active.mode === "ad_crm_sync";
  const current = sync ? "sync" : active.kpi.comparison ? "comparison" : "base";
  const rows = active.campaigns.filter((row) => row.period === current);
  const compare = !sync && Boolean(active.kpi.comparison);
  const labels = new Map();
  const periods = compare ? ["base", "comparison"] : [current];
  for (const row of active.campaigns) {
    if (!periods.includes(row.period) || (!sync && row.scope !== "campaign")) continue;
    const label = sync ? row.campaign + " / " + row.source : row.campaign;
    if (!labels.has(label)) labels.set(label, {});
    labels.get(label)[row.period] = row;
  }
  const ordered = [...labels].sort(([, a], [, b]) =>
    Number(b[current]?.spend_rub ?? 0) - Number(a[current]?.spend_rub ?? 0));
  $("chart-period").textContent = compare ? "Базовый и сравниваемый периоды"
    : sync ? "По кампаниям · весь период" : "По кампаниям · базовый период";
  const spendSeries = periods.flatMap((periodKey) => {
    const suffix = compare ? " · " + roles[periodKey].toLowerCase() : "";
    return [["spend_rub", "Расход"], ["revenue_rub", "Выручка"]].map(([key, label]) => ({
      label: label + suffix, values: ordered.map(([, periodsByCampaign]) => periodsByCampaign[periodKey]?.[key]),
      unit: " ₽",
    }));
  });
  drawChart("spend", ordered.map(([label]) => label), spendSeries);
  const select = $("chart-mode");
  select.options[0].textContent = sync ? "ROAS / ROMI" : "ROAS";
  select.options[1].textContent = sync ? "CPL / CAC" : "CPL";
  const channels = [...channelTotals(rows).entries()].sort(([left], [right]) => left.localeCompare(right));
  const rate = (numerator, denominator) => denominator ? numerator / denominator : null;
  const costs = select.value === "cost";
  const specs = costs ? [
    { label: "CPL", unit: " ₽", value: (row) => rate(row.spend_rub, row.leads) },
    { label: "CAC", unit: " ₽", value: (row) => rate(row.spend_rub, row.won_deals) },
  ] : [
    { label: "ROAS", unit: "×", value: (row) => rate(row.revenue_rub, row.spend_rub) },
    { label: "ROMI", unit: " %", yAxisID: "right", value: (row) => rate((row.gross_margin_rub - row.spend_rub) * 100, row.spend_rub) },
  ];
  drawChart("efficiency", channels.map(([name]) => name), specs.slice(0, sync ? 2 : 1).map((entry) => ({
    ...entry, values: channels.map(([, row]) => entry.value(row)),
  })), costs ? "₽" : "×");
}

function renderHistory() {
  makeTable($("run-history"), ["Сверка", "Режим", "Запуск", "Действия"], runList.map((run) => {
    const actions = node("div", undefined, "actions");
    const open = node("button", "Открыть", "button");
    open.type = "button";
    open.addEventListener("click", () => {
      showTab("audit");
      selectRun(run.id);
    });
    actions.append(open);
    if (!run.is_demo) {
      const remove = node("button", "Удалить", "button quiet");
      remove.type = "button";
      remove.addEventListener("click", () => deleteRun(run));
      actions.append(remove);
    }
    return [run.name + (run.is_demo ? " · Демо" : ""), modeLabels[run.mode],
      time.format(new Date(run.created_at)), actions];
  }), "История запусков пуста.");
}

function render() {
  renderOverview();
  renderAudit();
  renderQA();
  renderCharts();
}

async function selectRun(id) {
  const version = ++selection;
  $("run-select").value = id ?? "";
  if (active?.id !== id) {
    $("search").value = "";
    $("status-filter").value = "";
  }
  if (id == null) {
    active = null;
    render();
    return;
  }
  active = null;
  render();
  $("run-title").textContent = "Загрузка сверки…";
  try {
    const value = await request("/api/runs/" + id);
    if (version !== selection) return;
    active = value;
    render();
  } catch (error) {
    if (version !== selection) return;
    render();
    notice(error.message, true);
  }
}

async function reloadRuns(preferred) {
  runList = await request("/api/runs");
  const select = $("run-select");
  select.replaceChildren();
  for (const run of runList) {
    const option = node("option", modeLabels[run.mode] + " · " + run.name);
    option.value = run.id;
    select.append(option);
  }
  if (!runList.length) select.append(node("option", "История пуста"));
  select.disabled = !runList.length;
  renderHistory();
  const chosen = runList.find((run) => run.id === preferred) ??
    runList.find((run) => run.mode === "period_compare") ?? runList[0];
  await selectRun(chosen?.id);
}

async function deleteRun(run) {
  if (busy || !confirm("Удалить сверку «" + run.name + "»?")) return;
  busy = true;
  try {
    await request("/api/runs/" + run.id, { method: "DELETE" });
    await reloadRuns(active?.id === run.id ? undefined : active?.id);
    notice("Сверка удалена.");
  } catch (error) {
    notice(error.message, true);
  } finally {
    busy = false;
  }
}

const uploadModes = {
  ad_crm_sync: [
    ["ad_file", "Расходы рекламного кабинета", "date, campaign, source, spend_rub, clicks, leads", "ad_spend.csv", true],
    ["crm_file", "Сделки CRM", "date, utm_campaign, utm_source, status, revenue_rub, cost_rub или gross_margin_rub", "crm_deals.csv", true],
  ],
  period_compare: [
    ["base_file", "Базовый период", "date, campaign, source, spend_rub, clicks, leads, revenue_rub", "VMvc3_Рекламная_выгрузка_Опора.csv", true],
    ["comparison_file", "Сравниваемый период (необязательно)", "Те же колонки, что и в базовом файле", "VMvc3_Рекламная_выгрузка_Опора_21-25_сентября.csv", false],
  ],
};
const form = $("upload-form");

function formError(message) {
  $("form-error").hidden = !message;
  $("form-error").textContent = message;
}

function drops() {
  const parent = $("dropzones");
  parent.replaceChildren();
  formError("");
  for (const [key, title, columns, sample, required] of uploadModes[form.elements.mode.value]) {
    const box = node("div", undefined, "dropzone");
    const label = node("label", title);
    const file = node("input");
    file.type = "file";
    file.accept = ".csv,text/csv";
    file.name = key;
    file.required = required;
    label.append(file);
    const info = node("p", "Выберите или перетащите CSV", "muted caption");
    const template = node("a", "Скачать шаблон CSV");
    template.href = "/sample_data/" + encodeURIComponent(sample);
    template.download = sample;
    box.append(label, info, node("p", columns, "muted caption"), template);
    for (const event of ["dragenter", "dragover"]) {
      box.addEventListener(event, (e) => {
        e.preventDefault();
        if (!busy) box.classList.add("dragging");
      });
    }
    box.addEventListener("dragleave", (e) => {
      if (!box.contains(e.relatedTarget)) box.classList.remove("dragging");
    });
    box.addEventListener("drop", (event) => {
      event.preventDefault();
      box.classList.remove("dragging");
      if (busy) return;
      const [candidate] = event.dataTransfer.files;
      if (!candidate) return;
      if (event.dataTransfer.files.length !== 1 || !candidate.name.toLowerCase().endsWith(".csv")) {
        formError("Перетащите один файл с расширением .csv.");
        return;
      }
      const data = new DataTransfer();
      data.items.add(candidate);
      file.files = data.files;
      formError("");
    });
    file.addEventListener("change", () => formError(""));
    parent.append(box);
  }
}

form.addEventListener("change", (event) => {
  if (event.target.name === "mode") drops();
});
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const mode = form.elements.mode.value;
  const data = new FormData(form);
  data.delete("mode");
  if (!data.get("name")?.trim()) data.delete("name");
  for (const [key, file] of [...data]) {
    if (!(file instanceof File)) continue;
    if (!file.name) {
      data.delete(key);
      continue;
    }
    if (!file.name.toLowerCase().endsWith(".csv") || file.size > 10 * 1024 * 1024) {
      formError("Проверьте формат и размер файла «" + file.name + "».");
      return;
    }
  }
  const submit = $("submit-run");
  const fields = form.querySelectorAll("input");
  busy = true;
  submit.disabled = true;
  submit.textContent = "Сверка выполняется…";
  fields.forEach((input) => { input.disabled = true; });
  formError("");
  try {
    const result = await request("/api/reconciliations/" + mode.replaceAll("_", "-"), { method: "POST", body: data });
    await reloadRuns(result.id);
    form.reset();
    drops();
    showTab("audit", true);
    notice("Сверка завершена.");
  } catch (error) {
    formError(error.message);
  } finally {
    busy = false;
    submit.disabled = false;
    submit.textContent = "Запустить сверку";
    fields.forEach((input) => { input.disabled = false; });
  }
});

$("run-select").addEventListener("change", (event) => {
  notice("");
  selectRun(Number(event.target.value));
});
$("new-run").addEventListener("click", () => showTab("upload", true));
$("reset").addEventListener("click", async () => {
  if (busy || !confirm("Пересоздать два демонстрационных запуска?")) return;
  busy = true;
  $("reset").disabled = true;
  try {
    const demos = await request("/api/demo/reset", { method: "POST" });
    await reloadRuns(demos[0]?.id);
    notice("Демонстрационные сверки пересозданы.");
  } catch (error) {
    notice(error.message, true);
  } finally {
    busy = false;
    $("reset").disabled = false;
  }
});
$("search").addEventListener("input", renderAudit);
$("status-filter").addEventListener("change", renderAudit);
$("chart-mode").append(new Option("ROAS", "return"), new Option("CPL", "cost"));
$("chart-mode").addEventListener("change", renderCharts);
$("chart-library").addEventListener("load", renderCharts);
$("print").addEventListener("click", () => window.print());
window.addEventListener("beforeprint", () => charts.forEach((chart) => chart.resize(455, 195)));
window.addEventListener("afterprint", () => charts.forEach((chart) => chart.resize()));
drops();
reloadRuns().catch((error) => {
  $("run-title").textContent = "Не удалось загрузить сверки";
  notice(error.message, true);
});
