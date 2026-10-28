"""Regression tests: soft-deleted memories must not be returned by search.

Neo4j is the authority for the delete lifecycle; the Qdrant payload can be stale
(soft delete and recovery never rewrite it). These tests exercise the real
``Neo4jCommunityGraphDB.search_by_embedding`` / ``get_all_memory_items`` methods
with a fake driver that evaluates the generated Cypher predicates against
in-memory rows and a fake vector DB that applies payload filters like Qdrant.
No Neo4j, Qdrant, model or network access happens here.
"""

import pathlib
import sys

import pytest

from memos.configs.graph_db import Neo4jGraphDBConfig
from memos.graph_dbs.neo4j_community import Neo4jCommunityGraphDB


try:
    from tests.graph_dbs.soft_delete_fakes import (
        FakeDriver,
        FakeNodeStore,
        FakeVecDB,
        vec_hit,
    )
except ImportError:  # pragma: no cover - imports work without the rootdir on sys.path
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from soft_delete_fakes import FakeDriver, FakeNodeStore, FakeVecDB, vec_hit


def node(node_id, status="activated", user_name="cube-a", memory_type="LongTermMemory", **extra):
    """Build an authoritative Neo4j row (synthetic data only)."""
    return {
        "id": node_id,
        "memory": f"synthetic {node_id}",
        "status": status,
        "user_name": user_name,
        "memory_type": memory_type,
        **extra,
    }


@pytest.fixture
def config():
    return Neo4jGraphDBConfig(
        uri="bolt://localhost:7687",
        user="neo4j",
        password="test",
        db_name="test_db",
        auto_create=False,
        use_multi_db=False,
        user_name="cube-a",
        embedding_dimension=3,
    )


def make_db(config, nodes, hits, fail_with=None, vec_error=None, order="reverse"):
    """Real community adapter instance wired to fake backends only."""
    db = Neo4jCommunityGraphDB.__new__(Neo4jCommunityGraphDB)
    db.config = config
    db.db_name = config.db_name
    db.store = FakeNodeStore(nodes)
    db.driver = FakeDriver(db.store, fail_with=fail_with, order=order)
    db.vec_db = FakeVecDB(hits, error=vec_error)
    return db


def ids(results):
    return [item["id"] for item in results]


