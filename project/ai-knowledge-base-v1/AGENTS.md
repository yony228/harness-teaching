# AI 知识库助手 AGENTS.md

## 1. 项目概述
本项目是一个 AI 驱动的自动化知识采集与分析系统，从 GitHub Trending 和 Hacker News 实时采集 AI/LLM/Agent 领域的技术动态，经 AI 分析后结构化存储为 JSON 格式的知识条目，并通过 Telegram/飞书等渠道自动分发，构建持续更新的 AI 技术知识库。

## 2. 技术栈
- **Python 3.12**：核心开发语言
- **OpenCode + 国产大模型**：AI 分析与内容生成
- **LangGraph**：工作流编排与 Agent 状态管理
- **OpenClaw**：多渠道消息分发（Telegram/飞书）
- **SQLite/JSON**：轻量级数据存储

## 3. 编码规范
- 遵循 **PEP 8** 编码风格
- 变量/函数使用 **snake_case**
- 所有公共函数必须包含 **Google 风格 docstring**
- **禁止**使用裸 `print()`，统一使用 `logging` 模块
- 类型提示：**全部函数签名必须包含类型注解**
- 依赖管理：使用 `requirements.txt` 或 `pyproject.toml`

## 4. 项目结构
```
project/ai-knowledge-base-v1/
├── .opencode/
│   ├── agents/          # Agent 定义文件
│   └── skills/          # 自定义技能模块
├── knowledge/
│   ├── raw/             # 原始采集数据
│   └── articles/        # 分析后的结构化知识条目
├── src/
│   ├── collector/       # 数据采集模块
│   ├── analyzer/        # AI 分析模块
│   └── distributor/     # 渠道分发模块
├── tests/               # 单元测试
├── config/              # 配置文件
└── AGENTS.md            # 本文件
```

## 5. 知识条目 JSON 格式
```json
{
  "id": "uuid-v4-string",
  "title": "GPT-4 多模态能力突破",
  "source_type": "github_trending | hacker_news",
  "source_url": "https://github.com/xxx/yyy",
  "summary": "简短描述技术亮点（50-200字）",
  "tags": ["llm", "multimodal", "openai"],
  "status": "collected | analyzing | published | archived",
  "created_at": "2024-01-15T08:30:00Z",
  "updated_at": "2024-01-15T09:00:00Z",
  "publish_channels": ["telegram", "feishu"],
  "metadata": {
    "stars": 5000,
    "comments": 120,
    "authors": ["user1", "user2"]
  }
}
```

## 6. Agent 角色概览

| 角色 | 职责 | 触发条件 | 输出 |
|------|------|----------|------|
| **采集 Agent** | 定时抓取 GitHub Trending 和 Hacker News 原始数据 | 每 12 小时定时执行 | 原始 JSON 数据 → `knowledge/raw/` |
| **分析 Agent** | 对原始数据进行 AI 分类、摘要生成、标签提取 | 新数据到达时触发 | 结构化知识条目 → `knowledge/articles/` |
| **整理 Agent** | 去重、归档、多渠道分发到 Telegram/飞书 | 分析完成后触发 | 渠道推送消息 + 状态更新 |

## 7. 红线（绝对禁止）
- **禁止**在代码中硬编码 API Key 或敏感配置
- **禁止**向公共仓库提交 `config/` 下的配置文件
- **禁止**跳过 AI 分析直接将原始数据推送到用户渠道
- **禁止**使用 `eval()` 或 `exec()` 处理外部输入数据
- **禁止**未经人工审核向已验证用户渠道发送内容
- **禁止**使用全局可变状态，所有 Agent 状态必须通过 LangGraph 显式管理
- **禁止**忽略异常静默失败，必须记录完整错误堆栈
