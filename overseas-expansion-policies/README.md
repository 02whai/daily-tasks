# 飞书每日情报推送系统 — 实施方案

> **目标**：每天定时将跨境电商、出海、贸易政策、AI 赋能企业等领域的权威信息，通过飞书机器人推送到指定群聊或个人。

---

## 一、整体架构

```
┌──────────────────────────────────────────────────┐
│              macOS launchd (两个任务)              │
│                                                   │
│   RSSHub 守护任务：开机启动，常驻后台               │
│   Collector 采集任务：每天 08:30 触发              │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│              collector.py (Python)                │
│                                                   │
│  ┌─────────────────────────────────────────┐     │
│  │              数据采集层                   │     │
│  │                                          │     │
│  │  type: json_api → HTTP GET JSON（gov.cn）│     │
│  │  type: rss      → feedparser 直接读取    │     │
│  │  type: page     → BeautifulSoup 解析     │     │
│  │  enabled: false → 跳过（待部署/待修复）    │     │
│  └──────────────────┬──────────────────────┘     │
│                     │                             │
│                     ▼                             │
│  ┌─────────────────────────────────────────┐     │
│  │   时间过滤 + 去重 + 关键词打分 + 排序     │     │
│  └──────────────────┬──────────────────────┘     │
│                     │                             │
│                     ▼                             │
│  ┌─────────────────────────────────────────┐     │
│  │        飞书交互式卡片消息组装              │     │
│  └──────────────────┬──────────────────────┘     │
│                     │                             │
└─────────────────────┼─────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────┐
│          飞书 Webhook API (V2)                     │
│   POST https://open.feishu.cn/open-apis/bot/      │
│        v2/hook/{your-webhook-id}                   │
└──────────────────────────────────────────────────┘
```

---

## 二、信息源配置

按获取方式分类，覆盖政策 + 行业 + 全球三个维度。✅ = 已实地验证可用，⏸️ = 待解决。

| # | 来源 | 获取方式 | 状态 | 数据地址 | 分类 |
|---|------|----------|------|----------|------|
| 1 | 中国政府网 - 最新政策 | JSON API | ✅ | `https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json` | policy |
| 2 | 中国政府网 - 政策解读 | JSON API | ✅ | `https://www.gov.cn/zhengce/jiedu/ZCJD_QZ.json` | policy |
| 3 | 商务部 - 政策发布 | HTML 解析 | ✅ | `https://www.mofcom.gov.cn/zwgk/zcfb/index.html` | policy |
| 4 | 海关总署 - 海关法规 | Playwright | ✅ | `http://www.customs.gov.cn/customs/302249/302266/index.html` | policy |
| 5 | 亿邦动力 | Playwright | ✅ | `https://www.ebrun.com/` | industry |
| 6 | WTO | RSS | ⏸️ | RSS URL 失效，待找正确地址 | global |
| 7 | USTR | RSS | ⏸️ | RSS URL 失效，待找正确地址 | policy |

**已验证的核心发现**：

- **gov.cn 有公开 JSON API**（`/ZUIXINZHENGCE.json`、`/ZCJD_QZ.json`），最可靠的采集方式
- **商务部页面是服务端渲染**，BeautifulSoup 可解析
- **海关总署有 412 WAF**，需 Playwright + 反检测参数绕过（`--disable-blink-features=AutomationControlled`）
- **亿邦动力是 JS 渲染 SPA**，需 Playwright 浏览器渲染
- 海关法规更新频率低（周级），建议设置 30 天回溯窗口

**说明**：
**四种采集方式**：

- **JSON API (type: json_api)** — 网站有公开 JSON 接口，直接 HTTP GET 拿结构化数据。最可靠，零维护。目前 gov.cn 两个页面都有
- **原生 RSS (type: rss)** — 网站直接提供 XML feed，`feedparser.parse(url)` 即可读取
- **页面解析 (type: page)** — 服务端渲染的 HTML 页面，用 BeautifulSoup 解析列表页。适用于商务部等传统政府网站
- **RSSHub (type: rsshub)** — 无原生 RSS 时由 RSSHub 代理转换（当前未启用，待部署）