class TestSearchByEmbeddingSoftDeleteGate:
    def test_deleted_candidate_excluded_without_filters(self, config):
        """Plain vector search (no filter/return_fields) used to return deleted IDs as-is."""
        db = make_db(
            config,
            [node("deleted-a", "deleted"), node("live-b")],
            [vec_hit("deleted-a", 0.9), vec_hit("live-b", 0.8)],
        )

        results = db.search_by_embedding(vector=[0.1], top_k=5, user_name="cube-a")

        assert ids(results) == ["live-b"]
        assert results == [{"id": "live-b", "score": 0.8}]

    def test_deleted_candidate_excluded_with_return_fields(self, config):
        """return_fields must not bypass the authoritative state check."""
        db = make_db(
            config,
            [node("deleted-a", "deleted"), node("live-b")],
            [vec_hit("deleted-a", 0.9), vec_hit("live-b", 0.8)],
        )

        results = db.search_by_embedding(
            vector=[0.1],
            top_k=5,
            user_name="cube-a",
            return_fields=["memory", "key", "created_at", "updated_at", "source", "internal_info"],
        )

        assert ids(results) == ["live-b"]
        assert results[0]["memory"] == "synthetic live-b"
        assert "status" not in results[0]

    def test_deleted_candidate_excluded_for_or_filter(self, config):
        """A user OR filter is ANDed with the delete gate, so it cannot re-admit deleted rows."""
        db = make_db(
            config,
            [node("deleted-a", "deleted"), node("live-b")],
            [vec_hit("deleted-a", 0.9), vec_hit("live-b", 0.8)],
        )

        results = db.search_by_embedding(
            vector=[0.1],
            top_k=5,
            user_name="cube-a",
            filter={"or": [{"id": "deleted-a"}, {"id": "live-b"}]},
        )

        assert ids(results) == ["live-b"]

    def test_deleted_candidate_excluded_with_knowledgebase_ids(self, config):
        db = make_db(
            config,
            [node("deleted-a", "deleted", user_name="cube-b"), node("live-b", user_name="cube-b")],
            [
                vec_hit("deleted-a", 0.9, user_name="cube-b"),
                vec_hit("live-b", 0.8, user_name="cube-b"),
            ],
        )

        results = db.search_by_embedding(
            vector=[0.1], top_k=5, user_name="cube-b", knowledgebase_ids=["cube-b"]
        )

        assert ids(results) == ["live-b"]

    def test_cube_name_selects_user_scope(self, config):
        """cube_name keeps driving the user filter after the unified authoritative path."""
        db = make_db(
            config,
            [node("cube-x-row", user_name="cube-x"), node("cube-a-row", user_name="cube-a")],
            [
                vec_hit("cube-x-row", 0.9, user_name="cube-x"),
                vec_hit("cube-a-row", 0.8, user_name="cube-a"),
            ],
        )

        results = db.search_by_embedding(
            vector=[0.1], top_k=5, user_name="cube-a", cube_name="cube-x"
        )

        assert ids(results) == ["cube-x-row"]

    def test_stale_vector_payload_both_directions(self, config):
        """Qdrant payload must not decide visibility in either direction."""
        db = make_db(
            config,
            [node("stale-deleted", "deleted"), node("restored", "activated")],
            [
                vec_hit("stale-deleted", 0.9, status="activated"),
                vec_hit("restored", 0.8, status="deleted"),
            ],
        )

        results = db.search_by_embedding(
            vector=[0.1], top_k=5, status="activated", user_name="cube-a"
        )

        assert ids(results) == ["restored"]
        vec_filter = db.vec_db.calls[-1]["filter"]
        assert "status" not in vec_filter
        assert vec_filter["vector_sync"] == "success"
        assert vec_filter["user_name"] == "cube-a"

    def test_explicit_status_and_search_filter_status_stay_independent(self, config):
        """Conflicting lifecycle conditions must match nothing instead of overwriting."""
        db = make_db(config, [node("live-b")], [vec_hit("live-b", 0.8)])

        results = db.search_by_embedding(
            vector=[0.1],
            top_k=5,
            status="activated",
            user_name="cube-a",
            search_filter={"status": "deleted"},
        )

        assert results == []
        query = db.driver.queries[-1]["query"]
        params = db.driver.queries[-1]["params"]
        assert params["status"] == "activated"
        assert params["search_status"] == "deleted"
        assert "n.status = $status" in query
        assert "n.status = $search_status" in query

    def test_explicit_deleted_status_returns_nothing(self, config):
        """The search API never returns deleted rows, even when asked for them."""
        db = make_db(config, [node("deleted-a", "deleted")], [vec_hit("deleted-a", 0.9)])

        assert db.search_by_embedding(
            vector=[0.1], top_k=5, status="deleted", user_name="cube-a"
        ) == []

    def test_matching_search_filter_status_is_applied(self, config):
        """A matching search_filter-level status still filters by that status."""
        db = make_db(
            config,
            [node("archived-a", "archived"), node("live-b")],
            [vec_hit("archived-a", 0.9), vec_hit("live-b", 0.8)],
        )

        results = db.search_by_embedding(
            vector=[0.1], top_k=5, user_name="cube-a", search_filter={"status": "archived"}
        )

        assert ids(results) == ["archived-a"]

    def test_search_filter_status_must_be_scalar(self, config):
        db = make_db(config, [node("live-b")], [vec_hit("live-b", 0.8)])

        with pytest.raises(ValueError, match="search_filter\\['status'\\]"):
            db.search_by_embedding(
                vector=[0.1],
                top_k=5,
                user_name="cube-a",
                search_filter={"status": {"eq": "deleted"}},
            )

    def test_search_filter_dict_is_not_mutated(self, config):
        db = make_db(config, [node("live-b")], [vec_hit("live-b", 0.8)])
        search_filter = {"status": "activated", "session_id": "session-1"}

        db.search_by_embedding(
            vector=[0.1], top_k=5, user_name="cube-a", search_filter=search_filter
        )

        assert search_filter == {"status": "activated", "session_id": "session-1"}
        assert db.vec_db.calls[-1]["filter"]["session_id"] == "session-1"

    def test_survivors_keep_vector_score_and_order(self, config):
        """Graph order must not leak: results follow vector rank, scores unchanged."""
        db = make_db(
            config,
            [node("first"), node("second"), node("third")],
            [vec_hit("first", 0.9), vec_hit("second", 0.7), vec_hit("third", 0.5)],
        )

        results = db.search_by_embedding(
            vector=[0.1],
            top_k=5,
            user_name="cube-a",
            filter={"or": [{"id": "first"}, {"id": "second"}, {"id": "third"}]},
        )

        assert [(item["id"], item["score"]) for item in results] == [
            ("first", 0.9),
            ("second", 0.7),
            ("third", 0.5),
        ]

    def test_top_k_shortfall_is_accepted_not_backfilled(self, config):
        """Deleted candidates are dropped, not replaced by other candidates."""
        db = make_db(
            config,
            [node("deleted-a", "deleted"), node("live-b")],
            [vec_hit("deleted-a", 0.9), vec_hit("live-b", 0.8)],
        )

        results = db.search_by_embedding(vector=[0.1], top_k=5, user_name="cube-a")

        assert ids(results) == ["live-b"]

    def test_threshold_applies_before_the_gate(self, config):
        db = make_db(
            config,
            [node("deleted-a", "deleted"), node("live-b"), node("live-c")],
            [vec_hit("deleted-a", 0.95), vec_hit("live-b", 0.8), vec_hit("live-c", 0.3)],
        )

        results = db.search_by_embedding(
            vector=[0.1], top_k=5, user_name="cube-a", threshold=0.5
        )

        assert [(item["id"], item["score"]) for item in results] == [("live-b", 0.8)]

    def test_missing_graph_node_is_dropped(self, config):
        """Stale vector IDs without a graph node are not returned anymore."""
        db = make_db(
            config,
            [node("live-b")],
            [vec_hit("live-b", 0.8), vec_hit("stale-vector-only", 0.7)],
        )

        assert ids(db.search_by_embedding(vector=[0.1], top_k=5, user_name="cube-a")) == ["live-b"]

    def test_authority_error_propagates(self, config):
        """Fail closed: never fall back to unverified vector results."""
        db = make_db(
            config, [node("live-b")], [vec_hit("live-b", 0.8)], fail_with=RuntimeError("neo4j down")
        )

        with pytest.raises(RuntimeError, match="neo4j down"):
            db.search_by_embedding(vector=[0.1], top_k=5, user_name="cube-a")

    def test_missing_legacy_status_is_visible_without_status_filter(self, config):
        """Legacy rows without a status stay visible when no status is requested."""
        legacy = {"id": "legacy", "memory": "legacy row", "user_name": "cube-a"}
        db = make_db(config, [legacy], [vec_hit("legacy", 0.8)])

        assert ids(db.search_by_embedding(vector=[0.1], top_k=5, user_name="cube-a")) == ["legacy"]

    def test_missing_legacy_status_is_excluded_for_activated_search(self, config):
        """An activated search stays strict: missing/null status is not activated."""
        legacy = {"id": "legacy", "memory": "legacy row", "user_name": "cube-a"}
        db = make_db(config, [legacy], [vec_hit("legacy", 0.8)])

        results = db.search_by_embedding(
            vector=[0.1], top_k=5, status="activated", user_name="cube-a"
        )

        assert results == []

    def test_deleted_row_without_delete_markers_is_excluded(self, config):
        """Internal soft delete only writes status; markers are not required."""
        db = make_db(
            config,
            [node("deleted-a", "deleted", delete_time="", delete_record_id="")],
            [vec_hit("deleted-a", 0.9)],
        )

        assert db.search_by_embedding(vector=[0.1], top_k=5, user_name="cube-a") == []


