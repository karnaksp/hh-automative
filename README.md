# hh-automative

Контролируемая Selenium-автоматизация для hh.ru: логин, поиск вакансий, отклики, локальная история и диагностика ошибок.

Документация GitHub Pages: https://karnaksp.github.io/hh-automative/

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Заполните `HH_USERNAME` и `HH_PASSWORD` в `.env`. Chrome должен быть установлен локально; Selenium Manager сам подберет ChromeDriver для актуальных версий Selenium.

## Команды

```bash
python -m hh_automative check-config
python -m hh_automative login
python -m hh_automative run --profile data-engineer --limit 10 --dry-run
python -m hh_automative stats
python -m hh_automative analytics
python -m hh_automative dashboard --db-path data/hh_automative.duckdb
```

`--dry-run` открывает вакансии и пишет их в историю, но не отправляет отклики. Для отладки браузера используйте `--debug-browser`, тогда окно Chrome останется открытым.

## Конфигурация

- `.env` хранит секреты, пути и значения по умолчанию.
- `config/search_profiles.json` хранит профили поиска.
  Текущий профиль по умолчанию заточен под `data-engineer`: базовый поисковый текст — `data engineer`, а чистота выдачи держится через `exclude`, чтобы не сужать поиск до единичных вакансий и при этом отсекать `data analyst`, `data scientist`, `ml engineer`, `product analyst`.
- `config/resumes.json` хранит соответствие ключевых слов и ID резюме.
- `data/hh_automative.sqlite3` хранит историю обработанных вакансий.
- `data/hh_automative.duckdb` хранит аналитические события запусков и копию app-логов.
- `data/resume-texts` хранит локальные текстовые снимки резюме по каждому `resume_code` (по одному файлу на резюме).
- `reports/diagnostics` содержит screenshot и HTML при ошибках Selenium-flow.

## Аналитическое логирование

DuckDB содержит две таблицы:

- `automation_events`: `run_id`, событие, профиль, vacancy id/url, статус, ошибка, diagnostics path, metadata JSON.
- `app_logs`: обычные Python-логи с уровнем, logger name, сообщением и местом вызова.

Быстро посмотреть сводку:

```bash
python -m hh_automative analytics
```

Пример ручного запроса:

```bash
.venv/bin/python - <<'PY'
import duckdb
con = duckdb.connect("data/hh_automative.duckdb", read_only=True)
print(con.sql("select * from automation_events order by event_ts desc limit 20").df())
PY
```

## Локальная панель

Панель читает `data/hh_automative.duckdb` и показывает запуски, статусы вакансий, ошибки, diagnostics paths и app logs.

```bash
.venv/bin/hh-automative dashboard --db-path data/hh_automative.duckdb --port 8501 --host 127.0.0.1
```

По умолчанию команда старается занять указанный порт: если он занят, процессы на этом порту закрываются, и админка стартует снова на нём.
Если после очистки порт не освободился, а `--auto-port` включён, будет выбран ближайший свободный порт.
Если в браузере видишь «чужой» Streamlit на 8501, это обычно конфликт порта. Проверь занятость: `lsof -n -P -iTCP:8501 -sTCP:LISTEN`.
Если не хочешь завершать чужие процессы автоматически, добавь `--no-kill-existing`.
Если нужно именно перехватить 8501 именно под hh-automative, скинь занятое вручную или просто перезапусти:

```bash
.venv/bin/hh-automative dashboard --kill-existing --port 8501
```

Запуск на другом порту: `.venv/bin/hh-automative dashboard --port 8502`.

Важно: запуск `dashboard` с системным `python` не будет работать без зависимости `streamlit` в этом окружении.  
Всегда запускай через `.venv` проекта:
`.venv/bin/python -m hh_automative dashboard ...`

Если хочешь перезапустить админку на том же порту 8501:

```bash
.venv/bin/hh-automative dashboard --no-auto-port --port 8501
```

Если хочешь строго оставить `8501`, используй `--no-auto-port`:

```bash
.venv/bin/hh-automative dashboard --no-auto-port --port 8501
```

Альтернатива (если нужно обойти конфликт порта) — запусти на другом порту:

```bash
.venv/bin/hh-automative dashboard --port 8502
```

Если нужно открыть другой файл DuckDB, укажите путь в sidebar.

## LLM Для Писем И Опросов

Проект готовит строгие JSON-промпты для сопроводительных писем и вопросов работодателя. Контракты лежат в `hh_automative.ai_assist`:

- `build_cover_letter_prompt(...)` просит вернуть JSON с `cover_letter`.
- `build_questionnaire_prompt(...)` просит вернуть JSON с `answers`, `selected_options` и `needs_human_review`.

Основной путь — API-провайдеры (`gemini`, `mistral`, `openrouter`, `ollama`).

Пример `.env` для Gemini с fallback на Mistral, OpenRouter и локальный Ollama:

```env
HH_USE_LLM=true
HH_USE_LLM_COVER_LETTER=true
HH_USE_LLM_QUESTIONNAIRE=true
HH_LLM_PROVIDER=gemini
HH_LLM_FALLBACKS=mistral,openrouter,ollama
GEMINI_API_KEY=...
MISTRAL_API_KEY=...
OPENROUTER_API_KEY=...
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1:8b
```

Поддерживаемые API-провайдеры:

- `gemini`
- `mistral`
- `openrouter`
- `ollama` для локальной модели без API-ключа

Основной текст резюме для LLM брался из локального кэша `HH_RESUME_TEXT_CACHE_DIR` (по умолчанию `data/resume-texts`) и синхронизируется при каждом запуске `run` для всех резюме из `config/resumes.json`.
`HH_RESUME_TEXT_PATH` оставлен как fallback/legacy-источник и используется если локального кэша для конкретного резюме нет.

Для полностью локального режима:

```env
HH_USE_LLM=true
HH_USE_LLM_COVER_LETTER=true
HH_USE_LLM_QUESTIONNAIRE=true
HH_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1:8b
```

Если вакансия просит сопроводительное письмо, бот отправляет в выбранный LLM текст вакансии и резюме, получает JSON с `cover_letter` и заполняет поле письма.  
Если есть опрос, бот отправляет текст вакансии, резюме и описания вопросов, получает структурный JSON `answers` с `answer_text`/`selected_options` и раскладывает его по полям (`textarea`, `input`, `select`, `radio`, `checkbox`) — для кнопок делает клик по найденным опциям.

Сформировать prompt для сопроводительного письма без API-вызова:

```bash
python -m hh_automative llm-cover-letter path/to/vacancy.txt
```

Отправить prompt в настроенный API-провайдер:

```bash
python -m hh_automative llm-cover-letter path/to/vacancy.txt --ask
```

Каждый prompt/ответ пишется в DuckDB-таблицу `ai_assist_events` и виден в панели.

Сформировать prompt для опроса работодателя без API-вызова:

```bash
python -m hh_automative llm-questionnaire path/to/vacancy.txt path/to/questions.json
```

Отправить prompt в настроенный API-провайдер:

```bash
python -m hh_automative llm-questionnaire path/to/vacancy.txt path/to/questions.json --ask
```

`questions.json` должен быть массивом:

```json
[
  {
    "question_id": "q1",
    "label": "Есть ли опыт SQL?",
    "input_type": "radio",
    "options": ["Да", "Нет"],
    "required": true
  }
]
```

## Проверки

```bash
python -m scripts.check
```

Команда запускает `ruff check`, `pytest` и `python -m compileall`.
Компиляция ограничена исходниками проекта, чтобы локальная `.venv` не попадала в проверку.
