"""GitHub Trending 采集器单元测试"""
import sys, os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from collector import _extract_number, _parse_repo, _fetch_trending, _build_card, _send_card


# ── HTML 解析 ──

_stub_article = """
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/testowner/testrepo">
      <span>testowner / </span>
      <span>testrepo</span>
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">
    A test repository for demonstration
  </p>
  <div class="f6 color-fg-muted mt-2">
    <span class="d-inline-block mr-3">
      <span itemprop="programmingLanguage">Python</span>
    </span>
    <a class="Link--muted d-inline-block mr-3" href="/testowner/testrepo/stargazers">
      <svg></svg>
      12,345
    </a>
    <a class="Link--muted d-inline-block mr-3" href="/testowner/testrepo/forks">
      <svg></svg>
      678
    </a>
    <span class="d-inline-block float-sm-right">
      <svg></svg>
      234 stars today
    </span>
  </div>
</article>
"""


def test_parse_full_article():
    from bs4 import BeautifulSoup
    article = BeautifulSoup(_stub_article, "html.parser").select_one("article.Box-row")
    r = _parse_repo(article, "daily")
    assert r["name"] == "testowner/testrepo"
    assert r["url"] == "https://github.com/testowner/testrepo"
    assert r["description"] == "A test repository for demonstration"
    assert r["language"] == "Python"
    assert r["total_stars"] == 12345
    assert r["forks"] == 678
    assert r["today_stars"] == 234
    assert r["star_label"] == "今日新增"


def test_parse_article_weekly():
    from bs4 import BeautifulSoup
    html = _stub_article.replace("stars today", "stars this week")
    article = BeautifulSoup(html, "html.parser").select_one("article.Box-row")
    r = _parse_repo(article, "weekly")
    assert r["star_label"] == "本周新增"


def test_parse_article_no_description():
    from bs4 import BeautifulSoup
    html = _stub_article.replace(
        '<p class="col-9 color-fg-muted my-1 pr-4">\n    A test repository for demonstration\n  </p>',
        ""
    )
    article = BeautifulSoup(html, "html.parser").select_one("article.Box-row")
    r = _parse_repo(article, "daily")
    assert r is not None
    assert r["description"] == ""


def test_parse_article_missing_h2_returns_none():
    from bs4 import BeautifulSoup
    html = _stub_article.replace("<h2", "<div").replace("</h2>", "</div>")
    article = BeautifulSoup(html, "html.parser").select_one("article.Box-row")
    assert _parse_repo(article, "daily") is None


def test_extract_number():
    from bs4 import BeautifulSoup
    html = '<div><a href="/stargazers">1,234 stars</a></div>'
    el = BeautifulSoup(html, "html.parser")
    assert _extract_number(el, "a") == 1234


def test_extract_number_missing():
    from bs4 import BeautifulSoup
    el = BeautifulSoup("<div></div>", "html.parser")
    assert _extract_number(el, "a") == 0


# ── 卡片构建 ──


def test_build_card_daily():
    repos = [
        {
            "name": "owner/repo1",
            "url": "https://github.com/owner/repo1",
            "description": "第一个测试项目",
            "language": "Go",
            "total_stars": 5000,
            "forks": 300,
            "today_stars": 150,
            "star_label": "今日新增",
        }
    ]
    card = _build_card(repos, "今日热门")
    assert card["header"]["template"] == "indigo"
    assert "GitHub 热门项目" in card["header"]["title"]["content"]
    body = card["elements"][2]["text"]["content"]
    assert "owner/repo1" in body
    assert "5,000" in body
    assert "Go" in body
    assert "150" in body
    assert "今日新增" in body
    assert "今日热门" in card["elements"][0]["text"]["content"]


def test_build_card_weekly_label():
    repos = [
        {
            "name": "x/y",
            "url": "https://github.com/x/y",
            "description": "",
            "language": "",
            "total_stars": 100,
            "forks": 10,
            "today_stars": 20,
            "star_label": "本周新增",
        }
    ]
    card = _build_card(repos, "本周热门")
    assert "本周热门" in card["elements"][0]["text"]["content"]


# ── 飞书发送 ──


@patch("requests.post")
def test_send_card(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 0, "msg": "success"}
    mock_post.return_value = mock_resp
    result = _send_card("https://example.com/webhook", {"card": "test"})
    assert result["code"] == 0
    payload = mock_post.call_args[1]["json"]
    assert payload["msg_type"] == "interactive"
