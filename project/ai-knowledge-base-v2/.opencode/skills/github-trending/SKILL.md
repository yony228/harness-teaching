---
name: github-trending
description: 当需要采集 GitHub 热门开源项目时使用此技能
allowed-tools: Read, Grep, Glob, WebFetch
---

# GitHub Trending 采集技能

## 使用场景

- 定时采集 GitHub Trending 仓库的 AI/LLM/Agent 领域动态
- 从 GitHub API 获取每日/每周热门项目数据
- 为知识库系统提供结构化的采集数据源
- 触发条件：每 12 小时定时执行

## 执行步骤

### 步骤 1：获取 GitHub Trending 原始数据

使用 `WebFetch` 抓取 GitHub Trending 页面：

```
WebFetch: https://github.com/trending?since=daily&spoken_language_code=
```

或使用 GitHub API（需通过环境变量 `GITHUB_TOKEN` 注入认证）：

```
WebFetch: https://api.github.com/trending?since=daily
```

### 步骤 2：提取仓库信息

从返回的 HTML 或 JSON 数据中提取以下字段：

- 仓库名称（含 owner/repo）
- 仓库 URL
- Star 数量
- 编程语言
- Topics/Tags
- 简短描述

### 步骤 3：过滤筛选

**纳入标准**（任一满足）：
- 项目描述或 topics 包含 AI / LLM / Agent / Machine Learning / NLP / Computer Vision / Generative AI 等关键词
- 语言为 Python/TypeScript/Go 且与 AI 生态相关

**排除规则**：
- 匹配 `awesome-` 前缀的仓库（如 awesome-python、awesome-llm 等）
- Star 数少于 100 的仓库

### 步骤 4：去重

与 `knowledge/raw/` 目录下的历史文件进行比对，排除已采集的仓库。比对关键字段：`name` + `url`。

### 步骤 5：撰写中文摘要

对每个通过筛选的仓库，按以下公式生成中文摘要：

> **项目名**：做什么（功能/定位）— 为什么值得关注（数据亮点/技术特色/社区热度）

摘要要求：
- 中文表达，简明扼要
- 长度 50-200 字
- 不出现英文技术名的直译堆砌，需自然流畅

### 步骤 6：排序取 Top15

按 Star 数从高到低排序，取前 15 个项目。

### 步骤 7：输出 JSON 文件

生成文件路径：

```
knowledge/raw/github-trending-YYYY-MM-DD.json
```

格式要求：

```json
{
  "source": "github_trending",
  "skill": "github-trending",
  "collected_at": "YYYY-MM-DDTHH:MM:SSZ",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "summary": "中文摘要（50-200字）",
      "stars": 数字,
      "language": "语言名",
      "topics": ["topic1", "topic2"]
    }
  ]
}
```

## 注意事项
- GitHub API 未认证限频 10 次/分钟
- 摘要必须是中文
- 不编造不存在的仓库
- 每次采集前检查文件是否存在，避免覆盖已有数据
- 异常必须通过 `logging` 记录完整错误堆栈，禁止静默失败
- 采集的内容**必须**经过 AI 分析 Agent 二次审核后，才允许推送到分发渠道
- 如果 GitHub 请求受限（rate limit），降级使用 WebFetch 抓取 HTML 页面解析
- 输出的 JSON 必须通过 Pydantic schema 校验，不合格的数据标记为 `invalid`
