"""
Pure-function tests for poll.py.

Only functions without network/filesystem side effects are covered here.
Sends to Telegram, Claude, HF and git-push are covered manually by a live run
in production (workflow_dispatch), not in CI.
"""
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Make repo root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import poll


# ---------------------------------------------------------------- parse_user_topic

SAMPLE_TOPICS = """☀️ Темы на сегодня:

1. Финансы стартапа — Юника на пальцах: 5 цифр
2. Питч и инвестиции — Term Sheet: что смотреть
3. Юр.чек-лист — Договор с фрилансером
4. Команда и найм — Первые 10 сотрудников
5. Разборы ошибок — 5 провалов в MVP

Ответь номером или своей идеей.
"""


def test_parse_by_number():
    assert poll.parse_user_topic("1", SAMPLE_TOPICS) == "Финансы стартапа — Юника на пальцах: 5 цифр"
    assert poll.parse_user_topic("3", SAMPLE_TOPICS) == "Юр.чек-лист — Договор с фрилансером"
    assert poll.parse_user_topic("5", SAMPLE_TOPICS) == "Разборы ошибок — 5 провалов в MVP"


def test_parse_by_number_with_whitespace():
    assert poll.parse_user_topic("  2 ", SAMPLE_TOPICS) == "Питч и инвестиции — Term Sheet: что смотреть"


def test_parse_by_number_out_of_range():
    assert poll.parse_user_topic("9", SAMPLE_TOPICS) is None
    assert poll.parse_user_topic("0", SAMPLE_TOPICS) is None


def test_parse_full_line_match():
    assert (
        poll.parse_user_topic("Финансы стартапа — Юника на пальцах: 5 цифр", SAMPLE_TOPICS)
        == "Финансы стартапа — Юника на пальцах: 5 цифр"
    )


def test_parse_freeform_idea_with_dash():
    # User writes their own topic containing " — "; treated as-is
    idea = "Продукт и стратегия — Когда делать pivot"
    assert poll.parse_user_topic(idea, SAMPLE_TOPICS) == idea


def test_parse_short_freeform_rejected():
    # Too short to be a topic
    assert poll.parse_user_topic("hi", SAMPLE_TOPICS) is None
    assert poll.parse_user_topic("да", SAMPLE_TOPICS) is None


def test_parse_empty_returns_none():
    assert poll.parse_user_topic("", SAMPLE_TOPICS) is None
    assert poll.parse_user_topic("   ", SAMPLE_TOPICS) is None
    assert poll.parse_user_topic(None, SAMPLE_TOPICS) is None


# ---------------------------------------------------------------- pick_topics

FIXTURE_ROWS = [
    ("A", "a1"), ("A", "a2"), ("A", "a3"),
    ("B", "b1"), ("B", "b2"),
    ("C", "c1"),
    ("D", "d1"), ("D", "d2"),
    ("E", "e1"),
    ("F", "f1"),
]


def test_pick_topics_default_count():
    picked = poll.pick_topics(FIXTURE_ROWS, rng=random.Random(42))
    assert len(picked) == 5


def test_pick_topics_prefers_distinct_categories():
    picked = poll.pick_topics(FIXTURE_ROWS, count=5, rng=random.Random(42))
    cats = [c for c, _ in picked]
    # With 6 distinct categories and count=5, all picked must be distinct
    assert len(set(cats)) == 5


def test_pick_topics_falls_back_when_not_enough_categories():
    limited = [("A", "a1"), ("A", "a2"), ("A", "a3"), ("B", "b1")]
    picked = poll.pick_topics(limited, count=4, rng=random.Random(0))
    assert len(picked) == 4
    # Two categories only → some duplicates by category
    cats = [c for c, _ in picked]
    assert set(cats) == {"A", "B"}


def test_pick_topics_deterministic_with_seed():
    a = poll.pick_topics(FIXTURE_ROWS, rng=random.Random(123))
    b = poll.pick_topics(FIXTURE_ROWS, rng=random.Random(123))
    assert a == b


# ---------------------------------------------------------------- format_topics_message

def test_format_topics_message_structure():
    picked = [("Cat1", "Title1"), ("Cat2", "Title2")]
    text = poll.format_topics_message(picked)
    lines = text.splitlines()
    assert lines[0] == "☀️ Темы на сегодня:"
    assert lines[2] == "1. Cat1 — Title1"
    assert lines[3] == "2. Cat2 — Title2"
    assert "Ответь номером или своей идеей." in lines


def test_format_topics_message_round_trip_with_parse():
    picked = [("Финансы стартапа", "Юника"), ("Юр.чек-лист", "NDA")]
    text = poll.format_topics_message(picked)
    assert poll.parse_user_topic("1", text) == "Финансы стартапа — Юника"
    assert poll.parse_user_topic("2", text) == "Юр.чек-лист — NDA"


# ---------------------------------------------------------------- should_send_topics

def _now(hour_utc, day=15):
    return datetime(2026, 8, day, hour_utc, 0, 0, tzinfo=timezone.utc)


def test_should_send_when_idle_and_after_hour():
    state = {"phase": "idle", "last_topics_date": "2026-08-14"}
    assert poll.should_send_topics(state, _now(7), hour_utc=7)
    assert poll.should_send_topics(state, _now(10), hour_utc=7)


def test_should_not_send_before_hour():
    state = {"phase": "idle", "last_topics_date": "2026-08-14"}
    assert not poll.should_send_topics(state, _now(6), hour_utc=7)


def test_should_not_send_if_already_sent_today():
    state = {"phase": "waiting_topic", "last_topics_date": "2026-08-15"}
    assert not poll.should_send_topics(state, _now(10), hour_utc=7)


def test_should_not_send_when_drafting():
    state = {"phase": "drafting", "last_topics_date": "2026-08-14"}
    assert not poll.should_send_topics(state, _now(10), hour_utc=7)


def test_should_send_when_cooldown_and_new_day():
    state = {"phase": "cooldown", "last_topics_date": "2026-08-14"}
    assert poll.should_send_topics(state, _now(8), hour_utc=7)


# ---------------------------------------------------------------- classify_reply

def test_classify_approve_variants():
    for token in ["публикуй", "ОК", " Ok ", "+", "да", "yes", "PUBLISH"]:
        assert poll.classify_reply(token, "awaiting_approval") == "approve"


def test_classify_regen_variants():
    for token in ["переделай", "Переделать", "REGEN"]:
        assert poll.classify_reply(token, "awaiting_approval") == "regen"


def test_classify_unrelated_in_awaiting_returns_none():
    assert poll.classify_reply("что-нибудь", "awaiting_approval") is None


def test_classify_in_waiting_topic_returns_topic():
    assert poll.classify_reply("1", "waiting_topic") == "topic"
    assert poll.classify_reply("моя своя идея — тема разбора", "waiting_topic") == "topic"


def test_classify_in_other_phases_returns_none():
    assert poll.classify_reply("что угодно", "idle") is None
    assert poll.classify_reply("что угодно", "cooldown") is None
    assert poll.classify_reply("", "waiting_topic") is None


# ---------------------------------------------------------------- load_bank (integration with real file)

def test_load_bank_reads_repo_file():
    rows = poll.load_bank()
    assert len(rows) >= 30
    # each row is a (category, title) tuple, both non-empty
    for cat, title in rows:
        assert cat
        assert title
    # sanity: contains at least one known category
    cats = {c for c, _ in rows}
    assert "Финансы стартапа" in cats or "Питч и инвестиции" in cats
