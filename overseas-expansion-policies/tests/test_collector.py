"""测试 collector.py 中的纯函数"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from collector import (
    _compile_keyword_patterns,
    _determine_run_mode,
    _mode_config,
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


# ── _determine_run_mode ──
def test_determine_monthly_on_first():
    assert _determine_run_mode(datetime(2026, 6, 1).date()) == "monthly"
    assert _determine_run_mode(datetime(2026, 1, 1).date()) == "monthly"


def test_determine_weekly_on_monday():
    # 2026-06-08 is a Monday
    assert _determine_run_mode(datetime(2026, 6, 8).date()) == "weekly"


def test_determine_daily_on_normal_days():
    # 2026-06-03 is a Wednesday
    assert _determine_run_mode(datetime(2026, 6, 3).date()) == "daily"
    assert _determine_run_mode(datetime(2026, 6, 5).date()) == "daily"  # Friday


def test_determine_monthly_over_weekly():
    """如果 1 号正好是周一，月度优先"""
    # 2026-06-01 is a Monday
    assert _determine_run_mode(datetime(2026, 6, 1).date()) == "monthly"


# ── _mode_config ──
def test_mode_config_daily():
    mc = _mode_config("daily")
    assert mc["label"] == "每日情报"
    assert mc["lookback"] is None
    assert mc["max_items"] is None


def test_mode_config_weekly():
    mc = _mode_config("weekly")
    assert mc["label"] == "每周精选"
    assert mc["lookback"] == 168
    assert mc["max_items"] == 5


def test_mode_config_monthly():
    mc = _mode_config("monthly")
    assert mc["label"] == "月度精选"
    assert mc["lookback"] == 720
    assert mc["max_items"] == 10


# ── 关键词词边界匹配 ──
def test_keyword_boundary_prevents_false_match():
    """AI 不应匹配 MAIL / RETAIL 中的子串"""
    keywords = {"high_priority": ["AI"], "medium_priority": []}
    assert score_item({"title": "AI赋能跨境电商", "category": "industry"}, keywords) == 3  # 真正的 AI
    assert score_item({"title": "跨境电商发送MAIL通知", "category": "industry"}, keywords) == 0  # MAIL 不是 AI


def test_keyword_boundary_saas():
    keywords = {"high_priority": [], "medium_priority": ["SaaS"]}
    assert score_item({"title": "SaaS企业出海", "category": "industry"}, keywords) == 1
    assert score_item({"title": "PaaS平台", "category": "industry"}, keywords) == 0  # PaaS 不是 SaaS


def test_chinese_keyword_still_in_match():
    """中文关键词保持原有的 in 匹配方式"""
    keywords = {"high_priority": ["出海"], "medium_priority": []}
    assert score_item({"title": "企业出海", "category": "industry"}, keywords) == 3


def test_compile_keyword_patterns():
    keywords = {"high_priority": ["AI", "出海"], "medium_priority": ["SaaS"]}
    patterns = _compile_keyword_patterns(keywords)
    assert len(patterns) == 3
    # AI 和 SaaS 应为编译后的正则，出海为字符串
    types = {type(p[1]).__name__ for p in patterns}
    assert "Pattern" in types
    str_count = sum(1 for p in patterns if isinstance(p[1], str))
    assert str_count == 1


# ── 标题二级去重 ──
def test_is_duplicate_with_title():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_items (url_hash TEXT PRIMARY KEY, source TEXT NOT NULL, first_seen TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_titles (title_hash TEXT PRIMARY KEY, first_seen TEXT NOT NULL)")
    conn.commit()
    url = "https://a.com/1"
    title = "跨境电商新政发布"
    # 先标记
    from collector import mark_seen
    mark_seen(conn, url, "test", title)
    conn.commit()
    # 同标题不同 URL 应被识别为重复
    assert is_duplicate(conn, "https://b.com/mobile/1", title) is True
    # 不同标题不同 URL 不应重复
    assert is_duplicate(conn, "https://b.com/2", "完全不同的文章") is False
    conn.close()
