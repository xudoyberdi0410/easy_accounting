# Finance Bot — Database Design

## Overview

PostgreSQL schema для Telegram-бота личных финансов. Поддерживает мультивалютность, иерархические категории, бюджеты, теги, повторяющиеся транзакции и полную историю курсов валют.

---

## ER Diagram (text)

```
users
 ├── accounts (1:N)
 │    └── balance  ← auto-updated via trigger
 ├── categories (1:N, nullable = системная)
 │    └── categories (self-ref parent_id)
 ├── transactions (1:N)
 │    ├── → accounts (account_id, to_account_id)
 │    ├── → categories
 │    └── → transaction_tags → tags
 ├── budgets (1:N)
 │    └── → categories
 ├── recurring_transactions (1:N)
 │    ├── → accounts
 │    └── → categories
 └── tags (1:N)

exchange_rates  (standalone, history log)
```

---

## Tables

### `users`
Основная таблица пользователей. Идентификация по `telegram_id`.

| Колонка           | Тип          | Описание                        |
|-------------------|--------------|---------------------------------|
| id                | BIGSERIAL PK |                                 |
| telegram_id       | BIGINT UNIQUE| ID пользователя в Telegram      |
| username          | VARCHAR(64)  | @username (может быть NULL)     |
| language_code     | VARCHAR(8)   | Язык интерфейса, default `ru`   |
| default_currency  | VARCHAR(3)   | Базовая валюта, default `USD`   |
| is_active         | BOOLEAN      | Бан / деактивация               |
| created_at        | TIMESTAMPTZ  |                                 |
| updated_at        | TIMESTAMPTZ  |                                 |

---

### `accounts`
Счета пользователя (наличные, карта, крипто и т.д.).

| Колонка    | Тип           | Описание                              |
|------------|---------------|---------------------------------------|
| id         | BIGSERIAL PK  |                                       |
| user_id    | FK → users    |                                       |
| name       | VARCHAR(64)   | Название счёта                        |
| currency   | VARCHAR(3)    | Валюта счёта                          |
| balance    | NUMERIC(18,2) | Текущий баланс (денормализован, см. триггер) |
| type       | account_type  | `cash / card / savings / crypto / other` |
| is_archive | BOOLEAN       | Архивный счёт (скрыт, но не удалён)   |

**Важно:** `balance` обновляется автоматически триггером `trg_transactions_balance` при каждой вставке/soft-delete транзакции. Не обновляйте вручную без синхронизации с транзакциями.

---

### `categories`
Системные и пользовательские категории расходов/доходов.

| Колонка   | Тип           | Описание                                  |
|-----------|---------------|-------------------------------------------|
| id        | BIGSERIAL PK  |                                           |
| user_id   | FK → users    | NULL = системная категория (для всех)     |
| name      | VARCHAR(64)   |                                           |
| type      | category_type | `income` / `expense`                      |
| icon      | VARCHAR(32)   | Emoji или код иконки                      |
| parent_id | FK → self     | Родительская категория (иерархия 2 уровня)|
| is_default| BOOLEAN       | Используется при авто-категоризации       |

**Constraints:** `UNIQUE(user_id, name, type)` — нельзя создать две одинаковые категории одного типа.

---

### `transactions`
Центральная таблица. Все операции: доходы, расходы, переводы.

| Колонка       | Тип                | Описание                                         |
|---------------|--------------------|--------------------------------------------------|
| id            | BIGSERIAL PK       |                                                  |
| user_id       | FK → users         |                                                  |
| account_id    | FK → accounts      | Счёт-источник                                    |
| category_id   | FK → categories    | Может быть NULL (без категории)                  |
| to_account_id | FK → accounts      | Счёт-назначение (только для `transfer`)          |
| amount        | NUMERIC(18,2)      | Сумма в валюте счёта                             |
| amount_base   | NUMERIC(18,2)      | Сумма в `default_currency` пользователя          |
| currency      | VARCHAR(3)         | Валюта транзакции                                |
| exchange_rate | NUMERIC(18,6)      | Курс на момент транзакции                        |
| type          | transaction_type   | `income / expense / transfer`                    |
| note          | TEXT               | Произвольный комментарий                         |
| source        | transaction_source | `manual / screenshot / voice / forwarded_message`|
| occurred_at   | TIMESTAMPTZ        | Фактическое время (может редактироваться)        |
| created_at    | TIMESTAMPTZ        | Время записи в БД                                |
| deleted_at    | TIMESTAMPTZ        | Soft delete (NULL = активна)                     |

**Constraints:**
- `CHECK (type != 'transfer' OR to_account_id IS NOT NULL)` — у перевода обязателен счёт назначения.

