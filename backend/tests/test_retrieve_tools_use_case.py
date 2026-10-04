"""RetrieveToolsUseCase — see plans/tool_rag.md section 4. Uses
FakeToolIndexRepo/FakeEmbeddingPort (tests/fakes.py) instead of a real
Postgres+pgvector instance, following this project's usual fakes-over-
real-DB testing style."""

from __future__ import annotations

from app.application.refresh_tool_index import RawTool, RefreshToolIndexUseCase
from app.application.retrieve_tools import RetrieveToolsUseCase
from app.domain.tool_rag.policy import SelectionPolicy
from tests.fakes import FakeEmbeddingPort, FakeToolIndexRepo

PERMISSIVE_POLICY = SelectionPolicy(
    top_k=8, max_schema_tokens=10_000, min_score=0.0, max_per_server=10, skip_below_tool_count=0
)


def _index_two_tools(vectors: dict[str, list[float]]) -> FakeToolIndexRepo:
    """One 'quant' tool about model recommendations, one 'analytics' tool
    about concentration — indexed with hand-supplied embeddings so their
    similarity to a query is fully controlled by the test, not by a real
    model's opinion of the text."""
    repo = FakeToolIndexRepo()
    embedding_port = FakeEmbeddingPort(vectors)
    documents = [
        RawTool(
            server_module="ai.mcp_servers.quant",
            tool_name="recommend_model",
            description="recommend model",
            example_queries=["which model fits this stock"],
            category="quant",
            schema_token_estimate=15,
        ),
        RawTool(
            server_module="ai.mcp_servers.analytics",
            tool_name="get_concentration",
            description="get concentration",
            example_queries=["is my portfolio concentrated"],
            category="analytics",
            schema_token_estimate=10,
        ),
    ]
    RefreshToolIndexUseCase(repo, embedding_port).refresh(documents)
    return repo


def test_dense_search_ranks_the_semantically_closest_tool_first():
    # top_k=1 makes the assertion meaningful: with the permissive policy
    # both tools would fit the budget, so what's actually under test here
    # is ranking (quant scores higher on both stages), not budget/diversity.
    top_one_policy = SelectionPolicy(
        top_k=1, max_schema_tokens=10_000, min_score=0.0, max_per_server=10, skip_below_tool_count=0
    )
    vectors = {
        "recommend model": [1.0, 0.0],
        "which model fits this stock": [1.0, 0.0],
        "get concentration": [0.0, 1.0],
        "is my portfolio concentrated": [0.0, 1.0],
        "which model should I use for this ETF?": [0.9, 0.1],  # the query
    }
    repo = _index_two_tools(vectors)
    embedding_port = FakeEmbeddingPort(vectors)
    use_case = RetrieveToolsUseCase(repo, embedding_port, top_one_policy)
    ceiling = {
        ("ai.mcp_servers.quant", "recommend_model"),
        ("ai.mcp_servers.analytics", "get_concentration"),
    }

    decision = use_case.retrieve(
        "which model should I use for this ETF?", pinned=set(), all_tools_in_ceiling=ceiling
    )

    assert not decision.fallback_triggered
    assert decision.fused[0].key == ("ai.mcp_servers.quant", "recommend_model")
    assert decision.selected == {"ai.mcp_servers.quant": ["recommend_model"]}


def test_retrieval_never_selects_a_tool_outside_the_ceiling():
    vectors = {
        "recommend model": [1.0, 0.0],
        "which model fits this stock": [1.0, 0.0],
        "get concentration": [1.0, 0.0],  # deliberately made to score just as well
        "is my portfolio concentrated": [1.0, 0.0],
        "anything": [1.0, 0.0],
    }
    repo = _index_two_tools(vectors)
    embedding_port = FakeEmbeddingPort(vectors)
    use_case = RetrieveToolsUseCase(repo, embedding_port, PERMISSIVE_POLICY)
    # Ceiling excludes get_concentration entirely.
    ceiling = {("ai.mcp_servers.quant", "recommend_model")}

    decision = use_case.retrieve("anything", pinned=set(), all_tools_in_ceiling=ceiling)

    all_selected = {(m, n) for m, names in decision.selected.items() for n in names}
    assert all_selected <= ceiling
    assert ("ai.mcp_servers.analytics", "get_concentration") not in all_selected


def test_pinned_terminal_tool_survives_even_with_no_matching_documents_indexed():
    repo = FakeToolIndexRepo()  # empty index — nothing to match against
    embedding_port = FakeEmbeddingPort({})
    use_case = RetrieveToolsUseCase(repo, embedding_port, PERMISSIVE_POLICY)
    ceiling = {
        ("ai.mcp_servers.security", "save_security_mapping"),
        ("ai.mcp_servers.security", "flag_for_review"),
    }
    pinned = {
        ("ai.mcp_servers.security", "save_security_mapping"),
        ("ai.mcp_servers.security", "flag_for_review"),
    }

    decision = use_case.retrieve("anything at all", pinned=pinned, all_tools_in_ceiling=ceiling)

    # An empty index means an empty fused list, which triggers the
    # small-catalog/low-score fallback anyway — but the point of this test
    # is that pinning would have won even if it hadn't (see
    # test_tool_rag_policy.py for the pinning-only guarantee in isolation).
    all_selected = {(m, n) for m, names in decision.selected.items() for n in names}
    assert pinned <= all_selected


def test_small_ceiling_falls_back_to_exposing_everything():
    policy = SelectionPolicy(
        top_k=8,
        max_schema_tokens=10_000,
        min_score=0.0,
        max_per_server=10,
        skip_below_tool_count=12,
    )
    vectors = {
        "recommend model": [1.0, 0.0],
        "which model fits this stock": [1.0, 0.0],
        "anything": [1.0, 0.0],
    }
    repo = FakeToolIndexRepo()
    embedding_port = FakeEmbeddingPort(vectors)
    RefreshToolIndexUseCase(repo, embedding_port).refresh(
        [
            RawTool(
                server_module="ai.mcp_servers.quant",
                tool_name="recommend_model",
                description="recommend model",
                example_queries=[],
                category="quant",
                schema_token_estimate=15,
            )
        ]
    )
    use_case = RetrieveToolsUseCase(repo, embedding_port, policy)
    ceiling = {("ai.mcp_servers.quant", "recommend_model")}  # 1 tool <= skip_below_tool_count=12

    decision = use_case.retrieve("anything", pinned=set(), all_tools_in_ceiling=ceiling)

    assert decision.fallback_triggered
    assert decision.selected == {"ai.mcp_servers.quant": ["recommend_model"]}
