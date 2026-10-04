"""RefreshToolIndexUseCase — see plans/tool_rag.md section 3.3 (content-
hash based incremental reindexing)."""

from __future__ import annotations

from app.application.refresh_tool_index import RawTool, RefreshToolIndexUseCase
from tests.fakes import FakeEmbeddingPort, FakeToolIndexRepo


def _tool(
    description: str = "recommend model", example_queries: list[str] | None = None
) -> RawTool:
    return RawTool(
        server_module="ai.mcp_servers.quant",
        tool_name="recommend_model",
        description=description,
        example_queries=example_queries or ["which model fits this stock"],
        category="quant",
        schema_token_estimate=15,
    )


def test_first_refresh_embeds_every_document():
    repo = FakeToolIndexRepo()
    use_case = RefreshToolIndexUseCase(repo, FakeEmbeddingPort())

    result = use_case.refresh([_tool()])

    assert result == {
        "tools": 1,
        "documents": 2,
        "embedded": 2,
        "skipped": 0,
        "deleted": 0,
    }  # description + 1 example


def test_second_refresh_with_unchanged_content_embeds_nothing():
    repo = FakeToolIndexRepo()
    use_case = RefreshToolIndexUseCase(repo, FakeEmbeddingPort())
    use_case.refresh([_tool()])

    result = use_case.refresh([_tool()])

    assert result["embedded"] == 0
    assert result["skipped"] == 2


def test_changing_the_description_only_re_embeds_that_one_row():
    repo = FakeToolIndexRepo()
    use_case = RefreshToolIndexUseCase(repo, FakeEmbeddingPort())
    use_case.refresh([_tool(description="old description")])

    result = use_case.refresh([_tool(description="new description")])

    assert result["embedded"] == 1  # only the description row changed
    assert result["skipped"] == 1  # the example query row didn't


def test_a_removed_example_query_is_deleted_from_the_index():
    repo = FakeToolIndexRepo()
    use_case = RefreshToolIndexUseCase(repo, FakeEmbeddingPort())
    use_case.refresh([_tool(example_queries=["query one", "query two"])])

    result = use_case.refresh([_tool(example_queries=["query one"])])

    assert result["deleted"] == 1
    assert result["documents"] == 2  # description + the one remaining example
