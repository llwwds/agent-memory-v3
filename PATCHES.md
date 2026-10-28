# PATCHES — 对上游 MemOS 的就地修改清单

本仓库采用固定版本 vendoring（基线见 `UPSTREAM.md`）。凡对上游源码树的就地修改
（包括 bug 修复、文件替换、配置调整）逐条登记于此：**文件 + 修改点 + 理由**，
保证升级对比上游新 tag 时可逐条重验。

格式：`| # | 文件 | 修改点 | 理由 | 对应 commit |`

## 活跃清单

| # | 文件 | 修改点 | 理由 | commit |
| --- | --- | --- | --- | --- |
| P1 | `AGENTS.md` | 上游原文件移至 `docs/upstream/AGENTS.md`，替换为本项目 Agent 操作契约 | 项目规范要求根部 AGENTS.md 为本项目的操作契约；上游版本留档供升级比对 | 项目叠层 commit |
| P2 | `README.md` / `README_ZH.md` | 上游原文件移至 `docs/upstream/`，`README.md` 替换为本项目说明 | 本仓库是独立公开仓库，根 README 须描述本项目而非上游产品 | 项目叠层 commit |
| P3 | `.gitignore` | 追加本项目忽略项（`.env` 已由上游覆盖，确认无遗漏后按需追加） | 防止密钥与本地状态入库 | 项目叠层 commit |
| P4 | `.github/workflows/`（整目录移除） | 删除上游 12 个 CI/发布 workflow | 均为上游 MemTensor 的 release 流水线，引用上游 secrets 与 runner，在本 fork 中不可运行且无意义；`.github/` 其余（issue 模板、scripts）保留 | `1701156` |
| B3 | `src/memos/api/routers/server_router.py` | 新增 `_verify_api_key` 依赖并挂到 `/product` router：`MEMOS_API_KEY` 设置时强制校验 `X-API-Key` 头，未设置时保持上游开放行为 | companmem 审计 bug ③（2026-10-03 于 v2.0.34 运行时复现：任意 user_id 可读写他人记忆）；单机回环部署风险低，暴露端口前必须修 | 本 commit |
| M1-1 | `src/memos/api/product_models.py` | `APIADDRequest` 新增 `allow_memory_view: list[str] \| None`；`APISearchRequest` 新增 `include_memory_view: list[str] \| None` | M1 视图开关契约（写路径按类别抽取、读路径按视图返回；None=全部），对齐云版按类别 key 的管线形状 | 本 commit |
| M1-2 | `src/memos/mem_reader/multi_modal_struct.py` | 新增 `_submit_category_extraction` helper；`_process_multi_modal_data` 与 `_process_transfer_multi_modal_data` 中原 `process_preference_fine` future 改经该 helper 分发 | v2 类别抽取框架（`MEMOS_CATEGORY_EXTRACT` on 时走 `category_extract.runner`，off 时原样回落上游 pref 管线，避免双抽取）；为 M2–M4 类别接入统一入口 | 本 commit |
| M1-3 | `src/memos/multi_mem_cube/single_cube.py` | `_process_text_mem` 的 `get_memory` 调用透传 `allow_memory_view`；async 路径 `_schedule_memory_tasks` 的 message info 携带 `allow_memory_view` | 请求级类别 allow-list 贯通 sync/async 两条写入链路 | 本 commit |
| M1-4 | `src/memos/mem_scheduler/task_schedule_modules/handlers/mem_read_handler.py` | `_process_memories_with_reader` 从 info 弹出 `allow_memory_view` 并作为 kwarg 传入 `fine_transfer_simple_mem` | 异步 add 链路把请求级 allow-list 传到 reader 抽取层 | 本 commit |
| M1-5 | `src/memos/api/handlers/search_handler.py` | `handle_search_memories` 返回前对 data 调 `apply_memory_views` | 读路径派生云版形状 `*_detail_list` 类别视图（M1：memory/preference，其余占位），既有分桶保留向后兼容 | 本 commit |
| M1-6 | `src/memos/api/handlers/memory_handler.py` | `handle_get_memories` 返回前对 data 调 `apply_memory_views`（按请求 include_* 开关映射视图） | 同 M1-5，覆盖 get_memory 读路径 | 本 commit |
| M3-1 | `src/memos/mem_reader/read_multi_modal/system_parser.py` | `SystemParser.parse_fast`：system 消息含可解析 `<tool_schema>` 时不再返回空列表，改为产出一个携带 system source（含 tool_schema 原文）的 fast 占位节点（memory=压缩后的 `<tool_schema>` 标记文本） | 上游接缝缺陷：system 消息在 fast 阶段恒返空且不产生 source，导致 Part B `process_transfer` 永远到不了 `parse_fine`，add 管线中 ToolSchemaMemory 是不可达死代码（实测复现）；占位节点使窗口携带 system source 走既有 parse_fine 通路，schema 节点随 fast 节点生命周期被清理替换 | 本 commit |
| M5-1 | `src/memos/memories/textual/item.py` | `TextualMemoryMetadata` 新增 `info`/`internal_info` 的 before-validator：字符串形式的 JSON 对象容错反序列化 | 上游序列化回路 bug（v1 数据灌入时实测复现）：neo4j 节点属性不能存嵌套 dict，写入时 `internal_info` 被 stringify，读回时 pydantic dict 校验失败并**拖垮命中该节点的整次 search**（_search_text 整体异常返回空）；仅当节点经窗口合并（携带 ingest_batch_id）时出现，直灌迁移的超长记录 tools#151 触发 | 本 commit |