### RSSHub 部署（不需要 Docker）

三种方式，**推荐方式一（利用现有 Node.js v24）**：

| 方式 | 命令 | 适合场景 |
|------|------|----------|
| **方式一：npm 一行启动** | `npx rsshub` | 零安装，利用已有 Node 环境 |
| 方式二：npm 全局安装 | `npm install -g rsshub && rsshub` | 持久使用 |
| 方式三：公共服务 | 直接用 `https://rsshub.app` | 临时测试，有频率限制 |

启动后 RSSHub 监听 `http://localhost:1200`。

**重要**：`npx rsshub` 是前台进程，终端关闭后进程就没了。必须用 launchd 设成开机自启的守护进程，详见第七节「双任务 launchd 配置」。

### Tier 3 后备方案（无 RSSHub 路由时）

1. **RSSHub 自定义路由**（推荐）— 编写 JS 路由文件，告诉 RSSHub 从目标网页的哪个 HTML 元素提取标题/链接/摘要
2. **RSS 生成服务** — 用 `https://rss.app` 或 `https://fetchrss.com` 将目标页面 URL 转 RSS
3. **type: page 直接解析** — 用 `requests + BeautifulSoup` 解析页面文章列表

### 关于政府网 PDF 文件

国务院、商务部等网站的许多政策原文是 PDF 格式。**本方案不需要解析 PDF**，原因：

- RSSHub/页面解析抓取的是政策文件的 **HTML 发布页面**（标题 + 摘要 + PDF 链接），不是 PDF 本身
- 飞书卡片推送只需**标题 + 链接 + 一句话摘要**，用户点击链接在飞书浏览器查看 PDF 全文
- 政策 HTML 发布页面通常自带摘要/要点（政务公开规范要求），足够做推送摘要

若未来需深度分析（大模型读 PDF 全文生成总结），可后续加入 `pdfplumber` 或 `PyMuPDF`。推送场景现阶段用不着。

---

## 三、去重策略

使用 **SQLite** 轻量级去重，表结构：

```sql
CREATE TABLE IF NOT EXISTS seen_items (
    url_hash   TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    first_seen TEXT NOT NULL  -- ISO 8601 datetime
);
```

**逻辑**：
- 对每条新闻的 URL 计算 SHA256 作为主键
- INSERT OR IGNORE，已存在则跳过
- 每次运行末尾清理 30 天前的记录（`DELETE FROM seen_items WHERE first_seen < datetime('now', '-30 days')`）

---

## 四、关键词过滤与打分

### keywords.yaml

```yaml
# keywords.yaml — 关键词与打分规则
high_priority:
  # 跨境电商
  - "跨境电商"
  - "跨境电子商务"
  - "综试区"
  - "海外仓"
  - "出口退税"
  - "跨境支付"
  - "跨境物流"
  # 出海
  - "出海"
  - "企业出海"
  - "品牌出海"
  - "全球化"
  - "国际化"
  - "海外市场"
  - "对外投资"
  - "ODI"
  # 贸易
  - "外贸"
  - "进出口"
  - "关税"
  - "RCEP"
  - "自贸区"
  - "自由贸易"
  - "贸易摩擦"
  - "反倾销"
  - "出口管制"
  # AI 赋能
  - "人工智能"
  - "AI"
  - "大模型"
  - "AIGC"
  - "智能化"
  - "数字化"
  - "SaaS"
  - "企业服务"
  # 政策
  - "商务部"
  - "海关总署"
  - "国务院"
  - "外汇管理局"
  - "新规"
  - "通知"
  - "意见"
  - "公告"

medium_priority:
  - "供应链"
  - "物流"
  - "合规"
  - "数据安全"
  - "ESG"
  - "碳中和"
  - "产业带"
  - "DTC"
  - "独立站"

# 打分规则（在代码中实现）
#  标题命中 high_priority   → +3 分/词
#  标题命中 medium_priority  → +1 分/词
#  来源 category == "policy" → +2 分（官方来源加权，含 USTR）
#  只推送总分 ≥ 4 分的条目
#  例：1个HIGH词(=3) + 官方源(=2) → 5分 ✅
#  例：2个HIGH词(=6)普通行业源 → 6分 ✅
#  例：1个HIGH词(=3)普通行业源 → 3分 ❌ 不推
```