class TestGetAllMemoryItemsStatus:
    def test_status_activated_excludes_deleted(self, config):
        db = make_db(
            config,
            [node("deleted-a", "deleted", memory_type="WorkingMemory"),
             node("live-b", memory_type="WorkingMemory")],
            [],
        )

        results = db.get_all_memory_items(scope="WorkingMemory", status="activated")

        assert ids(results) == ["live-b"]

    def test_default_none_keeps_raw_enumeration(self, config):
        """Admin/history callers keep the original behaviour when status is omitted."""
        db = make_db(
            config,
            [node("deleted-a", "deleted", memory_type="WorkingMemory"),
             node("live-b", memory_type="WorkingMemory")],
            [],
        )

        results = db.get_all_memory_items(scope="WorkingMemory")

        assert sorted(ids(results)) == ["deleted-a", "live-b"]

    def test_status_filter_is_anded_with_or_filter(self, config):
        db = make_db(
            config,
            [
                node("deleted-a", "deleted", key="goal-key"),
                node("live-b", key="goal-key"),
                node("live-c", key="other-key"),
            ],
            [],
        )

        results = db.get_all_memory_items(
            scope="LongTermMemory",
            filter={"or": [{"key": "goal-key"}, {"key": "other-key"}]},
            status="activated",
        )

        assert sorted(ids(results)) == ["live-b", "live-c"]
