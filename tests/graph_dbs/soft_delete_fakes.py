"""Dependency-free fakes for the soft-delete search visibility regression tests.

The fake session evaluates the Cypher predicates that
``Neo4jCommunityGraphDB`` actually sends (a small MATCH/WHERE subset) against
in-memory rows instead of matching query strings. Tests therefore observe real
filtering, ordering and fail-closed behaviour without a Neo4j server.

Only the stdlib is used here, so the module can also be loaded by a stdlib-only
harness in environments without the full MemOS dependency set.
"""

import re


class VecHit:
    """Minimal stand-in for a vector-DB search hit (id, score, payload)."""

    def __init__(self, id: str, score: float, payload: dict | None = None):
        self.id = id
        self.score = score
        self.payload = dict(payload or {})

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"VecHit(id={self.id!r}, score={self.score!r})"


def vec_hit(
    id: str,
    score: float,
    status: str = "activated",
    user_name: str = "cube-a",
    memory_type: str = "LongTermMemory",
    **extra,
) -> VecHit:
    """Build a hit with a Qdrant-style payload used by the payload filter."""
    payload = {
        "status": status,
        "user_name": user_name,
        "memory_type": memory_type,
        "vector_sync": "success",
        **extra,
    }
    return VecHit(id, score, payload)


class FakeVecDB:
    """Vector DB double: records the payload filter and returns matching hits.

    Payload filtering mirrors Qdrant equality filters for the keys present in the
    requested filter, which is what lets the tests detect whether the lifecycle
    ``status`` is (wrongly) still pushed down into the vector store.
    """

    def __init__(self, hits: list[VecHit] | None = None, error: Exception | None = None):
        self.hits = list(hits or [])
        self.error = error
        self.calls: list[dict] = []

    def search(self, query_vector=None, top_k=5, filter=None, **kwargs):  # noqa: A002
        self.calls.append(
            {
                "query_vector": query_vector,
                "top_k": top_k,
                "filter": dict(filter or {}),
            }
        )
        if self.error is not None:
            raise self.error
        matches = [hit for hit in self.hits if _payload_matches(hit.payload, filter or {})]
        return matches[:top_k]

    def get_by_ids(self, ids):
        return []


def _payload_matches(payload: dict, filter: dict) -> bool:  # noqa: A002
    for key, expected in filter.items():
        if payload.get(key) != expected:
            return False
    return True


class FakeNodeStore:
    """In-memory authoritative graph store (Neo4j stand-in)."""

    def __init__(self, nodes: list[dict] | None = None):
        self.nodes = [dict(node) for node in nodes or []]

    def set_status(self, node_id: str, status: str) -> None:
        for node in self.nodes:
            if node["id"] == node_id:
                node["status"] = status
                return
        raise KeyError(node_id)


class FakeDriver:
    """Driver double handing out sessions bound to a FakeNodeStore."""

    def __init__(
        self,
        store: FakeNodeStore,
        fail_with: Exception | None = None,
        order: str = "reverse",
    ):
        self.store = store
        self.fail_with = fail_with
        self.order = order
        self.queries: list[dict] = []

    def session(self, database=None):
        return _FakeSession(self)


