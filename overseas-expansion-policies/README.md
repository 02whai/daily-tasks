# 飞书每日情报推送

每天 08:30 自动采集跨境贸易政策与行业动态，通过飞书卡片推送到群聊。

## 架构

```
launchd (08:30)
    │
    ▼
collector.py
    ├── _determine_run_mode()   ── daily / weekly / monthly
    ├── 7 个信息源并行采集       ── json_api / page / playwright / rss
    ├── 时间过滤 → URL+标题去重 → 关键词打分 → 排序截断
    ├── LLM 简报生成             ── MiniMax M3 (可选)
    └── 飞书卡片推送             ── Webhook / API
```

## 运行模式

| 模式 | 触发条件 | 回溯 | 条数 | 卡片标题 |
|------|---------|------|------|---------|
| 每日情报 | 非周一、非 1 号 | 24h | ≤5 | 每日情报 |
| 每周精选 | 周一 (非 1 号) | 7 天 | 5 | 每周精选 |
| 月度精选 | 每月 1 号 | 30 天 | 10 | 月度精选 |

1 号恰逢周一 → 月度优先（30 天回溯已覆盖周范围）。

## 信息源

| 来源 | 类型 | 分类 |
|------|------|------|
| 国务院-最新政策 | json_api | policy |
| 国务院-政策解读 | json_api | policy |
| 商务部-政策发布 | page | policy |
| 海关总署-海关法规 | playwright | policy |
| 亿邦动力 | playwright | industry |
| WTO-News | rss | global |
| USTR-贸易政策 | page | policy |

## 打分规则

- 标题命中 `high_priority` 关键词 → +3 分/词
- 标题命中 `medium_priority` 关键词 → +1 分/词
- 来源 category 为 `policy` → +2 分（官方源加权）
- ASCII 关键词（如 AI、SaaS）使用词边界匹配，防止误匹配 MAIL 等
- 只推送总分 ≥ `min_score_to_push` (3 分) 的条目

关键词配置在 `keywords.yaml`。

## 去重

两级去重，SQLite 持久化：
1. **URL 哈希** — `seen_items` 表，SHA256 主键
2. **标题哈希** — `seen_titles` 表，同内容不同 URL 也能识别

30 天自动清理过期记录。

## 快速开始

```bash
# 安装依赖
pip3 install -r requirements.txt
playwright install chromium

# 配置
cp config.yaml config.yaml  # 编辑占位符，或不改（通过 .env 注入）
cp .env.example .env        # 填写实际密钥

# 首次运行（只入库不推送）
python3 collector.py

# 第二次及以后（正常推送）
python3 collector.py

# 运行测试
python3 -m pytest tests/ -v
```

## 配置

### .env（密钥，不提交 Git）

```
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
FEISHU_WEBHOOK_SECRET=
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.minimax.chat
LLM_MODEL=MiniMax-M3
```

### config.yaml（非敏感配置）

- `collector.lookback_hours`: 每日回溯时间 (24h)
- `collector.min_score_to_push`: 最低推送分数 (3)
- `collector.max_push_items`: 每日最多推送条数 (5)
- `collector.dedup_retention_days`: 去重记录保留天数 (30)

### sources.yaml（信息源）

每个源的结构：
```yaml
- name: "来源名称"
  type: json_api | page | playwright | rss | rsshub
  url: "..."
  category: policy | industry | global
  enabled: true       # false 则跳过
  lookback_hours: 48  # 可选，覆盖全局 lookback
```

## 定时任务

macOS launchd，配置文件：`~/Library/LaunchAgents/com.user.feishu-daily.plist`

```bash
# 手动触发
launchctl kickstart gui/$(id -u)/com.user.feishu-daily

# 查看状态
launchctl print gui/$(id -u)/com.user.feishu-daily
```

## 文件结构

```
overseas-expansion-policies/
├── collector.py             # 主脚本 (535 行)
├── config.yaml              # 非敏感配置
├── sources.yaml             # 信息源定义
├── keywords.yaml            # 关键词与权重
├── requirements.txt         # Python 依赖
├── .env                     # 密钥 (gitignored)
├── templates/
│   └── feishu_card.json     # 飞书卡片 Jinja2 模板
├── tests/
│   └── test_collector.py    # 41 条单元测试
├── data/
│   └── seen.db              # SQLite 去重库 (自动创建)
└── logs/
    └── collector.log        # 运行日志
```

## 测试

41 条测试覆盖：打分、时间解析、摘要清洗、去重、模式决策、词边界匹配、简报生成。
