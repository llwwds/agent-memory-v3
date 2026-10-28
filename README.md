# agent-memory-v3

基于 [MemOS](https://github.com/MemTensor/MemOS)（Apache-2.0）二次开发的 agent 长期记忆系统 v2，
目标是补齐 MemOS 云端版独有的「**按类别抽取 + 按视图分发**」管线能力（不改存储引擎），
形成 100% 本地部署、可外接自有 AI API、经 MCP 接入 Codex 等编码 agent 的记忆系统，
替代前代 [agent-memory-skill](https://github.com/llwwds/agent-memory-skill)（SQLite + 标签精确召回）。

> **修改声明**：本仓库是 MemOS v2.0.34（commit `41bf5c7f`）的修改版，依 Apache-2.0 许可证发布；
> 上游 LICENSE 原样保留，全部差异登记于 [`PATCHES.md`](./PATCHES.md)，基线信息见 [`UPSTREAM.md`](./UPSTREAM.md)。

本地维护版本：**3.2.1.0**。本次修复软删除后的检索可见性；[发布与镜像说明](docs/releases/3.2.1.0.md)。上游 Python 包版本仍保留 2.0.34。

## 一期范围：六路记忆管线

写入时按类别 key 并行抽取，读出时按类别视图分发（对齐 MemOS 云版 API 形状）：

| 路 | 类别 key | 产出契约 | 状态 |
| --- | --- | --- | --- |
| 事实 | `detail_factual` | `memory_detail_list`（memory_key/value） | M1 |
| 偏好 | `preference` | `preference_detail_list`（preference/type/reasoning） | M1 |
| 事件 | `event` | `event_detail_list`（event_key/value/time/location/roles） | M2 |
| 工具记忆 | `tool_memory` | `tool_memory_detail_list`（ToolSchema/ToolTrajectory） | M3 |
| 技能 | `skill` | `skill_detail_list`（skill_value/url/type） | M4 |
| 模态 | `image` / `document` | 先解析（OCR/转文本）再进各抽取路 | M5 |

明确不做：Profile 模板路、知识库路（内置文档 RAG）、Dashboard 治理面板。

## How to start（for your agent）

前置条件：Docker（Neo4j + Qdrant + Redis + MemOS 服务）、一个 OpenAI 兼容 LLM 端点、bge-m3 embedding。

```bash
# 部署目录在设备本地 llwwds_application/docker_file/memos/（不在本仓库内）
cd ~/llwwds_application/docker_file/memos
cp .env.example .env        # 填入 LLM/embedder 端点与密钥
docker compose up -d
docker compose ps           # 等待全部 healthy

# 最小验证：写入并召回
curl -s localhost:8000/product/add/message -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"我喜欢用 VSCode 写代码"}],"user_id":"test"}'
curl -s localhost:8000/product/search/memory -H 'Content-Type: application/json' \
  -d '{"query":"我用什么编辑器","user_id":"test"}'
```

MCP 接入（Codex，`~/.codex/config.toml`）：

```toml
[mcp_servers.memos]
command = "<venv-python 路径>"
args = ["<仓库路径>/src/memos/api/mcp_serve.py"]
```

## 文件夹定义

| 路径 | 职责 |
| --- | --- |
| `src/memos/` | 上游 MemOS 源码（基线），自有管线代码叠加其上 |
| `docker/` | 上游官方 Dockerfile 与 compose（部署副本在设备本地 `llwwds_application/docker_file/memos/`） |
| `docs/upstream/` | 被本项目替换的上游根文档存档（README/AGENTS） |
| `docs/` | 与源码版本绑定的正式文档（API 契约、部署说明） |
| `tests/` | 上游测试 + 本项目管线测试 |
| `UPSTREAM.md` / `PATCHES.md` | 上游基线登记 / 对上游的就地修改清单 |

## 设计思路

核心架构决策与数据流见本仓库 `docs/` 与知识库权威计划文档
（`obsidian_file/02 项目/021 专业项目/0211 个人项目/agent_sql_memory/计划 2026-10-03 MemOS 二次开发 v2 开发计划.md`）。

一句话本质：云版没有为类别记忆加存储层，加的是「写路径上的分类抽取」+「读路径上的分类分发」；
开源版存储层字段全在、响应模型已预定义，二次开发是填空，不是造轮子。

## 当前状态

- M0 环境与基线：进行中（基线导入完成，bug 修复与闭环验证进行中）。
- 里程碑与验收标准以知识库权威计划文档为准。

## License

Apache-2.0（继承上游 MemOS）。