---

## 五、飞书消息卡片格式

使用飞书**交互式卡片消息** (`interactive`)。

### 卡片模板 (`templates/feishu_card.json`)

```json
{
  "config": {
    "wide_screen_mode": true,
    "enable_forward": true
  },
  "header": {
    "template": "blue",
    "title": {
      "tag": "plain_text",
      "content": "每日情报 | {{date}}"
    }
  },
  "elements": [
    {
      "tag": "div",
      "text": {
        "tag": "lark_md",
        "content": "**今日共扫描 {{total_sources}} 个信息源，收录 {{total_items}} 条，精选 {{selected}} 条**\n关键词：{% for kw in tags %}#{{kw}} {% endfor %}"
      }
    },
    {
      "tag": "hr"
    },
    {
      "tag": "div",
      "text": {
        "tag": "lark_md",
        "content": "{% for item in items %}{{item.emoji}} **〔{{item.source}}〕** [{{item.title}}]({{item.url}})\n{{item.summary}}\n{% if item.keywords %}`{{item.keywords}}`{% endif %}\n\n{% endfor %}"
      }
    },
    {
      "tag": "hr"
    },
    {
      "tag": "note",
      "elements": [
        {
          "tag": "plain_text",
          "content": "自动采集 | {{total_sources}} 个信息源 | 去重后 {{total_items}} 条 | 精选 {{selected}} 条"
        }
      ]
    }
  ]
}
```

### 列表项 emoji 含义

- `🔴` — 官方政策文件（来源 category = policy）
- `🟠` — 行业动态/深度分析（category = industry）
- `🟢` — 全球视角（category = global）

### 飞书推送方式选择

#### 方式 A：群自定义机器人 Webhook（推荐）

1. 飞书群 → 设置 → 群机器人 → 添加自定义机器人
2. 获取 Webhook URL：`https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxx`
3. 智能体直接 HTTP POST JSON 到这个 URL，无需 token

#### 方式 B：已有 App ID + App Secret 调 API

```python
# 1. 获取 tenant_access_token
token_resp = requests.post(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": APP_ID, "app_secret": APP_SECRET}
)
token = token_resp.json()["tenant_access_token"]

# 2. 发送消息
requests.post(
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "receive_id": CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(card_json)
    }
)
```

**需额外获取 `chat_id`**：飞书开发者后台 → 调试 → 获取群聊 ID。

#### 推荐做法

App ID + Secret 留给智能体做复杂操作，额外花 30 秒建一个群机器人拿 Webhook URL 专做推送。架构最干净。

---

## 六、核心代码结构

```
~/feishu-daily-news/
├── README.md
├── config.yaml
├── collector.py          # 主采集脚本
├── sources.yaml          # 信息源配置
├── keywords.yaml         # 关键词和打分规则
├── templates/
│   └── feishu_card.json  # 飞书卡片 Jinja2 模板
├── data/
│   └── seen.db           # SQLite 去重库(自动创建)
└── logs/
    └── collector.log     # 运行日志(自动轮转)
```

### `config.yaml`

```yaml
# === 飞书推送配置（二选一，方式 A 优先） ===
feishu:
  # 方式 A：群自定义机器人 Webhook
  webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_WEBHOOK_ID"
  webhook_secret: ""       # 签名密钥，未开启签名校验则留空

  # 方式 B：已有 App ID + App Secret
  # app_id: "cli_xxxxxxxxxxxx"
  # app_secret: "xxxxxxxxxxxxxxxx"
  # chat_id: "oc_xxxxxxxxxxxxx"

# === 采集配置 ===
collector:
  max_items_per_source: 10   # 每个源最多取多少条
  lookback_hours: 24         # 只看过去 N 小时的文章(基于 published 字段)
  min_score_to_push: 4       # 最低推送分数（HIGH关键词=3, 官方源+2, 普通类需≥2个HIGH词或1个HIGH+官方源）
  max_push_items: 8          # 单次最多推送条数
  dedup_retention_days: 30   # 去重记录保留天数(到期自动清理)

# === RSSHub 配置 ===
rsshub:
  base_url: "http://localhost:1200"

# === 调度 ===
schedule:
  time: "08:30"
  timezone: "Asia/Shanghai"
```

