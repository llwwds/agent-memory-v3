"""Regression tests: fine-mode graph recall must not return soft-deleted memories.

``GraphMemoryRetriever._graph_recall`` uses two code paths. The fast path already
restricted ``get_by_metadata`` to ``status="activated"``; the fine path (key/tag
branches) did not, so deleted memories could enter the reranker/LLM context.
These tests use a fake graph store that honours the status argument exactly like
the Neo4j adapter, so the assertions fail if the status filter is dropped again.
"""

import uuid

import pathlib
import sys

from unittest.mock import MagicMock

import pytest

from memos.configs.graph_db import Neo4jGraphDBConfig
from memos.graph_dbs.neo4j_community import Neo4jCommunityGraphDB
from memos.memories.textual.tree_text_memory.retrieve.recall import GraphMemoryRetriever
from memos.memories.textual.tree_text_memory.retrieve.retrieval_mid_structs import ParsedTaskGoal
from memos.memories.textual.tree_text_memory.retrieve.searcher import Searcher


try:
    from tests.graph_dbs.soft_delete_fakes import (
        FakeDriver,
        FakeNodeStore,
        FakeVecDB,
        vec_hit,
    )
except ImportError:  # pragma: no cover - imports work without the rootdir on sys.path
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "graph_dbs"))
    from soft_delete_fakes import FakeDriver, FakeNodeStore, FakeVecDB, vec_hit


class StatusAwareGraphStore:
    """In-memory graph store whose get_by_metadata honours ``status``."""

    def __init__(self, rows):
        self.rows = rows
        self.metadata_calls = []
        self.node_calls = []

    def get_by_metadata(self, filters, user_name=None, status=None, **kwargs):
        self.metadata_calls.append(
            {"filters": filters, "status": status, "user_name": user_name}
        )
        matched = []
        for row in self.rows:
            if status is not None and row["metadata"].get("status") != status:
                continue
            if all(_matches(row["metadata"], condition) for condition in filters):
                matched.append(row["id"])
        return matched

    def get_nodes(self, ids, include_embedding=False, user_name=None):
        self.node_calls.append(list(ids))
        wanted = set(ids)
        return [row for row in self.rows if row["id"] in wanted]


def _matches(metadata, condition):
    value = metadata.get(condition["field"])
    operator = condition.get("op", "=")
    if operator == "=":
        return value == condition["value"]
    if operator == "in":
        return value in condition["value"]
    if operator == "contains":
        return bool(set(value or []) & set(condition["value"]))
    raise AssertionError(f"unsupported operator: {operator}")


# The tag branch requires at least two overlapping tags, so synthetic rows carry two.
GOAL_TAGS = ["goal-tag", "goal-tag-2"]


def make_row(status="activated", key="goal-key", tags=None):
    return {
        "id": str(uuid.uuid4()),
        "memory": f"synthetic {status} memory",
        "metadata": {
            "memory_type": "LongTermMemory",
            "status": status,
            "key": key,
            "tags": list(GOAL_TAGS) if tags is None else tags,
            "user_name": "cube-a",
        },
    }


def make_retriever(rows):
    return GraphMemoryRetriever(StatusAwareGraphStore(rows), MagicMock())


def result_ids(items):
    return [item.id for item in items]


class TestFineGraphRecallSoftDelete:
    def test_key_and_tag_branches_request_activated(self):
        live = make_row()
        deleted = make_row(status="deleted")
        retriever = make_retriever([live, deleted])
        goal = ParsedTaskGoal(keys=["goal-key"], tags=["goal-tag"])

        results = retriever._graph_recall(goal, "LongTermMemory", user_name="cube-a")

        assert [call["status"] for call in retriever.graph_store.metadata_calls] == [
            "activated",
            "activated",
        ]
        assert result_ids(results) == [live["id"]]
        assert retriever.graph_store.node_calls == [[live["id"]]]

    def test_key_only_branch_requests_activated(self):
        live = make_row()
        deleted = make_row(status="deleted")
        retriever = make_retriever([live, deleted])
        goal = ParsedTaskGoal(keys=["goal-key"])

        results = retriever._graph_recall(goal, "LongTermMemory", user_name="cube-a")

        assert [call["status"] for call in retriever.graph_store.metadata_calls] == ["activated"]
        assert result_ids(results) == [live["id"]]

    def test_tag_only_branch_requests_activated(self):
        live = make_row()
        deleted = make_row(status="deleted")
        retriever = make_retriever([live, deleted])
        goal = ParsedTaskGoal(tags=list(GOAL_TAGS))

        results = retriever._graph_recall(goal, "LongTermMemory", user_name="cube-a")

        assert [call["status"] for call in retriever.graph_store.metadata_calls] == ["activated"]
        assert result_ids(results) == [live["id"]]

    def test_deleted_only_match_returns_nothing(self):
        deleted = make_row(status="deleted")
        retriever = make_retriever([deleted])
        goal = ParsedTaskGoal(keys=["goal-key"], tags=["goal-tag"])

        results = retriever._graph_recall(goal, "LongTermMemory", user_name="cube-a")

        assert results == []
        assert retriever.graph_store.node_calls == []

    def test_fast_branch_still_requests_activated(self):
        """Guard the pre-existing fast-path behaviour."""
        live = make_row()
        deleted = make_row(status="deleted")
        retriever = make_retriever([live, deleted])
        goal = ParsedTaskGoal(keys=["goal-key"], tags=["goal-tag"])

        results = retriever._graph_recall(
            goal, "LongTermMemory", user_name="cube-a", use_fast_graph=True
        )

        assert [call["status"] for call in retriever.graph_store.metadata_calls] == [
            "activated",
            "activated",
        ]
        assert result_ids(results) == [live["id"]]

    def test_empty_goal_skips_graph_lookup(self):
        retriever = make_retriever([make_row()])

        assert retriever._graph_recall(ParsedTaskGoal(), "LongTermMemory") == []
        assert retriever.graph_store.metadata_calls == []


