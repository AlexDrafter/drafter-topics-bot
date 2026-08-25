# drafter-topics-bot

Serverless content pipeline для Telegram-канала **[@Drafter_community](https://t.me/Drafter_community)** — комьюнити фаундеров и инвесторов DRAFTER АКСЕЛЕРАТОР.

Каждое утро в 10:00 МСК бот присылает владельцу канала (`@AlexDrafter` в личку через `@drafter_news_bot`) 5 тем для поста. Владелец отвечает номером — Claude пишет пост, HuggingFace генерит обложку, всё склеивается в готовое сообщение и уходит в канал после подтверждения.

Живёт полностью в GitHub Actions — без сервера, без локального мака.

---

## Как это работает

```
       10:00 MSK
    ┌────────────┐             ┌──────────────────┐
    │ GH Actions │───tick───▶  │   poll.py        │
    │  cron+/5m  │             │   (state.json)   │
    └────────────┘             └──────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
        ▼                          ▼                          ▼
  ┌────────────┐            ┌────────────┐            ┌────────────┐
  │ send_topics│            │ draft_post │            │publish_post│
  │            │            │            │            │            │
  │ 5 из ~50   │            │ Claude API │            │ TG channel │
  │ Bot API DM │            │ HF FLUX    │            │ (via LP)   │
  │            │            │ git push   │            │            │
  │            │            │ Bot API DM │            │            │
  └────────────┘            └────────────┘            └────────────┘
        │                          │                          │
        └────── state.json ────────┴────── state.json ─────────┘
                (committed back to this repo by the workflow)
```

**Внешние зависимости:**

- **Telegram Bot API** — доставка тем/превью/постов.
- **Anthropic Claude API** — написание текста поста и промпта для обложки.
- **HuggingFace Inference Providers** (FLUX.1-schnell) — генерация обложки.
- **[alexsmirnov158-coder/drafter-covers](https://github.com/alexsmirnov158-coder/drafter-covers)** — публичный репо, куда пушим PNG обложки, чтобы Telegram отрендерил их как link-preview.

---

## Стейт-машина

Всё состояние в [`state.json`](./state.json). Каждый tick воркфлоу читает его, при необходимости меняет и коммитит обратно в этот же репо.

| phase | что означает | как выходит |
|---|---|---|
| `idle` | Между днями, ждём 10:00 UTC+3 | На tick после 07:00 UTC + `last_topics_date != today` → `send_topics` → `waiting_topic` |
| `waiting_topic` | Темы отправлены в личку, ждём выбор | Юзер пишет номер/название → `parse_user_topic` → `drafting` |
| `drafting` | Пишем пост + генерим обложку | Claude → HF → git push → preview в личку → `awaiting_approval` (или на ошибке → обратно в `waiting_topic`) |
| `awaiting_approval` | Превью в личке, ждём approve | Юзер пишет `публикуй`/`ок` → `publishing`. Или `переделай` → `drafting`. |
| `publishing` | Отправляем в канал | `publish_post` → `cooldown` |
| `cooldown` | Пост опубликован, ждём завтра | На первом tick следующего дня → `idle` (в `main`) |

Пороги времени, токены и категории — константы в [`poll.py`](./poll.py).

---

## Секреты

Хранятся как GitHub Actions secrets в этом репо (`Settings → Secrets and variables → Actions`):

| Secret | Назначение | Где получить |
|---|---|---|
| `BOT_TOKEN` | Telegram Bot API токен `@drafter_news_bot` | `@BotFather` → `/mybots` |
| `CHAT_ID` | Chat ID владельца (для DM) | `curl getUpdates` после `/start` боту |
| `ANTHROPIC_KEY` | Claude API key | https://console.anthropic.com/settings/keys |
| `HF_TOKEN` | HuggingFace token с правом Inference | https://huggingface.co/settings/tokens |
| `GH_PAT` | Classic PAT со scope `repo` (для push в `drafter-covers`) | https://github.com/settings/tokens/new?scopes=repo,workflow |

Ротация: сгенерировать новый ключ у провайдера → обновить одноимённый secret через UI Settings → Secrets.

---

## Workflows

- **[poll.yml](.github/workflows/poll.yml)** — cron `*/5 * * * *`. Каждый tick запускает `poll.py`. Делает ровно один шаг стейт-машины.
- **[tests.yml](.github/workflows/tests.yml)** — гоняет `pytest tests/` на каждый push в `main`. Никаких сетевых вызовов — только чистые функции.

Ручной запуск pipeline: `Actions → Poll loop → Run workflow`. Или через API:

```bash
PAT=$(cat ~/.claude/secrets/github-pat)
curl -X POST -H "Authorization: Bearer $PAT" \
     -H "Accept: application/vnd.github+json" \
     https://api.github.com/repos/alexsmirnov158-coder/drafter-topics-bot/actions/workflows/poll.yml/dispatches \
     -d '{"ref":"main"}'
```

---

## Формат поста

Зафиксирован 28.05.2026, эталон — пост #903 в канале. Все посты пишутся Claude по этому шаблону:

```html
<b>Заголовок одной строкой</b>

Лид 1–2 предложения — ставит проблему.

Контекст 1 абзац — почему это важно.

<b>Явная польза для читателя</b> одним абзацем.

<b>N пунктов, которые …</b>

<blockquote expandable>
1. Пункт первый — конкретика с цифрами.
2. Пункт второй ...
...
</blockquote>

Афоризм-закрытие.

Сохрани пост / Перешли тому, кто …
```

Целевая длина видимого текста 1500–2500 символов (лимит `sendMessage` 4096, обложка идёт как link-preview из публичного репо).

---

## Тесты

24 unit-теста для pure-функций (`parse_user_topic`, `pick_topics`, `format_topics_message`, `should_send_topics`, `classify_reply`, `load_bank`):

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

Что **не** покрыто тестами и почему:

- **Сетевые вызовы** (Telegram, Claude, HF, git push) — smoke-тестятся live-запуском workflow. Мокать все три провайдера дороже, чем один раз глазами глянуть в личку.
- **`main()`** — оркестрация, все зависимости уже покрыты по кускам.
- **`draft_post`, `publish_post`** — тонкие обёртки над сетью.

CI гоняет тесты на каждый push через [`tests.yml`](.github/workflows/tests.yml).

---

## Диагностика

**Пост не пришёл утром.** Открой `Actions → Poll loop` — посмотри последний scheduled run. Если давно не было — cron `*/5` иногда просыпается медленно (известное поведение бесплатного GitHub Actions на public репо). Дёрни вручную: `Actions → Poll loop → Run workflow`.

**Workflow упал.** Открой конкретный run → job `tick` → step `Run python3 poll.py`. Traceback последнего exception автоматически отправляется в личку через `⚠️` сообщение (см. `main()`).

**HF возвращает 429.** Rate-limit на IP runner'ов. `poll.py` пробует 3 модели подряд (FLUX.1-schnell → FLUX.1-schnell retry 30с → FLUX.1-dev → SDXL); если всё зарезано — упадёт в exception, юзер получит traceback. Обычно отпускает через час.

**Git push в drafter-covers падает.** Проверь `GH_PAT` — истёк или отозван. Ротируй по инструкции выше.

**State рассинхронизировался.** Прямо отредактируй `state.json` через web-UI GitHub или через `Contents API`. Workflow подхватит изменения на следующем tick.

---

## Связанные ресурсы

- Канал: [t.me/Drafter_community](https://t.me/Drafter_community)
- Бот: `@drafter_news_bot` (только вручную через BotFather, не в этом репо)
- Обложки: [alexsmirnov158-coder/drafter-covers](https://github.com/alexsmirnov158-coder/drafter-covers) (public, raw URL как link-preview)
- Основной проект: [alexsmirnov158-coder/drafter-accelerator](https://github.com/alexsmirnov158-coder/drafter-accelerator) (приватный, backend/frontend продукта)
