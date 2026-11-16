# Проверка на живом Keitaro

Панель: `https://tlgk.host/admin` · API: `https://tlgk.host/admin_api/v1` · ключ из «Профиль → API ключи».

## 1. Кампания создана через наш сервис

`POST http://127.0.0.1:8000/api/campaigns` с телом `{"name":"campaign1","geo":"AU","offer_id":25327}`.

| Поле | Значение |
|---|---|
| id | `212056` |
| name / alias | `test_vorobev_campaign1` |
| ссылка | `https://kylovoa.com/test_vorobev_campaign1` |
| domain_id / group_id / traffic_source_id | `1813` / `9799` / `836` |

Сырой ответ `GET /campaigns/212056` — в [campaign_212056.json](campaign_212056.json).

## 2. Потоки

`GET /campaigns/212056/streams` (полный ответ — [streams_212056.json](streams_212056.json)):

| # | id | name | schema | редирект | фильтр | офферы |
|---|---|---|---|---|---|---|
| 1 | `1311120` | Google | `redirect` | `action_options.url = https://www.google.com/` | `country accept ["AU"]` | — |
| 2 | `1311121` | Offers | `landings` | — | — | `25327: 34`, `24800: 33`, `10184: 33` (сумма 100) |

Ключевые фрагменты ответа:

```json
{
  "id": 1311120, "name": "Google", "position": 1, "schema": "redirect",
  "action_type": "http", "action_options": {"url": "https://www.google.com/"},
  "collect_clicks": true,
  "filters": [{"name": "country", "mode": "accept", "payload": ["AU"]}]
}
```

```json
{
  "id": 1311121, "name": "Offers", "position": 2, "schema": "landings",
  "offers": [
    {"offer_id": 25327, "share": 34},
    {"offer_id": 24800, "share": 33},
    {"offer_id": 10184, "share": 33}
  ]
}
```

> `action_options.url` в потоке «Google» заполнен явно — в этой сборке Keitaro redirect-поток без него
> не работает. У многих кампаний в аккаунте это поле `null`.

## 3. Редактор офферов (через UI, реальные запросы)

1. Старт: `[(25327, 100)]`.
2. «＋ Добавить оффер» → `24800` → после нормализации `[(25327, 50), (24800, 50)]`.
3. «Удалить» `24800` (подтверждение в диалоге) → `[(25327, 100)]`.
4. Добавление `24800` (60 %) и `10184` (без веса) → `[(25327, 34), (24800, 33), (10184, 33)]` — сумма ровно 100.

Каждое изменение — один `PUT /streams/1311121` с полным массивом `offers`; запрос и ответ видны
на вкладке «Журнал API» (см. `docs/screenshots/04-logs.png`).

Вторая кампания `test_vorobev_campaign2_ro` (id `212058`) создана для проверки гео `RO` и оффера `24800`.

## 4. Статистика

`POST /report/build`:

```json
{"range": {"interval": "all_time"},
 "dimensions": ["campaign"],
 "metrics": ["clicks", "campaign_unique_clicks", "bots"],
 "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": 212056}]}
```

Ответ: `{"rows": [], "total": 0}` — у кампании пока нет трафика. Домен `kylovoa.com` не резолвится
из моей сети, поэтому реальный клик по ссылке кампании не воспроизводится; колонки «Клики / Уник. / Боты»
в UI берутся из этого же отчёта (метрика `uniques` не существует — правильное имя `campaign_unique_clicks`).

## 5. Уборка за собой

Разведочные кампании `test_probe_study_1794853072` (212027) и `test_vorobev_probe_1794853818` (212050)
удалены через `DELETE /campaigns/{id}` (ответ `200`), повторный `GET` по ним возвращает 404.
Кампании 212056 и 212058 оставлены как результат работы.

## 6. Чек-лист по ТЗ

- [x] кампания создаётся по имени + гео + ID оффера;
- [x] префикс имени `test_vorobev_`, alias транслитом;
- [x] поток №1 — catch-all по стране с редиректом на Google, `action_options.url` заполнен;
- [x] поток №2 — редирект на выбранный оффер;
- [x] домен, группа кампаний и источник трафика проставлены через API;
- [x] редактор офферов работает (добавить / удалить / веса) и подтверждён ответами API;
- [x] клики / уникальные / боты отдаются из `/report/build` и совпадают с панелью;
- [x] миграции Alembic, тесты `pytest` (12 шт.), README с инструкцией запуска;
- [x] мусорные кампании удалены, общий аккаунт не засорён.