class _FakeSession:
    def __init__(self, driver: FakeDriver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def run(self, query, params=None, **kwargs):
        self.driver.queries.append({"query": query, "params": dict(params or {})})
        if self.driver.fail_with is not None:
            raise self.driver.fail_with

        text = " ".join(str(query).split())
        where_expr, return_clause = _split_query(text)
        rows = self.driver.store.nodes
        if where_expr:
            rows = [row for row in rows if eval_where(where_expr, row, params or {})]
        matched = [dict(row) for row in rows]
        if self.driver.order == "reverse":
            matched.reverse()
        return _project(matched, return_clause)


def _split_query(text: str) -> tuple[str | None, str]:
    """Split a Cypher query into its WHERE expression and RETURN projection."""
    upper = text.upper()
    return_index = upper.index("RETURN")
    head = text[:return_index]
    return_clause = text[return_index + len("RETURN") :].strip()
    where_index = head.upper().find("WHERE")
    if where_index == -1:
        return None, return_clause
    return head[where_index + len("WHERE") :].strip(), return_clause


def _project(rows: list[dict], return_clause: str) -> list[dict]:
    """Apply a simple RETURN projection (``n`` or ``n.prop AS alias`` list)."""
    tokens = [token.strip() for token in return_clause.split(",")]
    if tokens == ["n"]:
        return [{"n": row} for row in rows]

    projected = []
    for row in rows:
        record = {}
        for token in tokens:
            match = re.fullmatch(r"n\.(\w+) AS (\w+)", token)
            if not match:
                raise AssertionError(f"Fake session cannot project: {token!r}")
            field, alias = match.groups()
            record[alias] = row.get(field)
        projected.append(record)
    return projected


# ── minimal Cypher predicate evaluator ──────────────────────────────────────


def eval_where(expr: str, row: dict, params: dict) -> bool:
    """Evaluate a Cypher WHERE expression against one node row."""
    expr = expr.strip()
    while expr.startswith("(") and _closing_paren(expr) == len(expr) - 1:
        expr = expr[1:-1].strip()

    or_parts = _split_top_level(expr, " OR ")
    if len(or_parts) > 1:
        return any(eval_where(part, row, params) for part in or_parts)

    and_parts = _split_top_level(expr, " AND ")
    if len(and_parts) > 1:
        return all(eval_where(part, row, params) for part in and_parts)

    return _eval_atom(expr, row, params)


def _closing_paren(expr: str) -> int:
    depth = 0
    for index, char in enumerate(expr):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level(expr: str, separator: str) -> list[str]:
    parts = []
    depth = 0
    quote = None
    start = 0
    index = 0
    while index < len(expr):
        char = expr[index]
        if quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and expr.startswith(separator, index):
            parts.append(expr[start:index])
            index += len(separator)
            start = index
            continue
        index += 1
    parts.append(expr[start:])
    return parts


def _eval_atom(expr: str, row: dict, params: dict) -> bool:
    match = re.fullmatch(r"ANY\(x IN \$(\w+) WHERE x IN n\.(\w+)\)", expr)
    if match:
        param, field = match.groups()
        return bool(set(params.get(param) or []) & set(row.get(field) or []))

    match = re.fullmatch(r"coalesce\(n\.(\w+),\s*[\"'](\w+)[\"']\)\s*<>\s*[\"'](\w+)[\"']", expr)
    if match:
        field, default, value = match.groups()
        current = row.get(field)
        return (default if current is None else current) != value

    match = re.fullmatch(r"n\.(\w+) IN \$(\w+)", expr)
    if match:
        field, param = match.groups()
        return row.get(field) in (params.get(param) or [])

    match = re.fullmatch(r"\$(\w+) IN n\.(\w+)", expr)
    if match:
        param, field = match.groups()
        return params.get(param) in (row.get(field) or [])

    match = re.fullmatch(r"n\.(\w+) CONTAINS \$(\w+)", expr)
    if match:
        field, param = match.groups()
        return params[param] in (row.get(field) or "")

    match = re.fullmatch(r"datetime\(n\.(\w+)\)\s*(=|<>|>=|<=|>|<)\s*datetime\(\$(\w+)\)", expr)
    if match:
        field, operator, param = match.groups()
        return _compare(row.get(field), operator, params[param])

    match = re.fullmatch(r"n\.(\w+)\s*(=|<>|>=|<=|>|<)\s*\$(\w+)", expr)
    if match:
        field, operator, param = match.groups()
        return _compare(row.get(field), operator, params[param])

    match = re.fullmatch(r"n\.(\w+)\s*(=|<>)\s*'([^']*)'", expr)
    if match:
        field, operator, literal = match.groups()
        return _compare(row.get(field), operator, literal)

    raise AssertionError(f"Fake session cannot evaluate predicate: {expr!r}")


def _compare(left, operator: str, right) -> bool:
    # Cypher comparisons against a missing/null property never match.
    if left is None:
        return False
    if operator == "=":
        return left == right
    if operator == "<>":
        return left != right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    raise AssertionError(f"Unsupported operator: {operator!r}")
