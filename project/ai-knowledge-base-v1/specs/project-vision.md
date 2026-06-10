# AI 知识库 · 项目愿景 v0.2

## 要做什么

- 每天抓取 GitHub Trending 中 AI 相关项目，限 20 条
- 用 Agent 分析每条收录内容，输出三项指标：
  1. 技术类别
  2. 创新点
  3. 使用难度
- 输出为 JSON 格式的知识条目

## JSON 条目结构

```json
{
  "rank": "int  — 本页排名",
  "repo_url": "string — 仓库链接",
  "repo_name": "string — 仓库名称",
  "readme_content": "string — README 全文或摘要",
  "analysis": {
    "tech_category": "string",
    "innovation": "string",
    "difficulty": "string"
  }
}
```

## 不做什么

- 不对抓取的文章内容做质量或准确性尝试
- 不做用户界面、Web 服务、数据存储持久化（除 JSON 文件外）
- 不做用户系统、认证、评论等社交平台功能

## 边界 & 验收

- 入口：GitHub Trending 页面（不限语言，仅按标题/描述/标签自动筛选 AI 相关内容）
- 日粒度：每天一次抓取，持续 7 天
- 每天最多收集 20 条（20 repositories）
- 输出目录结构固定（如 `data/YYYY-MM-DD.json`）
- 验收标准：
  1. 每个 JSON 条目必须符合上述 schema
  2. 连续 7 天任务按计划完成，间隔不超过 24h
  3. 数据可追溯（文件名含日期，内容含 repo_url）

## 怎么验证

- 脚本读取 `data/` 目录下所有 JSON 文件
- 校验每个条目是否包含必需字段且类型正确
- 检查日期范围内（7天）每天是否存在至少一条记录