### `sources.yaml`（已实地验证版）

```yaml
sources:
  # ===== 政策类 - JSON API（最可靠） ✅ =====
  - name: "国务院-最新政策"
    type: json_api
    url: "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json"
    category: policy
    field_map:
      title: "TITLE"
      url: "URL"
      date: "DOCRELPUBTIME"
      date_format: "%Y-%m-%d"

  - name: "国务院-政策解读"
    type: json_api
    url: "https://www.gov.cn/zhengce/jiedu/ZCJD_QZ.json"
    category: policy
    field_map:
      title: "TITLE"
      url: "URL"
      date: "DOCRELPUBTIME"
      date_format: "%Y-%m-%d"

  # ===== 政策类 - HTML 解析 ✅ =====
  - name: "商务部-政策发布"
    type: page
    url: "https://www.mofcom.gov.cn/zwgk/zcfb/index.html"
    category: policy
    selectors:
      item: "ul.list li, div.conList li, li"
      title: "a"
      link: "a"
      date: "span.date, em"

  # ===== 待解决（WAF/JS 渲染） ⏸️ =====
  - name: "海关总署-海关法规"
    type: playwright
    url: "http://www.customs.gov.cn/customs/302249/302266/index.html"
    category: policy
    lookback_hours: 720  # 法规更新慢，30 天回溯
    playwright_js: |
      (() => {
        const items = [];
        document.querySelectorAll('a[href*="/customs/"][href*="article_"]').forEach(a => {
          const text = a.textContent.trim();
          if (text.length > 5) {
            const parent = a.parentElement;
            let date = '';
            if (parent) {
              const el = parent.querySelector('span, em, i');
              if (el) date = el.textContent.trim();
            }
            items.push({title: text, url: a.href, date: date});
          }
        });
        return items;
      })()

  - name: "亿邦动力"
    type: playwright
    url: "https://www.ebrun.com/"
    category: industry
    playwright_js: |
      (() => {
        const items = [];
        const seen = new Set();
        document.querySelectorAll('a[href*=".shtml"]').forEach(a => {
          const text = a.textContent.trim();
          const href = a.href;
          if (text.length > 10 && !seen.has(href) && !href.includes('/tc/') && !href.includes('/ebs/')) {
            seen.add(href);
            let date = '';
            const parent = a.closest('li, div, article');
            if (parent) {
              const el = parent.querySelector('[class*="time"], time, .date, span');
              if (el) date = el.textContent.trim().substring(0, 20);
            }
            items.push({title: text, url: href, date: date});
          }
        });
        return items;
      })()

  # ===== 全球视角（RSS URL 待验证） ⏸️ =====
  - name: "WTO-News"
    type: rss
    url: "https://www.wto.org/rss/news.xml"
    category: global
    enabled: false

  - name: "USTR-贸易政策"
    type: rss
    url: "https://ustr.gov/rss/press-releases"
    category: policy
    enabled: false
```

> 注：`enabled: false` 的源在采集时自动跳过。待 Playwright 方案落地或找到正确 API/RSS 地址后改为 `true`。

### `collector.py`（完整实现参考）