**Soft delete:** физически строки не удаляются. Все индексы содержат `WHERE deleted_at IS NULL`.

---

### `tags` и `transaction_tags`
Произвольные теги для группировки транзакций.

- `tags(user_id, name)` — UNIQUE, теги уникальны в пределах пользователя.
- `transaction_tags` — связующая таблица M:N.

---

### `budgets`
Лимиты расходов по категории за период.

| Колонка      | Тип           | Описание                                     |
|--------------|---------------|----------------------------------------------|
| id           | BIGSERIAL PK  |                                              |
| user_id      | FK → users    |                                              |
| category_id  | FK → categories| NULL = бюджет на все расходы                |
| limit_amount | NUMERIC(18,2) | Лимит суммы                                  |
| currency     | VARCHAR(3)    | Валюта лимита                                |
| period       | budget_period | `weekly / monthly / yearly`                  |
| period_start | DATE          | Начало периода                               |
| period_end   | DATE          | Конец периода (явный, `CHECK > period_start`)|

**Расчёт использования бюджета:**
```sql
SELECT b.limit_amount,
       COALESCE(SUM(t.amount_base), 0) AS spent
FROM budgets b
LEFT JOIN transactions t
       ON t.user_id     = b.user_id
      AND t.category_id = b.category_id
      AND t.type        = 'expense'
      AND t.occurred_at BETWEEN b.period_start AND b.period_end
      AND t.deleted_at IS NULL
WHERE b.id = $1
GROUP BY b.limit_amount;
```

---

### `recurring_transactions`
Шаблоны регулярных платежей (подписки, аренда и т.д.).

| Колонка     | Тип              | Описание                              |
|-------------|------------------|---------------------------------------|
| cron_expr   | VARCHAR(32)      | Cron-выражение: `'0 9 1 * *'`         |
| next_run_at | TIMESTAMPTZ      | Время следующего запуска              |
| is_active   | BOOLEAN          | Можно отключить без удаления          |

**Воркер** должен периодически запрашивать:
```sql
SELECT * FROM recurring_transactions
WHERE is_active = TRUE AND next_run_at <= NOW();
```
После создания транзакции обновить `next_run_at` по `cron_expr`.

---

### `exchange_rates`
История курсов валют. Хранит все загруженные значения.

| Колонка        | Описание                        |
|----------------|---------------------------------|
| base_currency  | Базовая валюта (напр. `USD`)    |
| quote_currency | Котируемая валюта (напр. `RUB`) |
| rate           | Курс                            |
| fetched_at     | Время загрузки                  |

**Нет UNIQUE** — каждая загрузка добавляет новую строку. Для получения актуального курса:
```sql
SELECT rate FROM exchange_rates
WHERE base_currency = 'USD' AND quote_currency = 'RUB'
ORDER BY fetched_at DESC
LIMIT 1;
```

---

## Trigger: `trg_transactions_balance`

Автоматически корректирует `accounts.balance` при:
- **INSERT** активной транзакции — применяет эффект
- **UPDATE** (soft delete, `deleted_at` становится NOT NULL) — откатывает эффект

Не срабатывает при прямом изменении `amount` — в этом случае нужно обновлять вручную или добавить обработку `UPDATE` с изменением суммы.

---

## Enums

| Тип                  | Значения                                              |
|----------------------|-------------------------------------------------------|
| `account_type`       | cash, card, savings, crypto, other                    |
| `transaction_type`   | income, expense, transfer                             |
| `transaction_source` | manual, screenshot, voice, forwarded_message          |
| `category_type`      | income, expense                                       |
| `budget_period`      | weekly, monthly, yearly                               |

---

## Indexes

| Индекс                        | Таблица               | Назначение                              |
|-------------------------------|-----------------------|-----------------------------------------|
| `idx_accounts_user`           | accounts              | Список счетов пользователя              |
| `idx_transactions_user`       | transactions          | Лента транзакций (partial: active)      |
| `idx_transactions_account`    | transactions          | Транзакции по счёту (partial: active)   |
| `idx_transactions_date`       | transactions          | Сортировка по дате (partial: active)    |
| `idx_transactions_type`       | transactions          | Фильтр по типу (partial: active)        |
| `idx_categories_user`         | categories            | Категории пользователя                  |
| `idx_budgets_user`            | budgets               | Бюджеты пользователя                    |
| `idx_exchange_rates_pair_time`| exchange_rates        | Актуальный курс по паре валют           |
| `idx_recurring_next_run`      | recurring_transactions| Воркер: задачи к запуску (partial: active) |

Все индексы по `transactions` — **partial** (`WHERE deleted_at IS NULL`), чтобы не индексировать удалённые записи.
