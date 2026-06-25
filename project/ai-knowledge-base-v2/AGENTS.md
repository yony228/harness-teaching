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

### 3.1 Python 部分

- 格式化/统一 lint：**ruff**（`ruff check` + `ruff format --check`），配置写入 `pyproject.toml` 的 `[tool.ruff]` 部分
- 变量/函数/模块使用 **snake_case**，类名使用 **PascalCase**
- 可见性：以 `_` 下划线开头的成员视为**私有**，其余为**公开**
- 文档：所有**公开函数/类**必须包含 **Google 风格 docstring**（含 Args/Returns/Raises 节）
- 类型提示：**全部函数签名必须包含类型注解**，集合类型使用 `typing` 模块（如 `list[str]`、`dict[str, Any]`）
- 禁止裸 `print()`，统一使用 `logging` 模块（日志等级：debug/info/warning/error/critical）

### 3.2 TypeScript 部分

- **暂定延期**：TypeScript 编码规范待补全后启用（命名约定、JSDoc、模块导入规则等）
- 接口定义以 `I` 前缀命名（如 `IKnowledgeEntry`），类型别名直接使用有意义的名称

### 3.3 跨语言通用规范

**命名约定**：
- 变量/函数使用 **camelCase**；常量使用 **UPPER_SNAKE_CASE**
- 类/接口/枚举使用 **PascalCase**，接口定义以 `I` 前缀

**可见性与文档**：
- TypeScript 中 `export` 关键字标记的成员为**公开**
- 所有**公开函数**必须包含 **JSDoc** 注释（含 `@param`/`@returns`/`@throws` 节）

**类型注解**：
- 全部函数签名必须包含类型注解，禁止使用 `any`（必要时使用 `unknown` + 类型守卫）

**输入校验（安全红线）**：
- 所有外部输入必须通过 schema 验证（Python 用 **Pydantic**，TypeScript 用 **zod**）
- 校验失败的条目写入 `knowledge/raw/<id>.json.invalid`，标记 `status: "invalid"`，需人工审核
- 禁止 `eval()`/`exec()`，禁止跳过 AI 分析直接推送

**异常处理**：
- 严禁静默忽略异常，所有 `except` 必须 `logging.exception()` 记录完整错误堆栈
- 第三方库异常若为系统内部错误，必须**包装后重新抛出**（禁用原始异常透传）
- 禁止使用全局可变状态

**工具约定**：
- Python 依赖管理优先使用 `pyproject.toml`（必要时保留 `requirements.txt` 兼容）
- Git 提交信息遵循 **[Conventional Commits](https://www.conventionalcommits.org/)** 规范（`feat:`, `fix:`, `refactor:` 等前缀）

**配置管理（红线）**：
- 禁止硬编码密钥/Token，统一通过环境变量或 `.env` 文件注入（`.env` 加入 `.gitignore`）
- 禁止在代码中硬编码 API Key 或敏感配置

### 3.4 测试与 CI

**覆盖率分层要求**：

| 层级 | 目录 | 要求 |
|------|------|------|
| 核心层 | `src/collector/`, `src/analyzer/`, `src/distributor/` | ≥ 90% 行覆盖（硬性门槛） |
| 应用层 | `src/` 其他目录 | ≥ 75% 软阈值（标红不阻断） |

- Python 侧：`pytest --cov=src/`，排除 `tests/`、`node_modules/`、`dist/`
- TypeScript 侧：`vitest --coverage`（c8 引擎，排除方式同上）
- 允许 `pytest.mark.xfail`，但 xfail 超过 5 条时禁止合并

**CI 触发与阻断**：
- 每次 push 提交分支 + 创建 PR 时自动触发
- 阻断命令：`ruff check src/ && ruff format --check src/ && pytest src/`
- 阻断条件：`ruff check` 的 E/C/F 级别错误未通过 / `ruff format --check` 不通过 / 测试未全部通过
- Warning 级别不阻断，xfail > 5 条不阻断但禁止合并

### 3.5 红线（绝对禁止）

- **禁止**在代码中硬编码 API Key 或敏感配置
- **禁止**向公共仓库提交 `config/` 下的配置文件
- **禁止**跳过 AI 分析直接将原始数据推送到用户渠道
- **禁止**使用 `eval()` 或 `exec()` 处理外部输入数据
- **禁止**未经人工审核向已验证用户渠道发送内容
- **禁止**使用全局可变状态，所有 Agent 状态必须通过 LangGraph 显式管理
- **禁止**忽略异常静默失败，必须记录完整错误堆栈

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
  "id": "github-20260317-001",
  "title": "GPT-4 多模态能力突破",
  "source_type": "github_trending | hacker_news",
  "source_url": "https://github.com/xxx/yyy",
  "summary": "简短描述技术亮点（50-200字）",
  "tags": ["llm", "multimodal", "openai"],
  "status": "draft | review | published | archived",
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