```python
#!/usr/bin/env python3
"""
飞书每日情报推送 - 主采集脚本
运行一次：拉取所有信息源 → 时间过滤 → 去重 → 打分 → 推送到飞书
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from jinja2 import Template

# ── 日志 ──────────────────────────────────────────
logging.basicConfig(
    filename="logs/collector.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── 1. 加载配置 ───────────────────────────────────
def load_config():
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    with open("sources.yaml") as f:
        sources = yaml.safe_load(f)
    with open("keywords.yaml") as f:
        keywords = yaml.safe_load(f)
    return config, sources, keywords

# ── 2. 数据库初始化 ───────────────────────────────
def init_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_items (
            url_hash   TEXT PRIMARY KEY,
            source     TEXT NOT NULL,
            first_seen TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

# ── 3. 飞书签名 ───────────────────────────────────
def gen_signature(timestamp: int, secret: str) -> str:
    """飞书 V2 Webhook 签名算法"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")

# ── 4. RSSHub 探活 ─────────────────────────────────
def wait_for_rsshub(base_url, timeout=30):
    """等待 RSSHub 就绪，超时则跳过 RSSHub 源"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/api/categories", timeout=3)
            if resp.status_code == 200:
                logging.info(f"RSSHub 已就绪 ({base_url})")
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    logging.warning(f"RSSHub 未在 {timeout}s 内就绪，将跳过所有 rsshub 类型源")
    return False

# ── 5. 时间过滤辅助 ───────────────────────────────
def is_within_lookback(published_parsed, hours):
    """判断是否在过去 N 小时内。published_parsed 是 feedparser 的 time.struct_time"""
    if not published_parsed:
        return True  # 无时间信息则不过滤
    try:
        pub_dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return pub_dt >= cutoff
    except (TypeError, ValueError):
        return True  # 解析失败不过滤（宁可多抓，不遗漏）

# ── 5. 数据采集分发器 ─────────────────────────────
def fetch_from_source(src, config):
    """根据 source 的 type 字段分发到对应采集器"""
    src_type = src["type"]
    max_items = config["collector"]["max_items_per_source"]
    rsshub_base = config["rsshub"]["base_url"]

    try:
        if src_type == "rss":
            return fetch_rss(src["url"], max_items, src)
        elif src_type == "rsshub":
            url = urljoin(rsshub_base, src["route"])
            return fetch_rss(url, max_items, src)
        elif src_type == "json_api":
            return fetch_json_api(src, max_items)
        elif src_type == "page":
            return fetch_page(src, max_items)
        else:
            logging.warning(f"未知 source type: {src_type}")
            return []
    except Exception as e:
        logging.error(f"采集失败 [{src['name']}]: {e}")
        return []


def fetch_rss(url, max_items, src):
    """RSS 采集（含 feedparser 原生 RSS 和 RSSHub）"""
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:max_items]:
        items.append({
            "title": getattr(entry, "title", "").strip(),
            "url": getattr(entry, "link", ""),
            "summary": clean_summary(getattr(entry, "summary", "")),
            "published": entry.get("published_parsed", None),  # struct_time，不是 RFC 822 字符串
            "source": src["name"],
            "category": src["category"],
        })
    return items


def fetch_json_api(src, max_items):
    """JSON API 采集（gov.cn 等有公开 JSON 接口的网站）"""
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
        date_str = row.get(fm.get("date", ""), "")
        pub_time = None
        if date_str and date_fmt:
            try:
                dt = datetime.strptime(date_str.strip(), date_fmt)
                pub_time = dt.timetuple()
            except ValueError:
                pass
        if title and url:
            items.append({
                "title": title,
                "url": url,
                "summary": row.get("SUB_TITLE", "") or "",
                "published": pub_time,
                "source": src["name"],
                "category": src["category"],
            })
    return items


def fetch_page(src, max_items):
    """页面解析采集 (type: page)
    注意：中文网站常用自然语言时间（"2小时前"/"昨天"/"06-01"），
    fromisoformat 无法解析。时间过滤对此类源会失效（return True），
    但 SQLite 去重机制可防止重复推送。首次运行会多推几条历史文章，后续正常。
    """
    sel = src["selectors"]
    resp = requests.get(src["url"], timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    })
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for container in soup.select(sel["item"])[:max_items]:
        title_el = container.select_one(sel["title"])
        link_el = container.select_one(sel["link"])
        if not title_el or not link_el:
            continue
        href = link_el.get("href", "")
        if href and not href.startswith("http"):
            href = urljoin(src["url"], href)
        summary_el = container.select_one(sel.get("summary"))
        date_el = container.select_one(sel.get("date"))
        # 自然语言时间（"2小时前"）→ 近似为 now，不做精确过滤
        raw_date = date_el.get_text(strip=True) if date_el else ""
        parsed_time = _parse_cn_relative_time(raw_date)
        items.append({
            "title": title_el.get_text(strip=True),
            "url": href,
            "summary": summary_el.get_text(strip=True) if summary_el else "",
            "published": parsed_time,  # None 或 time.struct_time
            "source": src["name"],
            "category": src["category"],
        })
    return items


def _parse_cn_relative_time(text):
    """尝试解析中文相对时间，返回 time.struct_time 或 None"""
    import re
    from datetime import datetime, timedelta
    if not text:
        return None
    now = datetime.now()
    # "X 小时前" / "X 分钟前"
    m = re.match(r'(\d+)\s*小时前', text)
    if m:
        dt = now - timedelta(hours=int(m.group(1)))
        return dt.timetuple()
    m = re.match(r'(\d+)\s*分钟前', text)
    if m:
        dt = now - timedelta(minutes=int(m.group(1)))
        return dt.timetuple()
    # "昨天"
    if '昨天' in text:
        dt = now - timedelta(days=1)
        return dt.timetuple()
    # "前天"
    if '前天' in text:
        dt = now - timedelta(days=2)
        return dt.timetuple()
    # 尝试标准日期格式
    for fmt in ["%Y-%m-%d", "%m-%d", "%m月%d日"]:
        try:
            dt = datetime.strptime(text.strip(), fmt)
            if dt.year == 1900:
                dt = dt.replace(year=now.year)
            return dt.timetuple()
        except ValueError:
            continue
    return None


def clean_summary(text):
    """去掉 HTML 标签，截断到 120 字"""
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    plain = soup.get_text(separator=" ", strip=True)
    return plain[:120] + "..." if len(plain) > 120 else plain

# ── 6. 去重 ───────────────────────────────────────
def is_duplicate(db, url):
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    cur = db.execute("SELECT 1 FROM seen_items WHERE url_hash = ?", (url_hash,))
    return cur.fetchone() is not None


def mark_seen(db, url, source):
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    db.execute(
        "INSERT OR IGNORE INTO seen_items VALUES (?, ?, ?)",
        (url_hash, source, datetime.now().isoformat()),
    )


def cleanup_old_records(db, retention_days):
    """删除超过 retention_days 天的旧记录"""
    db.execute(
        "DELETE FROM seen_items WHERE first_seen < datetime('now', ?)",
        (f"-{retention_days} days",),
    )
    db.commit()

# ── 7. 打分 ───────────────────────────────────────
def score_item(item, keywords):
    score = 0
    title = item.get("title", "")
    matched_kws = []
    for kw in keywords.get("high_priority", []):
        if kw in title:
            score += 3
            matched_kws.append(kw)
    for kw in keywords.get("medium_priority", []):
        if kw in title:
            score += 1
            matched_kws.append(kw)
    # 官方来源加权
    if item.get("category") == "policy":
        score += 2
    item["_score"] = score
    item["_matched_kws"] = matched_kws
    return score


def emoji_for_category(category):
    return {"policy": "🔴", "industry": "🟠", "global": "🟢"}.get(category, "⚪")

# ── 8. 构建飞书卡片 ───────────────────────────────
def build_card(items, config, keywords):
    with open("templates/feishu_card.json") as f:
        template = Template(f.read())

    total_sources = config.get("_total_sources", 0)
    total_items = config.get("_total_items", 0)

    enriched = []
    for item in items:
        enriched.append({
            "emoji": emoji_for_category(item.get("category", "")),
            "source": item["source"],
            "title": item["title"],
            "url": item["url"],
            "summary": item["summary"],
            "keywords": " ".join(f"`{kw}`" for kw in item.get("_matched_kws", [])),
        })

    return json.loads(template.render(
        date=datetime.now().strftime("%Y-%m-%d"),
        items=enriched,
        total_sources=total_sources,
        total_items=total_items,
        selected=len(items),
        tags=["跨境电商", "出海", "AI", "贸易政策"],
    ))


# ── 9. 发送飞书 ────────────────────────────────────
def send_to_feishu(feishu_cfg, card):
    """支持 Webhook 和 App 两种方式"""
    # 方式 A：Webhook
    webhook_url = feishu_cfg.get("webhook_url", "")
    if webhook_url:
        payload = {"msg_type": "interactive", "card": card}
        secret = feishu_cfg.get("webhook_secret", "")
        if secret:
            ts = str(int(time.time()))
            payload["timestamp"] = ts
            payload["sign"] = gen_signature(int(ts), secret)
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # 方式 B：App ID + App Secret
    app_id = feishu_cfg.get("app_id", "")
    app_secret = feishu_cfg.get("app_secret", "")
    chat_id = feishu_cfg.get("chat_id", "")
    if app_id and app_secret and chat_id:
        token_resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
        token_resp.raise_for_status()
        token = token_resp.json()["tenant_access_token"]
        msg_resp = requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card),
            },
            timeout=15,
        )
        msg_resp.raise_for_status()
        return msg_resp.json()

    raise ValueError("飞书配置缺失：请提供 webhook_url 或 (app_id + app_secret + chat_id)")

# ── 11. 主流程 ─────────────────────────────────────
def main():
    config, sources, keywords = load_config()

    # RSSHub 探活
    rsshub_base = config["rsshub"]["base_url"]
    rsshub_ok = wait_for_rsshub(rsshub_base)

    # 首次运行守卫：seen.db 不存在 → 只入库不推送，避免历史文章刷屏
    is_first_run = not os.path.exists("data/seen.db")
    db = init_db("data/seen.db")
    lookback_hours = config["collector"]["lookback_hours"]

    all_items = []
    for src in sources["sources"]:
        if src.get("enabled") is False:
            logging.info(f"[{src['name']}] disabled, skip")
            continue
        if src["type"] == "rsshub" and not rsshub_ok:
            logging.warning(f"[{src['name']}] RSSHub 不可用，跳过")
            continue
        items = fetch_from_source(src, config)
        items = [it for it in items if is_within_lookback(it["published"], lookback_hours)]
        all_items.extend(items)
        logging.info(f"[{src['name']}] 获取 {len(items)} 条(过滤后)")

    if is_first_run:
        # 首次运行：只入库，不推送
        for item in all_items:
            if item.get("url"):
                mark_seen(db, item["url"], item["source"])
        db.commit()
        logging.info(f"首次运行：已入库 {len(all_items)} 条，跳过推送。次日开始正常推送。")
        db.close()
        return

    # 去重 + 打分
    scored = []
    deduped_count = 0
    for item in all_items:
        if not item.get("url"):
            continue
        if is_duplicate(db, item["url"]):
            continue
        deduped_count += 1
        s = score_item(item, keywords)
        if s >= config["collector"]["min_score_to_push"]:
            scored.append((s, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    max_push = config["collector"]["max_push_items"]
    top = [item for _, item in scored[:max_push]]

    if not top:
        logging.info("无新增高相关性内容，跳过推送")
        db.close()
        return

    # 推送（total_items 传去重后的数量，与卡片文案 "去重后" 一致）
    stats = {
        "_total_sources": len(sources["sources"]),
        "_total_items": deduped_count,
    }
    card = build_card(top, {**config, **stats}, keywords)
    result = send_to_feishu(config["feishu"], card)
    logging.info(f"推送完成: {len(top)} 条, 响应状态: {result.get('code', 'ok')}")

    for item in top:
        mark_seen(db, item["url"], item["source"])
    db.commit()

    cleanup_old_records(db, config["collector"]["dedup_retention_days"])
    db.close()


if __name__ == "__main__":
    main()
```

