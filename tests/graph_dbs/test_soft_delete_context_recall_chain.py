"""Real call chain: the product context recall must not return soft-deleted rows.

``SearchHandler._recall_context_buckets`` requests ``return_fields`` without
``status``, so a projection-only fix cannot hide a deleted row: visibility must be
decided by the authoritative Neo4j state gate inside
``Neo4jCommunityGraphDB.search_by_embedding``. The handler, the adapter, the scope
and the field list are all real; only the Neo4j driver and the vector DB are fakes
(no Neo4j, Qdrant, model or network access).
"""

import pathlib
import sys
import uuid

from memos.api.handlers.base_handler import HandlerDependencies
from memos.api.handlers.search_handler import SearchHandler
from memos.api.product_models import APISearchRequest
from memos.configs.graph_db import Neo4jGraphDBConfig
from memos.dream.contextualization import CONTEXT_MEMORY_TYPE
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


class FakeEmbedder:
    """Deterministic embedder: no model, no network."""

    def __init__(self, vector=None):
        self.vector = list(vector or [0.1, 0.2, 0.3])

    def embed(self, texts):
        return [list(self.vector) for _ in texts]


def context_row(status="activated", node_id=None, key="Context summary"):
    return {
        "id": node_id or str(uuid.uuid4()),
        "memory": f"synthetic {status} context",
        "status": status,
        "user_name": "cube-a",
        "memory_type": CONTEXT_MEMORY_TYPE,
        "key": key,
        "source": "system",
        "internal_info": {"dream": {"memory_ids": ["m1"]}},
    }


def community_context_db(rows, hits, fail_with=None):
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
    db.vec_db = FakeVecDB(hits)
    return db


def make_handler(graph_db):
    return SearchHandler(
        HandlerDependencies(
            naive_mem_cube=object(),
            mem_scheduler=object(),
            searcher=object(),
            deepsearch_agent=object(),
            graph_db=graph_db,
            embedder=FakeEmbedder(),
        )
    )


def search_request():
    return APISearchRequest(
        query="what is the user designing?",
        user_id="cube-a",
        readable_cube_ids=["cube-a"],
        top_k=5,
        relativity=0,
        dedup="no",
    )


def context_hit(node_id, score, status):
    return vec_hit(node_id, score, status=status, memory_type=CONTEXT_MEMORY_TYPE)


class TestContextRecallChain:
    def test_context_bucket_excludes_deleted_rows(self):
        """return_fields has no `status`, so only the authority gate can filter."""
        live = context_row()
        deleted = context_row(status="deleted")
        db = community_context_db(
            [live, deleted],
            [context_hit(deleted["id"], 0.95, "activated"), context_hit(live["id"], 0.9, "activated")],
        )
        handler = make_handler(db)

        buckets = handler._recall_context_buckets(search_req=search_request(), top_k=2)

        assert len(buckets) == 1
        memories = buckets[0]["memories"]
        assert [memory["id"] for memory in memories] == [live["id"]]
        assert memories[0]["metadata"]["memory_type"] == CONTEXT_MEMORY_TYPE
        assert memories[0]["metadata"]["key"] == "Context summary"
        assert memories[0]["metadata"]["internal_info"] == {"dream": {"memory_ids": ["m1"]}}

    def test_context_bucket_skipped_when_authority_check_fails(self):
        """Fail closed: the cube is skipped instead of falling back to vector bodies."""
        live = context_row()
        db = community_context_db(
            [live],
            [context_hit(live["id"], 0.9, "activated")],
            fail_with=RuntimeError("neo4j down"),
        )
        handler = make_handler(db)

        assert handler._recall_context_buckets(search_req=search_request(), top_k=2) == []
