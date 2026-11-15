# Ad & CRM ROI Reconciliation Hub

Система сквозной сверки рекламных расходов, CRM-выручки и качества данных. Она сравнивает два периода по кампаниям или сопоставляет рекламу со сделками CRM по UTM. Принятые строки участвуют в расчёте KPI, а ошибки сохраняются в карантине с исходными полями и номером физической строки CSV.

**Автор — Алексей Ханипов · AI & Marketing Automation.** [cyberneuro.ru](https://cyberneuro.ru)

## Роль в линейке Marketing Ops

| Продукт | Задача |
|---|---|
| Media Plan & Budget Builder | Планирование каналов, инвестиций и целевых KPI |
| UTM Governance Hub | Единый стандарт UTM для рекламных ссылок и CRM |
| Ad & CRM ROI Reconciliation Hub | Сверка фактических расходов и оплат, аудит кампаний, проверка выгрузок |

Сверка принимает CSV из рекламных кабинетов и CRM. В режиме «Реклама + CRM» ключи `utm_source` и `utm_campaign` очищаются от внешних пробелов и приводятся к нижнему регистру. Отчёт показывает расход, выручку, CAC, ДРР, ROAS и ROMI, пары UTM без совпадений и рекомендации для кампаний. При сравнении периодов отчёт показывает абсолютные и относительные изменения и причину, по которой отдельную метрику невозможно рассчитать.

## Поток данных

```mermaid
flowchart LR
    Ads["CSV рекламных кабинетов"] --> Parser["Парсер и валидация CSV"]
    CRM["CSV сделок CRM"] --> Parser
    Parser -->|"Принятые строки"| Engine["Сверка периодов или сопоставление UTM"]
    Parser -->|"Ошибки строк"| QA["Data Quarantine"]
    Engine --> Audit["KPI и аудит кампаний"]
    QA --> Database[("SQLite или PostgreSQL")]
    Audit --> Database
    Database --> API["FastAPI"]
    API --> SPA["SPA и печать"]
    API --> Export["Excel, CSV и Markdown"]
```

## Галерея

| Сравнение периодов | Реклама + CRM |
|---|---|
| ![Обзор сверки «Опора»](./screenshots/01_overview_period_compare.png) | ![Сквозная сверка рекламы и CRM](./screenshots/02_ad_crm_reconciliation.png) |

| Data Quarantine | Загрузка CSV |
|---|---|
| ![Файлы и исключённые строки](./screenshots/03_data_quarantine_qa.png) | ![Форма запуска и пороги аудита](./screenshots/04_new_reconciliation_upload.png) |

### История и экспорт

![История запусков и экспорт отчётов](./screenshots/05_run_history_and_export.png)

## Локальный запуск

Требуется Python 3.12+. Из корня проекта в PowerShell выполните:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

В Linux и macOS создайте окружение через `python3.12 -m venv .venv` и замените `.\.venv\Scripts\python` на `.venv/bin/python`.

Откройте [интерфейс](http://127.0.0.1:8000/) или [Swagger UI](http://127.0.0.1:8000/docs). По умолчанию используется `sqlite:///./reconciliation.db`. Для PostgreSQL установите переменную окружения `DATABASE_URL`, например `postgresql+psycopg://user:password@localhost:5432/reconciliation`.

## Запуск через Docker Compose

```powershell
Copy-Item .env.example .env
```

В Linux или macOS используйте `cp .env.example .env`. Задайте свой `POSTGRES_PASSWORD` в `.env`. Для DSN Compose используйте пароль из латинских букв, цифр, дефиса и подчёркивания.

```bash
docker compose up --build --wait
docker compose ps
```

Веб-интерфейс доступен по адресу [127.0.0.1:8000](http://127.0.0.1:8000/). Если порт 8000 занят, укажите другой порт в `WEB_PORT` в `.env` и откройте соответствующий адрес. Compose запускает приложение после прохождения проверки готовности PostgreSQL. База хранится в именованном томе `postgres_data`. Для остановки используйте `docker compose down`.

При первом запуске на пустой БД создаются два демо. Кнопка «Сбросить демо» пересоздаёт только демонстрационные запуски. Исходные CSV и примеры схем находятся в [sample_data/](./sample_data/).

## REST API

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/health` | Готовность приложения и БД |
| GET | `/api/runs` | Список запусков |
| GET | `/api/runs/{run_id}` | KPI, аудит, дельты или UTM, карантин |
| POST | `/api/reconciliations/period-compare` | CSV базового периода `base_file` и необязательный `comparison_file` |
| POST | `/api/reconciliations/ad-crm-sync` | CSV расходов `ad_file` и сделок `crm_file` |
| POST | `/api/demo/reset` | Пересоздать демо-запуски |
| DELETE | `/api/runs/{run_id}` | Удалить запуск |
| GET | `/api/runs/{run_id}/export/xlsx` | Excel-отчёт с тремя листами |
| GET | `/api/runs/{run_id}/export/csv?kind=campaigns` | Аудит кампаний в CSV |
| GET | `/api/runs/{run_id}/export/csv?kind=quarantine` | Журнал карантина в CSV |
| GET | `/api/runs/{run_id}/export/markdown` | Управленческий отчёт и план действий |

Загрузка принимает `multipart/form-data` и до 10 МиБ на файл. Дополнительные поля — `name`, `target_roas`, `max_cpl`, `target_drr`. CSV экспортируется в UTF‑8 с BOM и разделителем `;`.

## Тесты

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -W error
```

Набор из 20 автотестов покрывает контрольные суммы «Опоры», расчёты CRM, обработку CSV, API и экспорт отчётов.

## Материалы

- [Бизнес-кейс и модель окупаемости](./BUSINESS_CASE.md)
- [Автономная презентация на 10 слайдов](./presentation.html) — скачайте HTML и откройте локально. Навигация доступна кнопками и стрелками клавиатуры, печать настроена под A4 в альбомной ориентации.