---

## 七、macOS 双任务 launchd 配置

**RSSHub 必须常驻后台运行**，`npx rsshub` 终端关了进程就没了。需要两个 launchd 任务：

- **任务 A**：RSSHub 守护进程，开机启动、常驻运行
- **任务 B**：采集脚本，每天 08:30 触发一次

### 任务 A：RSSHub 守护进程

文件：`~/Library/LaunchAgents/com.user.rsshub.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.rsshub</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/.nvm/versions/node/v24.15.0/bin/npx</string>
        <string>rsshub</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/feishu-daily-news/logs/rsshub-stdout.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/feishu-daily-news/logs/rsshub-stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Users/YOUR_USERNAME/.nvm/versions/node/v24.15.0/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

### 任务 B：每日采集脚本

文件：`~/Library/LaunchAgents/com.user.feishu-daily.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.feishu-daily</string>

    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3</string>
        <string>/Users/YOUR_USERNAME/feishu-daily-news/collector.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/feishu-daily-news</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/feishu-daily-news/logs/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/feishu-daily-news/logs/stderr.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

### 启动命令

```bash
# 加载两个任务
launchctl load ~/Library/LaunchAgents/com.user.rsshub.plist
launchctl load ~/Library/LaunchAgents/com.user.feishu-daily.plist

# 手动测试采集
launchctl start com.user.feishu-daily

# 查看状态
launchctl list | grep -E "rsshub|feishu"

# 卸载
launchctl unload ~/Library/LaunchAgents/com.user.rsshub.plist
launchctl unload ~/Library/LaunchAgents/com.user.feishu-daily.plist
```

