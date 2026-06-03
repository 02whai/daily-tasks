#!/usr/bin/env python3
"""飞书每日情报推送 - 主采集脚本"""
import base64, hashlib, hmac, json, logging, os, re, sqlite3, sys, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser, requests, yaml
from bs4 import BeautifulSoup
from jinja2 import Template
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(os.path.join(BASE_DIR, "logs", "collector.log")),
            logging.StreamHandler(sys.stdout),
        ],
    )

def _load_dotenv(path):
    """手动加载 .env 文件，避免引入额外依赖。"""
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

def _overlay_env(config):
    """将环境变量覆盖到 config 的敏感字段。"""
    for key, env_var in [
        ("feishu.webhook_url", "FEISHU_WEBHOOK_URL"),
        ("feishu.webhook_secret", "FEISHU_WEBHOOK_SECRET"),
        ("llm.api_key", "LLM_API_KEY"),
        ("llm.base_url", "LLM_BASE_URL"),
        ("llm.model", "LLM_MODEL"),
    ]:
        val = os.environ.get(env_var, "")
        if val:
            section, field = key.split(".")
            config[section][field] = val

def load_config():
    _load_dotenv(os.path.join(BASE_DIR, ".env"))

    try:
        with open(os.path.join(BASE_DIR, "config.yaml")) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        logging.error("config.yaml not found")
        sys.exit(1)

    try:
        with open(os.path.join(BASE_DIR, "sources.yaml")) as f:
            sources = yaml.safe_load(f)
    except FileNotFoundError:
        logging.error("sources.yaml not found")
        sys.exit(1)

    try:
        with open(os.path.join(BASE_DIR, "keywords.yaml")) as f:
            keywords = yaml.safe_load(f)
    except FileNotFoundError:
        logging.error("keywords.yaml not found")
        sys.exit(1)

    _overlay_env(config)

    # 校验必填字段
    _validate_config(config, sources, keywords)

    return config, sources, keywords

def _validate_config(config, sources, keywords):
    feishu = config.get("feishu", {})
    if not feishu.get("webhook_url") and not (feishu.get("app_id") and feishu.get("app_secret")):
        logging.error("飞书配置缺失：需要 webhook_url 或 (app_id + app_secret)")
        sys.exit(1)
    if not config.get("llm", {}).get("api_key"):
        logging.warning("LLM API key 未配置，将跳过简报生成")
    if not sources.get("sources"):
        logging.error("sources.yaml 中没有配置任何信息源")
        sys.exit(1)
    if not keywords.get("high_priority"):
        logging.warning("keywords.yaml 中未配置 high_priority 关键词")