# ── real call chains: retriever/searcher → Neo4jCommunityGraphDB adapter ─────


class FakeEmbedder:
    """Deterministic embedder: no model, no network."""

    def __init__(self, vector=None):
        self.vector = list(vector or [0.1, 0.2, 0.3])
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [list(self.vector) for _ in texts]


def graph_row(status="activated", memory_type="LongTermMemory", node_id=None, **extra):
    """Authoritative Neo4j row; ids must be UUIDs because results become TextualMemoryItem."""
    return {
        "id": node_id or str(uuid.uuid4()),
        "memory": f"synthetic {status} {memory_type}",
        "status": status,
        "user_name": "cube-a",
        "memory_type": memory_type,
        "key": extra.pop("key", "goal-key"),
        "tags": extra.pop("tags", list(GOAL_TAGS)),
        **extra,
    }


def community_db(rows, hits=None, fail_with=None):
    """Real community adapter with fake driver/vector backends."""
    db = Neo4jCommunityGraphDB.__new__(Neo4jCommunityGraphDB)
    db.config = Neo4jGraphDBConfig(
        uri="bolt://localhost:7687",
        user="neo4j",
        password="test",
        db_name="test_db",
        auto_create=False,
        use_multi_db=False,
        user_name="cube-a",
        embedding_dimension=3,
    )
    db.db_name = db.config.db_name
    db.driver = FakeDriver(FakeNodeStore(rows), fail_with=fail_with)
    db.vec_db = FakeVecDB(hits or [])
    return db


class TestRecallCallChains:
    def test_working_memory_chain_excludes_deleted(self):
        live = graph_row(memory_type="WorkingMemory")
        deleted = graph_row(status="deleted", memory_type="WorkingMemory")
        retriever = GraphMemoryRetriever(community_db([live, deleted]), FakeEmbedder())

        items = retriever.retrieve(
            query="q",
            parsed_goal=ParsedTaskGoal(),
            top_k=5,
            memory_scope="WorkingMemory",
            query_embedding=None,
            user_name="cube-a",
        )

        assert [item.id for item in items] == [live["id"]]

    def test_long_term_chain_excludes_deleted_from_graph_and_vector(self):
        live = graph_row()
        deleted = graph_row(status="deleted")
        db = community_db(
            [live, deleted],
            hits=[vec_hit(deleted["id"], 0.9), vec_hit(live["id"], 0.8)],
        )
        db.get_by_metadata = MagicMock(wraps=db.get_by_metadata)
        retriever = GraphMemoryRetriever(db, FakeEmbedder())

        items = retriever.retrieve(
            query="q",
            parsed_goal=ParsedTaskGoal(keys=["goal-key"], tags=list(GOAL_TAGS)),
            top_k=5,
            memory_scope="LongTermMemory",
            query_embedding=[[0.1, 0.2, 0.3]],
            user_name="cube-a",
        )

        assert {item.id for item in items} == {live["id"]}
        assert [call.kwargs["status"] for call in db.get_by_metadata.call_args_list] == [
            "activated",
            "activated",
        ]

    def test_searcher_fine_full_recall_excludes_deleted(self):
        """fine mode + full_recall: candidates are gated before the early return."""
        live = graph_row()
        deleted = graph_row(status="deleted")
        searcher = Searcher(MagicMock(), community_db([live, deleted]), FakeEmbedder(), MagicMock())
        searcher.task_goal_parser = MagicMock()
        searcher.task_goal_parser.parse.return_value = ParsedTaskGoal(
            keys=["goal-key"], tags=list(GOAL_TAGS)
        )

        results = searcher.search(
            query="goal-key",
            top_k=5,
            info={"user_id": "cube-a", "session_id": "session-1"},
            mode="fine",
            memory_type="LongTermMemory",
            user_name="cube-a",
            full_recall=True,
            rerank=False,
        )

        assert [item.id for item, _score in results] == [live["id"]]

    def test_searcher_plugin_path_excludes_deleted(self):
        """plugin=True retrieval path never surfaces a deleted memory."""
        live = graph_row()
        deleted = graph_row(status="deleted")
        db = community_db(
            [live, deleted],
            hits=[vec_hit(deleted["id"], 0.9), vec_hit(live["id"], 0.8)],
        )
        searcher = Searcher(MagicMock(), db, FakeEmbedder(), None)

        results = searcher.search(
            query="goal-key",
            top_k=2,
            info={"user_id": "cube-a", "session_id": "session-1"},
            mode="fast",
            user_name="cube-a",
            plugin=True,
            full_recall=True,
            rerank=False,
        )

        assert [item.id for item, _score in results] == [live["id"]]

    def test_searcher_fine_chain_fails_closed(self):
        """Authority failure propagates instead of falling back to vector results."""
        live = graph_row()
        db = community_db([live], hits=[vec_hit(live["id"], 0.8)], fail_with=RuntimeError("down"))
        searcher = Searcher(MagicMock(), db, FakeEmbedder(), MagicMock())
        searcher.task_goal_parser = MagicMock()
        searcher.task_goal_parser.parse.return_value = ParsedTaskGoal(keys=["goal-key"])

        with pytest.raises(RuntimeError, match="down"):
            searcher.search(
                query="goal-key",
                top_k=5,
                info={"user_id": "cube-a", "session_id": "session-1"},
                mode="fine",
                memory_type="LongTermMemory",
                user_name="cube-a",
                full_recall=True,
                rerank=False,
            )