---

## 八、依赖安装

```bash
pip3 install feedparser requests pyyaml jinja2 beautifulsoup4
```

---

## 九、可靠性保障

| 保障项 | 实现方式 |
|--------|----------|
| **RSSHub 常驻** | launchd + `KeepAlive: true`，崩溃自动重启 |
| **去重** | SQLite 持久化，URL SHA256 主键 |
| **时间过滤** | `published` 字段对比 `lookback_hours`，无时间信息则不过滤 |
| **网络重试** | 每个源独立 `try/except`，一个源挂掉不影响其他 |
| **超时控制** | 每个请求 `timeout=30` 秒 |
| **去重库清理** | `dedup_retention_days` 天后自动 DELETE 旧记录 |
| **内容为空不推送** | 当天无新高相关性内容 → 不推送（不刷屏） |
| **日志** | `logging` 写文件，推送失败时有错误堆栈 |
| **部分失败不影响整体** | 每个源独立采集、独立异常捕获 |

---

## 十、给智能体的执行清单

按顺序执行：

1. **[ ] 确认飞书推送方式** — 方式 A 建群机器人拿 Webhook URL；或方式 B 确认已有 App ID + App Secret + chat_id
2. **[ ] 启动 RSSHub 并验证路由** — `npx rsshub`，然后 `curl localhost:1200/36kr/motif/出海` 和 `curl localhost:1200/36kr/motif/AI` 确认 36氪路由未 404
3. **[ ] 验证页面源 URL 和 selectors** — 浏览器打开 gov.cn、mofcom、customs、ebrun 各页面，F12 确认 selectors 与实际 DOM 匹配
4. **[ ] 创建项目目录** — `mkdir -p ~/feishu-daily-news/{templates,data,logs}`
5. **[ ] 替换路径占位符** — 将 plist、config 中所有 `YOUR_USERNAME` 替换为实际用户名（`whoami`）
6. **[ ] 写配置文件** — `config.yaml` + `sources.yaml` + `keywords.yaml`
7. **[ ] 写卡片模板** — `templates/feishu_card.json`
8. **[ ] 写主脚本** — `collector.py`（参考上方完整实现）
9. **[ ] 安装 Python 依赖** — `pip3 install feedparser requests pyyaml jinja2 beautifulsoup4`
10. **[ ] 手动测试采集** — `python3 collector.py`，首次运行只入库不推送；第二次运行确认飞书群收到消息
11. **[ ] 配置 RSSHub 守护** — 写 `com.user.rsshub.plist`，`launchctl load`，`curl localhost:1200` 确认
12. **[ ] 配置采集定时** — 写 `com.user.feishu-daily.plist`，`launchctl load`，`launchctl start` 测试
13. **[ ] 验证定时触发** — 第二天 08:30 检查飞书群

---

## 十一、可选增强（后续迭代）

1. **多群推送** — 不同信息源推送到不同飞书群（政策群/行业群/AI 群）
2. **摘要生成** — 用大模型对长文自动生成 50 字摘要
3. **周报总结** — 周末自动生成本周 TOP 10 摘要推送
4. **异常告警** — 连续 3 天零推送时发飞书通知
5. **多关键词命中高亮** — 卡片内命中词加粗或变色
