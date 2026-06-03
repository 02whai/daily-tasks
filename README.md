# Daily Tasks

自动化信息推送系统，每天定时采集数据并推送飞书卡片消息。

## 架构

```mermaid
flowchart TB
    subgraph Schedule["macOS launchd 调度"]
        L1["com.user.feishu-daily<br/>每天 08:30"]
        L2["com.user.github-popular-projects<br/>每天 08:40"]
    end

    L1 --> OP
    L2 --> GP

    subgraph OP["overseas-expansion-policies 海外扩张政策采集"]
        direction TB
        A1["7 个信息源"] --> A2["采集引擎"]
        A2 --> A3["SQLite 去重"]
        A3 --> A4["关键词打分"]
        A4 --> A5["LLM 简报生成<br/>(MiniMax M3)"]
        A5 --> A6["Jinja2 卡片渲染"]
        A1_sources["国务院 · 商务部 · 海关总署<br/>亿邦动力 · WTO · USTR"] -.-> A1
    end

    subgraph GP["GitHub-Popular-Projects GitHub 热门项目"]
        direction TB
        B1["GitHub Trending 页面"] --> B2["爬取解析<br/>(requests + BeautifulSoup)"]
        B2 --> B3["模式判断<br/>周一→本周热门 / 其他→今日热门"]
        B3 --> B4["内联卡片构建"]
        B1_url["github.com/trending<br/>?since=daily | weekly"] -.-> B1
    end

    A6 --> FS["飞书 Webhook"]
    B4 --> FS

    FS --> FC["飞书群消息卡片"]

    style Schedule fill:#f5f5f5,stroke:#999
    style OP fill:#e3f2fd,stroke:#1976d2
    style GP fill:#e8f5e9,stroke:#388e3c
    style FS fill:#fff3e0,stroke:#f57c00
    style FC fill:#fce4ec,stroke:#c62828
```

## 项目结构

```
Daily-Tasks/
├── overseas-expansion-policies/   # 海外扩张政策采集
│   ├── collector.py               # 主脚本（采集→去重→打分→简报→推送）
│   ├── config.yaml                # 采集参数、飞书配置
│   ├── sources.yaml               # 7 个信息源定义
│   ├── keywords.yaml              # 打分关键词
│   ├── requirements.txt           # Python 依赖
│   ├── templates/
│   │   └── feishu_card.json       # 飞书卡片 Jinja2 模板
│   └── tests/
│       └── test_collector.py      # 41 个单元测试
│
├── GitHub-Popular-Projects/       # GitHub 热门项目推送
│   ├── collector.py               # 主脚本（爬取→判断模式→构建卡片→推送）
│   ├── requirements.txt           # Python 依赖
│   └── tests/
│       └── test_collector.py      # 9 个单元测试
│
└── .gitignore
```

## 两个子项目对比

| | overseas-expansion-policies | GitHub-Popular-Projects |
|---|---|---|
| **用途** | 海外扩张政策情报 | GitHub 热门项目 |
| **频率** | 每日 / 每周 / 每月 | 每日 / 每周 |
| **数据来源** | 国务院、商务部、海关总署、亿邦动力、WTO、USTR 等 7 个源 | GitHub Trending 页面 |
| **采集方式** | RSS、JSON API、Playwright 浏览器、静态页面 | requests + BeautifulSoup |
| **处理流程** | 去重 → 打分 → LLM 简报 → 卡片渲染 | 直接构建卡片 |
| **飞书卡片** | Jinja2 模板渲染 | Python 内联构建 |
| **依赖数** | 6 个 | 2 个 |
| **推送时间** | 08:30 | 08:40 |

## 运行模式

```
周一     → 每周精选（overseas）+ 本周热门（GitHub）
周二~周日 → 每日情报（overseas）+ 今日热门（GitHub）
每月1日  → 月度精选（overseas）+ 本周/今日热门（GitHub）
```

## 环境要求

- Python 3.14+
- macOS（使用 launchd 调度）

```bash
# 海外政策采集
pip3 install -r overseas-expansion-policies/requirements.txt
playwright install chromium

# GitHub 热门项目
pip3 install -r GitHub-Popular-Projects/requirements.txt
```

## 日志

所有运行时日志存放在各自项目的 `logs/` 目录下，launchd 的 stdout/stderr 也分别重定向到 `logs/stdout.log` 和 `logs/stderr.log`。