def init_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS seen_items (
        url_hash TEXT PRIMARY KEY, source TEXT NOT NULL, first_seen TEXT NOT NULL)""")
    conn.commit()
    return conn

def gen_signature(timestamp, secret):
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode()

def wait_for_rsshub(base_url, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{base_url}/api/categories", timeout=3).status_code == 200:
                logging.info(f"RSSHub ready ({base_url})"); return True
        except requests.RequestException:
            pass
        time.sleep(2)
    logging.warning(f"RSSHub not ready in {timeout}s"); return False

def is_within_lookback(published_parsed, hours):
    if not published_parsed: return True
    try:
        pub_dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        # 只有日期没有具体时间的（午夜零点），视为当天末尾
        if published_parsed[3:6] == (0, 0, 0):
            pub_dt = pub_dt.replace(hour=23, minute=59, second=59)
        return pub_dt >= datetime.now(timezone.utc) - timedelta(hours=hours)
    except (TypeError, ValueError):
        return True

def parse_cn_time(text):
    if not text: return None
    now = datetime.now()
    m = re.match(r'(\d+)\s*小时前', text)
    if m: return (now - timedelta(hours=int(m.group(1)))).timetuple()
    m = re.match(r'(\d+)\s*分钟前', text)
    if m: return (now - timedelta(minutes=int(m.group(1)))).timetuple()
    if '昨天' in text: return (now - timedelta(days=1)).timetuple()
    if '前天' in text: return (now - timedelta(days=2)).timetuple()
    for fmt in ["%Y-%m-%d", "%m-%d", "%Y年%m月%d日", "%m月%d日"]:
        try:
            dt = datetime.strptime(text.strip(), fmt)
            if dt.year == 1900: dt = dt.replace(year=now.year)
            return dt.timetuple()
        except ValueError: continue
    return None

def clean_summary(text):
    if not text: return ""
    plain = BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)
    return plain[:120] + "..." if len(plain) > 120 else plain

# ── 采集分发 ──
def fetch_from_source(src, config):
    src_type = src["type"]
    max_items = config["collector"]["max_items_per_source"]
    try:
        if src_type == "rss":
            return fetch_rss(src["url"], max_items, src)
        elif src_type == "rsshub":
            return fetch_rss(urljoin(config["rsshub"]["base_url"], src["route"]), max_items, src)
        elif src_type == "json_api":
            return fetch_json_api(src, max_items)
        elif src_type == "playwright":
            return fetch_playwright(src, max_items)
        elif src_type == "page":
            return fetch_page(src, max_items)
        else:
            logging.warning(f"Unknown type: {src_type}"); return []
    except Exception as e:
        logging.error(f"Fetch failed [{src['name']}]: {e}"); return []

def fetch_rss(url, max_items, src):
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:max_items]:
        items.append({
            "title": getattr(entry, "title", "").strip(),
            "url": getattr(entry, "link", ""),
            "summary": clean_summary(getattr(entry, "summary", "")),
            "published": entry.get("published_parsed", None),
            "source": src["name"], "category": src["category"],
        })
    return items

def fetch_json_api(src, max_items):
    resp = requests.get(src["url"], timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })
    resp.raise_for_status()
    data = resp.json()
    fm = src["field_map"]
    date_fmt = fm.get("date_format", "%Y-%m-%d")
    items = []
    for row in data[:max_items]:
        title = row.get(fm["title"], "").strip()
        url = row.get(fm["url"], "")
        pub_time = None
        date_str = row.get(fm.get("date", ""), "")
        if date_str and date_fmt:
            try:
                pub_time = datetime.strptime(date_str.strip(), date_fmt).timetuple()
            except ValueError: pass
        if title and url:
            items.append({
                "title": title, "url": url,
                "summary": row.get(fm.get("summary", ""), "") if fm.get("summary") else "",
                "published": pub_time, "source": src["name"], "category": src["category"],
            })
    return items

def fetch_playwright(src, max_items):
    js_code = src.get("playwright_js", "")
    if not js_code:
        logging.error(f"[{src['name']}] No playwright_js"); return []
    items = []
    timeout_ms = src.get("timeout_ms", 60000)  # 默认 60s（亿邦这种 JS 重的页面 30s 不够）
    retries = src.get("retries", 2)  # 失败重试次数
    for attempt in range(1, retries + 2):  # 1, 2, 3
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    locale="zh-CN",
                )
                page = context.new_page()
                page.set_extra_http_headers({
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                })
                page.goto(src["url"], timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                rows = page.evaluate(js_code)
                context.close(); browser.close()
                for row in rows[:max_items]:
                    pub_time = None
                    date_str = row.get("date", "")
                    if date_str:
                        try:
                            pub_time = datetime.strptime(date_str.strip(), "%Y-%m-%d").timetuple()
                        except ValueError:
                            pub_time = parse_cn_time(date_str)
                    items.append({
                        "title": row.get("title", "").strip(),
                        "url": row.get("url", ""),
                        "summary": row.get("summary", "") or "",
                        "published": pub_time, "source": src["name"], "category": src["category"],
                    })
            return items  # 成功就返回
        except Exception as e:
            logging.warning(f"[{src['name']}] attempt {attempt}/{retries+1} failed: {e}")
            if attempt <= retries:
                time.sleep(3)  # 重试前等 3s
            else:
                logging.error(f"[{src['name']}] all {retries+1} attempts failed: {e}")
                return []
    return items

def fetch_page(src, max_items):
    sel = src["selectors"]
    resp = requests.get(src["url"], timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    seen = set()
    for container in soup.select(sel["item"])[:max_items * 3]:
        # Direct <a> tag matches (e.g., USTR)
        if container.name == "a":
            title_el = link_el = container
        else:
            title_el = container.select_one(sel.get("title")) if sel.get("title") else container
            link_el = container.select_one(sel.get("link")) if sel.get("link") else title_el
        if not title_el or not link_el:
            continue
        href = link_el.get("href", "")
        if not href: continue
        if not href.startswith("http"):
            href = urljoin(src["url"], href)
        if href in seen: continue
        seen.add(href)
        if len(items) >= max_items: break
        summary_el = container.select_one(sel.get("summary")) if sel.get("summary") else None
        date_el = container.select_one(sel.get("date")) if sel.get("date") else None
        raw_date = date_el.get_text(strip=True) if date_el else ""
        parsed_time = parse_cn_time(raw_date)
        # Extract date from URL path (e.g., /2026/june/...)
        if not parsed_time:
            dm = re.search(r'/(\d{4})/(\w+)/', href)
            if dm:
                months = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
                         "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
                try:
                    dt = datetime(int(dm.group(1)), months.get(dm.group(2).lower(), 1), 1)
                    parsed_time = dt.timetuple()
                except (ValueError, KeyError): pass
        items.append({
            "title": title_el.get_text(strip=True)[:120],
            "url": href,
            "summary": summary_el.get_text(strip=True) if summary_el else "",
            "published": parsed_time, "source": src["name"], "category": src["category"],
        })
    return items

# ── 去重 ──
def is_duplicate(db, url):
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    return db.execute("SELECT 1 FROM seen_items WHERE url_hash = ?", (url_hash,)).fetchone() is not None

def mark_seen(db, url, source):
    db.execute("INSERT OR IGNORE INTO seen_items VALUES (?, ?, ?)",
               (hashlib.sha256(url.encode()).hexdigest(), source, datetime.now().isoformat()))

def cleanup_old_records(db, retention_days):
    db.execute("DELETE FROM seen_items WHERE first_seen < datetime('now', ?)",
               (f"-{retention_days} days",)); db.commit()

# ── 打分 ──
def score_item(item, keywords):
    score = 0; matched = []
    title = item.get("title", "")
    for kw in keywords.get("high_priority", []):
        if kw in title: score += 3; matched.append(kw)
    for kw in keywords.get("medium_priority", []):
        if kw in title: score += 1; matched.append(kw)
    if item.get("category") == "policy": score += 2
    item["_score"] = score; item["_matched_kws"] = matched
    return score

def emoji_for_category(cat):
    return {"policy": "🔴", "industry": "🟠", "global": "🟢"}.get(cat, "⚪")

# ── LLM 简报 ──
def generate_briefing(items, llm_config):
    """调用 LLM 对精选条目生成每日简报"""
    if not llm_config.get("api_key"):
        return ""
    lines = []
    for i, item in enumerate(items, 1):
        title = item['title']
        kws = item.get("_matched_kws", [])
        kw_str = (" | 关键词: " + "、".join(kws)) if kws else ""
        lines.append(f"{i}. [{item['source']}] {title}{kw_str}")
        if item.get("summary"):
            lines.append(f"   摘要: {item['summary'][:200]}")
    prompt = (
        f"以下是今日精选的 {len(items)} 条跨境贸易政策与行业动态。请直接输出一段中文每日简报（150-200字），"
        f"突出最重要的2-3个要点。直接输出正文，不要加任何前缀或说明。\n\n"
        + "\n".join(lines)
    )
    try:
        resp = requests.post(
            f"{llm_config['base_url']}/v1/chat/completions",
            headers={"Authorization": f"Bearer {llm_config['api_key']}"},
            json={
                "model": llm_config.get("model", "MiniMax-M3"),
                "messages": [
                    {"role": "system", "content": "你是一个中文跨境贸易情报分析助手。你只用中文回复，绝对不使用英文。收到任何输入后，你直接输出分析结果，不加任何前缀或解释。"},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 4096, "temperature": 0.3,
                "thinking": {"type": "disabled"},
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()["choices"][0]["message"]["content"].strip()
        # MiniMax M3 默认开启 thinking，实际输出在 </think> 之后
        if '</think>' in result:
            result = result.split('</think>', 1)[1].strip()
        # 如果返回了英文（说明模型没听中文指令），做简单模板降级
        if any(c.isascii() and c.isalpha() for c in result[:20]) and not any('一' <= c <= '鿿' for c in result[:50]):
            logging.warning("LLM returned English, using rule-based fallback")
            return _rule_based_briefing(items)
        return result
    except Exception as e:
        logging.error(f"LLM briefing failed: {e}")
        return ""

def _rule_based_briefing(items):
    """纯规则降级简报：当 LLM 不听话时的保底方案"""
    sources = {}
    for it in items:
        src = it["source"]
        if src not in sources:
            sources[src] = []
        sources[src].append(it["title"][:40])
    parts = []
    policy_count = sum(1 for it in items if it.get("category") == "policy")
    parts.append(f"今日共采集 {len(items)} 条情报，其中政策类 {policy_count} 条。")
    for src, titles in list(sources.items())[:5]:
        parts.append(f"{src}：{'、'.join(titles[:2])}")
    return "；".join(parts[:4])

# ── 飞书卡片 ──
def build_card(items, config, keywords, briefing=""):
    with open(os.path.join(BASE_DIR, "templates", "feishu_card.json")) as f:
        template = Template(f.read())
    enriched = [{
        "emoji": emoji_for_category(it.get("category", "")),
        "source": it["source"], "title": it["title"], "url": it["url"],
        "summary": it["summary"],
        "keywords": " ".join(f"`{kw}`" for kw in it.get("_matched_kws", [])),
    } for it in items]
    return json.loads(template.render(
        date=datetime.now().strftime("%Y-%m-%d"), items=enriched,
        total_sources=config.get("_total_sources", 0),
        total_items=config.get("_total_items", 0),
        selected=len(items), tags=["跨境电商", "出海", "AI", "贸易政策"],
        briefing=json.dumps(briefing)[1:-1] if briefing else "",
    ))

def send_to_feishu(feishu_cfg, card):
    webhook_url = feishu_cfg.get("webhook_url", "")
    if webhook_url:
        payload = {"msg_type": "interactive", "card": card}
        secret = feishu_cfg.get("webhook_secret", "")
        if secret:
            payload["timestamp"] = str(int(time.time()))
            payload["sign"] = gen_signature(int(payload["timestamp"]), secret)
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status(); return resp.json()
    app_id = feishu_cfg.get("app_id", ""); app_secret = feishu_cfg.get("app_secret", "")
    chat_id = feishu_cfg.get("chat_id", "")
    if app_id and app_secret and chat_id:
        token = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret}, timeout=15,
        ).json()["tenant_access_token"]
        return requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": chat_id, "msg_type": "interactive", "content": json.dumps(card)},
            timeout=15,
        ).json()
    raise ValueError("缺少飞书配置")

# ── 主流程 ──
def main():
    _setup_logging()
    config, sources, keywords = load_config()
    rsshub_ok = wait_for_rsshub(config["rsshub"]["base_url"])
    db_path = os.path.join(BASE_DIR, "data", "seen.db")
    is_first_run = not os.path.exists(db_path)
    db = init_db(db_path)

    all_items = []
    for src in sources["sources"]:
        if src.get("enabled") is False:
            continue
        if src["type"] == "rsshub" and not rsshub_ok:
            continue
        items = fetch_from_source(src, config)
        lookback = src.get("lookback_hours", config["collector"]["lookback_hours"])
        items = [it for it in items if is_within_lookback(it["published"], lookback)]
        all_items.extend(items)
        logging.info(f"[{src['name']}] {len(items)} items (filtered)")

    if is_first_run:
        for item in all_items:
            if item.get("url"): mark_seen(db, item["url"], item["source"])
        db.commit()
        print(f"[首次运行] 已入库 {len(all_items)} 条，明天开始推送")
        logging.info(f"First run: stored {len(all_items)} items")
        db.close(); return

    scored = []; deduped = 0
    for item in all_items:
        if not item.get("url"): continue
        if is_duplicate(db, item["url"]): continue
        deduped += 1
        s = score_item(item, keywords)
        if s >= config["collector"]["min_score_to_push"]:
            scored.append((s, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [item for _, item in scored[:config["collector"]["max_push_items"]]]

    if not top:
        print("无新增高相关性内容，跳过推送")
        db.close(); return

    briefing = generate_briefing(top, config.get("llm", {}))
    stats = {"_total_sources": len(sources["sources"]), "_total_items": deduped}
    card = build_card(top, {**config, **stats}, keywords, briefing)
    result = send_to_feishu(config["feishu"], card)
    print(f"推送完成: {len(top)} 条, 响应: {result.get('code', 'ok')}")
    logging.info(f"Push done: {len(top)} items")

    for item in top: mark_seen(db, item["url"], item["source"])
    db.commit()
    cleanup_old_records(db, config["collector"]["dedup_retention_days"])
    db.close()

if __name__ == "__main__":
    main()
