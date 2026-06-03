"""测试 collector.py 中的纯函数"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from collector import (
    _rule_based_briefing,
    clean_summary,
    emoji_for_category,
    gen_signature,
    is_duplicate,
    is_within_lookback,
    parse_cn_time,
    score_item,
)


# ── score_item ──
def test_score_item_with_high_priority_keyword():
    item = {"title": "跨境电商出口退税新政发布", "category": "industry"}
    keywords = {"high_priority": ["跨境电商", "出口退税"], "medium_priority": []}
    s = score_item(item, keywords)
    assert s == 6  # 两个 high 各 3 分
    assert item["_score"] == 6
    assert "跨境电商" in item["_matched_kws"]
    assert "出口退税" in item["_matched_kws"]


def test_score_item_with_policy_bonus():
    item = {"title": "外贸企业补贴", "category": "policy"}
    keywords = {"high_priority": ["外贸"], "medium_priority": []}
    s = score_item(item, keywords)
    assert s == 5  # 1 个 high (3) + policy bonus (2)


def test_score_item_below_threshold():
    item = {"title": "供应链优化方案", "category": "industry"}
    keywords = {"high_priority": ["跨境电商"], "medium_priority": ["供应链"]}
    s = score_item(item, keywords)
    assert s == 1  # 1 个 medium = 1


def test_score_item_no_match():
    item = {"title": "今天天气真好", "category": "industry"}
    keywords = {"high_priority": ["跨境电商"], "medium_priority": ["物流"]}
    assert score_item(item, keywords) == 0


# ── parse_cn_time ──
def test_parse_hours_ago():
    result = parse_cn_time("3小时前")
    expected = (datetime.now() - timedelta(hours=3))
    assert result is not None
    assert abs(datetime(*result[:6]).timestamp() - expected.timestamp()) < 5


def test_parse_minutes_ago():
    result = parse_cn_time("30分钟前")
    expected = (datetime.now() - timedelta(minutes=30))
    assert result is not None
    assert abs(datetime(*result[:6]).timestamp() - expected.timestamp()) < 5


def test_parse_yesterday():
    result = parse_cn_time("昨天")
    expected = datetime.now() - timedelta(days=1)
    assert result is not None
    assert result[:3] == expected.timetuple()[:3]


def test_parse_standard_date():
    result = parse_cn_time("2026-05-20")
    assert result is not None
    assert result[:3] == (2026, 5, 20)


def test_parse_month_day():
    result = parse_cn_time("05-20")
    now = datetime.now()
    assert result is not None
    assert result[1:3] == (5, 20)


def test_parse_chinese_date():
    result = parse_cn_time("2026年05月20日")
    assert result is not None
    assert result[:3] == (2026, 5, 20)


def test_parse_empty_string():
    assert parse_cn_time("") is None
    assert parse_cn_time(None) is None


def test_parse_unrecognized():
    assert parse_cn_time("刚刚发布") is None


# ── clean_summary ──
def test_clean_summary_normal():
    text = "国务院发布跨境电商新政，支持海外仓建设，推动外贸高质量发展。"
    assert clean_summary(text) == text


def test_clean_summary_with_html():
    text = "<p>国务院发布<strong>跨境电商</strong>新政</p>"
    result = clean_summary(text)
    assert "<" not in result
    assert "跨境电商" in result


def test_clean_summary_truncation():
    text = "A" * 200
    result = clean_summary(text)
    assert len(result) == 123  # 120 chars + "..."
    assert result.endswith("...")


def test_clean_summary_empty():
    assert clean_summary("") == ""
    assert clean_summary(None) == ""


# ── emoji_for_category ──
def test_emoji_policy():
    assert "🔴" in emoji_for_category("policy")


def test_emoji_industry():
    assert "🟠" in emoji_for_category("industry")


def test_emoji_global():
    assert "🟢" in emoji_for_category("global")


def test_emoji_unknown():
    assert emoji_for_category("unknown") == "⚪"


# ── gen_signature ──
def test_gen_signature():
    sig = gen_signature(1234567890, "test-secret")
    assert isinstance(sig, str)
    assert len(sig) > 0
    # 相同输入应产生相同输出
    assert sig == gen_signature(1234567890, "test-secret")
    # 不同输入应产生不同输出
    assert sig != gen_signature(1234567891, "test-secret")


# ── is_within_lookback ──
def test_within_lookback_recent():
    now = datetime.now(timezone.utc)
    ts = now.timetuple()
    assert is_within_lookback(ts, 24) is True


def test_within_lookback_old():
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).timetuple()
    assert is_within_lookback(old, 24) is False


def test_within_lookback_midnight_adjustment():
    # 午夜零点 (0,0,0) 应被调整为 23:59:59，视为当天内
    midnight = (2026, 6, 3, 0, 0, 0, 0, 0, 0)  # struct_time
    # 今天就是 2026-06-03，所以应该视为在 lookback 内
    assert is_within_lookback(midnight, 24) is True


def test_within_lookback_none():
    assert is_within_lookback(None, 24) is True


# ── is_duplicate ──
def test_is_duplicate_returns_false_for_new_url():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_items ("
        "url_hash TEXT PRIMARY KEY, source TEXT NOT NULL, first_seen TEXT NOT NULL)"
    )
    assert is_duplicate(conn, "https://example.com/news/1") is False
    conn.close()


def test_is_duplicate_returns_true_after_insert():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_items ("
        "url_hash TEXT PRIMARY KEY, source TEXT NOT NULL, first_seen TEXT NOT NULL)"
    )
    url = "https://example.com/news/1"
    conn.execute(
        "INSERT INTO seen_items VALUES (?, ?, ?)",
        (hashlib.sha256(url.encode()).hexdigest(), "test", datetime.now().isoformat()),
    )
    conn.commit()
    assert is_duplicate(conn, url) is True
    conn.close()


# ── _rule_based_briefing ──
def test_rule_based_briefing():
    items = [
        {"source": "国务院", "title": "跨境电商出口退税新政发布", "category": "policy"},
        {"source": "商务部", "title": "外贸企业补贴政策", "category": "policy"},
        {"source": "亿邦动力", "title": "海外仓建设加速", "category": "industry"},
    ]
    result = _rule_based_briefing(items)
    assert "3 条" in result
    assert "政策类 2 条" in result
    assert "国务院" in result
    assert "商务部" in result


def test_rule_based_briefing_empty():
    assert "共采集 0 条" in _rule_based_briefing([])
