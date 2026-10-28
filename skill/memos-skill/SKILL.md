---
name: memos-skill
description: >-
  通过本地 MemOS API（agent-memory-v3）读写 agent 长期记忆：按视图检索
  （事实/偏好/事件/工具记忆/技能）、写入记忆、召回踩坑记录、统计与健康检查。
  当 agent 需要 remember/recall 跨会话记忆、动手前查相关踩坑、查询用户偏好与
  项目背景、或维护 MemOS 记忆库时使用。后端为本地部署的 MemOS 服务
  （四容器栈），是 agent-memory-skill（v1，SQLite）迁移后的新记忆系统。
metadata:
  short-description: "MemOS 长期记忆读写（v2），视图检索 + 踩坑召回 + 记忆写入"
---

# MemOS Memory Skill

通过本地 MemOS `/product` API 读写长期记忆。v1（agent-memory-skill，SQLite）的
609 条历史记忆已直灌本系统；v1 保留为 fallback 直至 M6 对比验收通过。

## 前置条件

- 本地 MemOS 栈运行中（`~/llwwds_application/docker_file/memos/`，端口 127.0.0.1:8000）。
- 配置（优先级：环境变量 > `~/.memos-skill.env`，文件权限 600）：

```
MEMOS_API_BASE=http://127.0.0.1:8000
MEMOS_API_KEY=<部署目录 .env 中的 MEMOS_API_KEY>
```

密钥红线：key 只存在于部署 .env 与 ~/.memos-skill.env，绝不进源码/文档/commit。

## 命令

```bash
S=~/.agents/skills/memos-skill/scripts/memos.py

# 检索（自动分视图返回：memory/preference/event/tool_memory/skill）
python3 $S search "agent_sql_memory 项目的部署栈是什么"
python3 $S search "我的包管理器偏好" --view preference
python3 $S search "渲染任务" --top-k 10 --json

# 踩坑召回：动手做任务前先查相关踩坑（对齐 v1 的 G 规则）
python3 $S pitfalls "通过 ssh 在远端 4090 上编排下载与渲染"

# 写入（默认走完整抽取管线，自动分类到各视图）
python3 $S add "用户决定 v2 的 MCP 接入一律走 api-mcp-bridge，不用上游 mcp_serve.py"

# 原文直存（不抽取，用于已结构化的记录）
python3 $S add "[v1-tool #20] browser-harness 部署记录…" --fast

# 软删除与恢复（按记录精确删除；record_id 取 search 结果里的 id，删除时也会回显）
python3 $S delete <record_id>
python3 $S recover <record_id>

# 统计与健康
python3 $S stats
python3 $S health
```

## 行为约定

- **动手前查坑**：执行部署、迁移、删改、外部服务编排类任务前，先用
  `pitfalls "<任务关键词>"` 召回相关踩坑记录；命中则先读再动。
- **写入选择**：对话中产生的偏好/决策/事实用默认 `add`（抽取管线分类）；
  已结构化的记录（带 [v1-*] 标记的原文等）用 `add --fast` 原文直存。
- **检索语义**：v2 是语义+全文混合召回，不依赖标签精确匹配；历史踩坑的
  event/device 标签已编入正文文本，按语义描述即可召回。
- **删除语义**：`delete <record_id>` 为按记录软删除，结果可 `recover`（按删除返回的
  `record_id` 批次恢复，此时该 id 即被删记录的 id）；硬删（`hard_delete`）不进 CLI，
  只留在 API 层。自本地版本 3.2.1.0 起，删除提交后新发起的搜索按 Neo4j
  权威状态排除软删记录；支持删除再重写。原始按 ID 读取仍保留管理语义。
- **与 MCP 的关系**：Codex 已配置 `[mcp_servers.memos]`（stdio 桥接，工具
  remember/recall）；本 skill 提供同一后端的 CLI 访问与更细的视图控制，
  两者读写同一记忆库，可并存。
