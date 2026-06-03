#!/usr/bin/env python3
"""GitHub Trending 项目推送 — 每日/每周精选"""
import json, logging, os, re, sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_ITEMS = 5


def _setup_logging():
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(BASE_DIR, "logs", "collector.log")),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def _extract_number(el, selector):
    found = el.select_one(selector)
    if not found:
        return 0
    nums = re.findall(r"[\d,]+", found.get_text(strip=True))
    return int(nums[0].replace(",", "")) if nums else 0


def _parse_repo(article, since):
    h2_link = article.select_one("h2 a")
    if not h2_link:
        return None
    href = h2_link.get("href", "").strip().strip("/")
    parts = href.split("/")
    if len(parts) < 2:
        return None
    full_name = f"{parts[0]}/{parts[1]}"

    desc_el = article.select_one("p")
    description = desc_el.get_text(strip=True)[:200] if desc_el else ""

    lang_el = article.select_one('[itemprop="programmingLanguage"]')
    language = lang_el.get_text(strip=True) if lang_el else ""

    f6 = article.select_one(".f6")
    total_stars = _extract_number(f6, 'a[href*="/stargazers"]') if f6 else 0
    forks = _extract_number(f6, 'a[href*="/forks"]') if f6 else 0
    star_label = "今日新增" if since == "daily" else "本周新增"

    today_stars = 0
    if f6:
        for child in f6.find_all("span", recursive=False):
            text = child.get_text(strip=True)
            if "star" in text.lower():
                nums = re.findall(r"[\d,]+", text)
                if nums:
                    today_stars = int(nums[0].replace(",", ""))
                break

    return {
        "name": full_name,
        "url": f"https://github.com/{full_name}",
        "description": description,
        "language": language,
        "total_stars": total_stars,
        "forks": forks,
        "today_stars": today_stars,
        "star_label": star_label,
    }


def _fetch_trending(since="daily"):
    url = f"https://github.com/trending?since={since}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, timeout=30, headers=headers)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    repos = []
    for article in soup.select("article.Box-row"):
        repo = _parse_repo(article, since)
        if repo:
            repos.append(repo)
        if len(repos) >= MAX_ITEMS:
            break
    return repos


def _build_card(repos, mode_label):
    date_str = datetime.now().strftime("%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][datetime.now().weekday()]
    lines = []
    for i, r in enumerate(repos, 1):
        lang_part = f"  🔵 {r['language']}" if r["language"] else ""
        desc_part = f"\n{r['description']}" if r["description"] else ""
        lines.append(
            f"{i}. **[{r['name']}]({r['url']})**\n"
            f"⭐ {r['total_stars']:,} 星  🍴 {r['forks']:,} fork{lang_part}{desc_part}\n"
            f"📈 +{r['today_stars']} {r['star_label']}"
        )

    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "indigo",
            "title": {"tag": "plain_text", "content": f"GitHub 热门项目 · {date_str} {weekday_cn}"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{mode_label}** · TOP {len(repos)}",
                },
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n\n".join(lines)},
            },
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"GitHub 热门项目 · {date_str} · 自动采集",
                    }
                ],
            },
        ],
    }


def _send_card(webhook_url, card):
    payload = {"msg_type": "interactive", "card": card}
    resp = requests.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main():
    _setup_logging()
    _load_dotenv(os.path.join(BASE_DIR, ".env"))

    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        logging.error("FEISHU_WEBHOOK_URL 未配置")
        sys.exit(1)

    today = datetime.now().date()
    if today.weekday() == 0:
        since, mode_label = "weekly", "本周热门"
    else:
        since, mode_label = "daily", "今日热门"

    logging.info(f"模式: {mode_label} (since={since})")

    try:
        repos = _fetch_trending(since)
    except requests.RequestException as e:
        logging.error(f"GitHub Trending 获取失败: {e}")
        sys.exit(1)

    if not repos:
        logging.warning("未获取到 Trending 项目，跳过推送")
        return

    logging.info(f"获取到 {len(repos)} 个项目")

    card = _build_card(repos, mode_label)

    try:
        result = _send_card(webhook_url, card)
        logging.info(f"推送完成: {len(repos)} 个项目, code={result.get('code', 'ok')}")
        print(f"已推送 {len(repos)} 个 GitHub Trending 项目")
    except requests.RequestException as e:
        logging.error(f"飞书推送失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
