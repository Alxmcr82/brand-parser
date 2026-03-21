# Brand Parser API

FastAPI-сервис для извлечения описания бренда и ссылок на соцсети с любого сайта.

## Возможности

- **AI-режим** — Claude анализирует HTML и умно находит описание и соцсети
- **Regex-режим** — быстрый разбор без API-ключа (мета-теги + регулярные выражения)
- **Batch-эндпоинт** — обработка до 20 URL за один запрос
- **Fallback** — если AI недоступен, автоматически переключается на regex

## Быстрый старт

### 1. Клонируй и настрой

```bash
cp .env.example .env
# Открой .env и вставь свой ANTHROPIC_API_KEY
```

### 2. Запусти через Docker Compose

```bash
docker compose up --build -d
```

Сервис будет доступен на `http://localhost:8000`

### 3. Документация API

Открой в браузере: `http://localhost:8000/docs`

---

## Эндпоинты

### `POST /parse` — разобрать один URL

```bash
curl -X POST http://localhost:8000/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://apple.com", "use_ai": true}'
```

Ответ:
```json
{
  "url": "https://apple.com",
  "description": "Apple — компания, которая разрабатывает iPhone, iPad, Mac...",
  "socials": [
    {"platform": "Instagram", "url": "https://instagram.com/apple"},
    {"platform": "YouTube",   "url": "https://youtube.com/@apple"}
  ],
  "method": "ai"
}
```

### `POST /parse/batch` — разобрать несколько URL (до 20)

```bash
curl -X POST "http://localhost:8000/parse/batch?use_ai=true" \
  -H "Content-Type: application/json" \
  -d '["https://apple.com", "https://nike.com"]'
```

### `GET /health` — проверка работоспособности

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Параметры запроса

| Поле     | Тип     | По умолчанию | Описание                              |
|----------|---------|--------------|---------------------------------------|
| `url`    | string  | —            | URL сайта для анализа                 |
| `use_ai` | boolean | `true`       | Использовать Claude AI для анализа    |

## Режимы работы

| Режим    | Когда используется                            | Точность |
|----------|-----------------------------------------------|----------|
| `ai`     | `use_ai=true` + задан `ANTHROPIC_API_KEY`     | Высокая  |
| `regex`  | `use_ai=false` или нет API-ключа              | Средняя  |
| `error`  | Batch: URL недоступен                         | —        |

---

## Локальная разработка (без Docker)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --reload
```

## Структура проекта

```
brand-parser/
├── app/
│   └── main.py          # FastAPI приложение
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```