## 新增自有模块（非上游修改，不占 PATCH 条目，列出便于升级时识别）

| 位置 | 说明 |
| --- | --- |
| `UPSTREAM.md` / `PATCHES.md` | 基线与差异登记 |
| `docker/Dockerfile.v2` | 本项目 API 镜像（上游 `docker/Dockerfile` 保留未动） |
| `docker/.env.example-v2` | 本项目部署脱敏模板（上游 `.env.example*` 保留未动） |
| `apps/api-mcp-bridge/` | stdio MCP → `/product` HTTP API 桥接（上游 `mcp_serve.py` 为进程内 MOS，绕开 API 管线，不满足 v2 需求） |
| `src/memos/mem_reader/category_extract/` | v2 按类别抽取框架：registry（类别 key→视图字段）+ prompts + runner（M1 preference、M2 event 为框架抽取器；detail_factual/tool_memory/skill 为委托登记，抽取仍由上游对应提取器承担） |
| `src/memos/api/v2_views.py` | v2 读路径视图分发：桶结果 → 云版形状 `*_detail_list` |
| `scripts/pour_v1_snapshot.py` | v1 冻结快照 → v2 直灌脚本（sync+fast 原生通道，零 LLM、1 记录 1 add、v1_source/v1_id 落节点属性、echo 校验、skip-ids 幂等重跑）；取代早期的 LLM 抽取式 `migrate_v1_to_v2.py`（已删除，见 git 历史） |
| `docs/upstream/` | 被替换的上游根文档存档 |

## M0 必修 bug 复现结论（companmem 审计清单 vs v2.0.34，2026-10-03）

| # | 结论 | 依据 |
| --- | --- | --- |
| B1 `_vector_recall` 断引用 | **上游已修，不复现** | 静态：`recall.py` 中 `_vector_recall` 定义（L370）与调用（L144/212/232）均正常，`_vector_recall_ORIGINAL` 已不存在；运行时：qdrant 集合 `neo4j_vec_db` 写入/检索正常（1024 维 bge-m3） |
| B2 delete 不生效 | **上游已修（召回层面），不复现** | 运行时：`/product/delete_memory` 删除后 search 返回 0、neo4j 节点移除。残留卫生问题：qdrant 孤儿向量不清理（读取时因图库回源取全量节点而被屏蔽，不影响正确性；不做存储引擎改动，仅记录） |
| B3 `/product` 无鉴权 | **复现 → 已修复** | 复现：无凭据可 `POST /product/search` 读写他人 cube；修复见活跃清单 B3（无 key 401 / 错 key 401 / 对 key 200 / `/health` 不受影响） |

## 已关闭（上游已修 / 决定不修）

| # | 原因 |
| --- | --- |
| B1 | 上游 v2.0.34 已修（见复现结论） |
| B2 | 上游 v2.0.34 已修召回正确性；孤儿向量残留仅卫生问题，按「不改存储引擎」原则不修 |


## 3.2.1.0 — 检索软删除可见性修复（2026-10-28）

| # | 文件 | 修改点 | 理由 | commit |
| --- | --- | --- | --- | --- |
| B4-1 | `src/memos/graph_dbs/neo4j_community.py` | 向量召回全部分支回 Neo4j 按权威 status 筛选；显式状态与 priority 状态独立 AND；保留 cube、字段投影和向量排序 | 软删与恢复不更新 Qdrant 状态，旧 payload 不能决定检索可见性；同时覆盖 Context 直接投影分支 | 本版本提交 |
| B4-2 | `src/memos/graph_dbs/neo4j_community.py` | `get_all_memory_items` 落实可选 status，默认仍保留原始枚举 | WorkingMemory 的 activated 条件此前被 kwargs 吞掉 | 本版本提交 |
| B4-3 | `src/memos/memories/textual/tree_text_memory/retrieve/recall.py` | 慢速图召回的 key/tag 两路补传 activated | 在重排或模型上下文构造之前排除软删候选 | 本版本提交 |

不新增字段、不迁移数据、不重建 embedding；原始按 ID 读取与恢复协议保持不变。功能验收与本地镜像构建说明见 `docs/releases/3.2.1.0.md`。
