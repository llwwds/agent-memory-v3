"""MemOS memory skill CLI (agent-memory-v3 companion skill).

Zero-dependency (urllib only) command line access to the local MemOS /product
API for agents: view-routed search, memory writing, soft delete/recover,
pitfall recall, stats and health. Configuration (in priority order):

1. environment: MEMOS_API_BASE, MEMOS_API_KEY
2. config file: ~/.memos-skill.env (KEY=VALUE lines, chmod 600)

Never hardcode the API key anywhere; the key lives in the deployment .env and
optionally in ~/.memos-skill.env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, request


DEFAULT_API_BASE = "http://127.0.0.1:8000"
CONFIG_FILE = Path.home() / ".memos-skill.env"
TIMEOUT = 90

VIEW_FIELDS = (
    "memory_detail_list",
    "preference_detail_list",
    "event_detail_list",
    "tool_memory_detail_list",
    "skill_detail_list",
)


def load_config() -> tuple[str, str]:
    base = os.getenv("MEMOS_API_BASE", "").strip()
    key = os.getenv("MEMOS_API_KEY", "").strip()
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            k, v = line.split("=", 1)
            if not base and k.strip() == "MEMOS_API_BASE":
                base = v.strip()
            if not key and k.strip() == "MEMOS_API_KEY":
                key = v.strip()
    return (base or DEFAULT_API_BASE).rstrip("/"), key


def post(api_base: str, key: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    req = request.Request(f"{api_base}{path}", data=body, headers=headers, method="POST")
    with request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(api_base: str, key: str, path: str) -> dict:
    headers = {}
    if key:
        headers["X-API-Key"] = key
    req = request.Request(f"{api_base}{path}", headers=headers, method="GET")
    with request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def print_views(data: dict, top_k: int) -> None:
    """Human-readable view-routed output for a search/get response."""
    printed = 0
    for field in VIEW_FIELDS:
        items = data.get(field) or []
        if not items:
            continue
        print(f"## {field} ({len(items)})")
        for item in items[:top_k]:
            printed += 1
            score = item.get("relativity")
            score_s = f" [rel={float(score):.3f}]" if isinstance(score, (int, float)) else ""
            print(f"- {item.get('id', '')[:8]}{score_s}")
            for key, val in item.items():
                if key in ("id", "relativity", "status", "conversation_id", "create_time", "update_time"):
                    continue
                if val in (None, "", []):
                    continue
                text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
                print(f"    {key}: {text[:400]}")
    if not printed:
        print("(no matching memories)")


def cmd_search(args: argparse.Namespace) -> int:
    base, key = load_config()
    payload: dict = {"query": args.query, "user_id": args.user_id, "top_k": args.top_k}
    if args.view:
        payload["include_memory_view"] = args.view.split(",")
    if args.relativity is not None:
        payload["relativity"] = args.relativity
    result = post(base, key, "/product/search", payload)
    data = result.get("data") or {}
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print_views(data, args.top_k)
    return 0


def cmd_pitfalls(args: argparse.Namespace) -> int:
    """Recall pitfall records ([v1-pitfall ...] markers) for a task at hand."""
    base, key = load_config()
    result = post(base, key, "/product/search", {"query": args.query, "user_id": args.user_id, "top_k": 20})
    data = result.get("data") or []
    facts = data.get("memory_detail_list") or []
    hits = [f for f in facts if str(f.get("memory_value", "")).startswith("[v1-pitfall")]
    if args.json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
    elif not hits:
        print("(no pitfall records matched; safe to proceed)")
    else:
        print(f"## pitfall records ({len(hits)}) — read before acting:")
        for f in hits[: args.top_k]:
            print(f"- {f.get('id', '')[:8]}: {(f.get('memory_value') or '')[:500]}")
            print()
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    base, key = load_config()
    payload: dict = {"user_id": args.user_id, "messages": [{"role": "user", "content": args.text}]}
    if args.fast:
        # direct verbatim storage (sync fast mode, no LLM extraction)
        payload["async_mode"] = "sync"
        payload["mode"] = "fast"
        payload["messages"] = [{"type": "text", "text": args.text}]
    result = post(base, key, "/product/add", payload)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("code") == 200 else 1
    print(f"add: code={result.get('code')} message={result.get('message')}")
    for m in result.get("data") or []:
        print(f"- [{m.get('memory_type')}] {m.get('memory_id')} {(m.get('memory') or '')[:80]}")
    return 0 if result.get("code") == 200 else 1


def cmd_delete(args: argparse.Namespace) -> int:
    """软删除指定记录（按记录精确删）。hard_delete 恒为 false：硬删只保留在 API 层，不进 CLI。"""
    base, key = load_config()
    result = post(base, key, "/product/delete_memory_by_record_id", {
        "mem_cube_id": args.user_id,
        "record_id": args.record_id,
        "hard_delete": False,
    })
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("code") == 200 else 1
    print(f"delete: code={result.get('code')} message={result.get('message')}")
    for k, v in (result.get("data") or {}).items():
        print(f"- {k}: {v}")
    # 回显输入 id，作为后续 recover 的句柄
    print(f"- record_id: {args.record_id}")
    print(f"  recover with: recover {args.record_id}")
    print("  note: search 召回链路暂未过滤已软删记忆，验证删除以 stats/get_memory 为准")
    return 0 if result.get("code") == 200 else 1


def cmd_recover(args: argparse.Namespace) -> int:
    """恢复软删除的记忆（按删除时记录的 id）。"""
    base, key = load_config()
    result = post(base, key, "/product/recover_memory_by_record_id", {
        "mem_cube_id": args.user_id,
        "delete_record_id": args.delete_record_id,
    })
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("code") == 200 else 1
    print(f"recover: code={result.get('code')} message={result.get('message')}")
    for k, v in (result.get("data") or {}).items():
        print(f"- {k}: {v}")
    print(f"- delete_record_id: {args.delete_record_id}")
    return 0 if result.get("code") == 200 else 1


def cmd_stats(args: argparse.Namespace) -> int:
    base, key = load_config()
    result = post(base, key, "/product/get_memory", {
        "mem_cube_id": args.user_id,
        "include_preference": True,
        "include_tool_memory": True,
        "include_skill_memory": True,
    })
    data = result.get("data") or {}
    if args.json:
        counts = {
            name: len(bucket) if isinstance(bucket, list) else 0
            for name, bucket in data.items()
            if isinstance(bucket, list)
        }
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return 0
    for field in VIEW_FIELDS:
        items = data.get(field) or []
        if items:
            print(f"{field}: {len(items)}")
    for legacy in ("text_mem", "pref_mem", "tool_mem", "skill_mem"):
        for bucket in data.get(legacy) or []:
            n = bucket.get("total_nodes")
            if n:
                print(f"{legacy}: {n}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    base, key = load_config()
    try:
        result = get(base, key, "/health")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"health check failed: {e}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user-id", default=os.getenv("MEMOS_USER_ID", "llwwds"))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="view-routed memory search")
    p.add_argument("query")
    p.add_argument("--view", help="comma-separated views: detail_factual,preference,event,tool_memory,skill")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--relativity", type=float, default=None, help="relevance threshold (0 disables)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("pitfalls", help="recall pitfall records relevant to a task (read before acting)")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pitfalls)

    p = sub.add_parser("add", help="store a memory (default: full extraction pipeline)")
    p.add_argument("text")
    p.add_argument("--fast", action="store_true", help="verbatim storage without LLM extraction (sync fast mode)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("delete", help="soft-delete a memory by record id (recoverable)")
    p.add_argument("record_id", help="memory id from search results")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("recover", help="recover a soft-deleted memory")
    p.add_argument("delete_record_id", help="record id returned by/when deleting")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_recover)

    p = sub.add_parser("stats", help="memory counts per view")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("health", help="service health check")
    p.set_defaults(func=cmd_health)

    args = parser.parse_args()
    try:
        return args.func(args)
    except error.HTTPError as e:
        print(f"API error: HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
