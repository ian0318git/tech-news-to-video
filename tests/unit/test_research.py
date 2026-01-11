"""Tests for research functionality."""

import json
import re

import pytest

from notebooklm import NotebookLMClient
from notebooklm.auth import AuthTokens
from notebooklm.rpc import RPCMethod


@pytest.fixture
def auth_tokens():
    return AuthTokens(
        cookies={"SID": "test"},
        csrf_token="test_csrf",
        session_id="test_session",
    )


class TestResearch:
    @pytest.mark.asyncio
    async def test_start_fast_research(self, auth_tokens, httpx_mock):
        response_json = json.dumps(["task_123", None])
        chunk = json.dumps(
            ["wrb.fr", RPCMethod.START_FAST_RESEARCH.value, response_json, None, None]
        )
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(
            url=re.compile(r".*batchexecute.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.research.start(
                notebook_id="nb_123", query="Quantum computing", mode="fast"
            )

        assert result["task_id"] == "task_123"
        assert result["mode"] == "fast"

    @pytest.mark.asyncio
    async def test_poll_research_completed(self, auth_tokens, httpx_mock):
        # Mock poll response with completed status (2)
        sources = [
            ["http://example.com", "Example Title", "Description", 1],
        ]
        task_info = [
            None,
            ["query", 1],  # query info
            1,  # mode
            [sources, "Summary text"],  # sources and summary
            2,  # status: completed
        ]

        response_json = json.dumps([[["task_123", task_info]]])
        chunk = json.dumps(["wrb.fr", RPCMethod.POLL_RESEARCH.value, response_json, None, None])
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.research.poll("nb_123")

        assert result["status"] == "completed"
        assert len(result["sources"]) == 1
        assert result["sources"][0]["url"] == "http://example.com"
        assert result["summary"] == "Summary text"

    @pytest.mark.asyncio
    async def test_import_research(self, auth_tokens, httpx_mock):
        response_json = json.dumps([[[["src_new"], "Imported Title"]]])
        chunk = json.dumps(["wrb.fr", RPCMethod.IMPORT_RESEARCH.value, response_json, None, None])
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            sources = [{"url": "http://example.com", "title": "Example"}]
            result = await client.research.import_sources(
                notebook_id="nb_123", task_id="task_123", sources=sources
            )

        assert len(result) == 1
        assert result[0]["id"] == "src_new"

    @pytest.mark.asyncio
    async def test_start_deep_research(self, auth_tokens, httpx_mock):
        """Test starting deep web research."""
        response_json = json.dumps(["task_456", "report_123"])
        chunk = json.dumps(
            ["wrb.fr", RPCMethod.START_DEEP_RESEARCH.value, response_json, None, None]
        )
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(
            url=re.compile(r".*batchexecute.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.research.start(
                notebook_id="nb_123", query="AI research", mode="deep"
            )

        assert result["task_id"] == "task_456"
        assert result["report_id"] == "report_123"
        assert result["mode"] == "deep"

    @pytest.mark.asyncio
    async def test_start_research_invalid_source(self, auth_tokens):
        """Test that invalid source raises ValueError."""
        async with NotebookLMClient(auth_tokens) as client:
            with pytest.raises(ValueError, match="Invalid source"):
                await client.research.start(
                    notebook_id="nb_123", query="test", source="invalid"
                )

    @pytest.mark.asyncio
    async def test_start_research_invalid_mode(self, auth_tokens):
        """Test that invalid mode raises ValueError."""
        async with NotebookLMClient(auth_tokens) as client:
            with pytest.raises(ValueError, match="Invalid mode"):
                await client.research.start(
                    notebook_id="nb_123", query="test", mode="invalid"
                )

    @pytest.mark.asyncio
    async def test_start_deep_drive_invalid(self, auth_tokens):
        """Test that deep research with drive source raises ValueError."""
        async with NotebookLMClient(auth_tokens) as client:
            with pytest.raises(ValueError, match="Deep Research only supports Web"):
                await client.research.start(
                    notebook_id="nb_123", query="test", source="drive", mode="deep"
                )

    @pytest.mark.asyncio
    async def test_start_research_returns_none(self, auth_tokens, httpx_mock):
        """Test start returns None on empty response."""
        response_json = json.dumps([])
        chunk = json.dumps(
            ["wrb.fr", RPCMethod.START_FAST_RESEARCH.value, response_json, None, None]
        )
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.research.start(
                notebook_id="nb_123", query="test", mode="fast"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_poll_no_research(self, auth_tokens, httpx_mock):
        """Test poll returns no_research on empty response."""
        response_json = json.dumps([])
        chunk = json.dumps(["wrb.fr", RPCMethod.POLL_RESEARCH.value, response_json, None, None])
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.research.poll("nb_123")

        assert result["status"] == "no_research"

    @pytest.mark.asyncio
    async def test_poll_in_progress(self, auth_tokens, httpx_mock):
        """Test poll returns in_progress status."""
        task_info = [
            None,
            ["research query", 1],
            1,
            [[], ""],  # no sources yet
            1,  # status: in_progress (not 2)
        ]
        response_json = json.dumps([[["task_123", task_info]]])
        chunk = json.dumps(["wrb.fr", RPCMethod.POLL_RESEARCH.value, response_json, None, None])
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.research.poll("nb_123")

        assert result["status"] == "in_progress"
        assert result["query"] == "research query"

    @pytest.mark.asyncio
    async def test_poll_deep_research_sources(self, auth_tokens, httpx_mock):
        """Test poll parses deep research sources (title only, no URL)."""
        # Deep research format: [None, title, None, type, ...]
        sources = [
            [None, "Deep Research Finding", None, 2],
        ]
        task_info = [
            None,
            ["deep query", 1],
            1,
            [sources, "Deep summary"],
            2,
        ]
        response_json = json.dumps([[["task_123", task_info]]])
        chunk = json.dumps(["wrb.fr", RPCMethod.POLL_RESEARCH.value, response_json, None, None])
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.research.poll("nb_123")

        assert result["status"] == "completed"
        assert len(result["sources"]) == 1
        assert result["sources"][0]["title"] == "Deep Research Finding"
        assert result["sources"][0]["url"] == ""

    @pytest.mark.asyncio
    async def test_import_empty_sources(self, auth_tokens):
        """Test import_sources with empty list returns empty list."""
        async with NotebookLMClient(auth_tokens) as client:
            result = await client.research.import_sources(
                notebook_id="nb_123", task_id="task_123", sources=[]
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_import_sources_missing_url(self, auth_tokens, httpx_mock):
        """Test import_sources handles sources without URL."""
        response_json = json.dumps([[[["src_new"], "Imported"]]])
        chunk = json.dumps(["wrb.fr", RPCMethod.IMPORT_RESEARCH.value, response_json, None, None])
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            # Source without url key
            sources = [{"title": "Title Only"}]
            result = await client.research.import_sources(
                notebook_id="nb_123", task_id="task_123", sources=sources
            )

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_import_sources_empty_response(self, auth_tokens, httpx_mock):
        """Test import_sources handles empty API response."""
        response_json = json.dumps([])
        chunk = json.dumps(["wrb.fr", RPCMethod.IMPORT_RESEARCH.value, response_json, None, None])
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            sources = [{"url": "http://example.com", "title": "Example"}]
            result = await client.research.import_sources(
                notebook_id="nb_123", task_id="task_123", sources=sources
            )

        assert result == []

    @pytest.mark.asyncio
    async def test_import_sources_malformed_response(self, auth_tokens, httpx_mock):
        """Test import_sources handles malformed response gracefully."""
        # Response with invalid structure (missing id)
        response_json = json.dumps([[["not_a_list", "Title"]]])
        chunk = json.dumps(["wrb.fr", RPCMethod.IMPORT_RESEARCH.value, response_json, None, None])
        response_body = f")]}}'\n{len(chunk)}\n{chunk}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            sources = [{"url": "http://example.com", "title": "Example"}]
            result = await client.research.import_sources(
                notebook_id="nb_123", task_id="task_123", sources=sources
            )

        # Should handle gracefully and return empty (no valid id found)
        assert result == []

    @pytest.mark.asyncio
    async def test_full_workflow_poll_to_import(self, auth_tokens, httpx_mock):
        """Test that sources from poll() can be passed directly to import_sources().

        This simulates the real UI workflow:
        1. Start research
        2. Poll until complete (get sources)
        3. User confirms sources to import
        4. Import selected sources

        The key validation is that poll() output format is compatible with
        import_sources() input format.
        """
        # Step 1: Mock start response
        start_response = json.dumps(["task_123", None])
        start_chunk = json.dumps(
            ["wrb.fr", RPCMethod.START_FAST_RESEARCH.value, start_response, None, None]
        )

        # Step 2: Mock poll response with sources (fast research format)
        poll_sources = [
            ["http://example.com/article1", "First Article", "Description 1", 1],
            ["http://example.com/article2", "Second Article", "Description 2", 1],
            ["http://example.com/article3", "Third Article", "Description 3", 1],
        ]
        task_info = [
            None,
            ["AI research query", 1],
            1,
            [poll_sources, "Summary of findings"],
            2,  # completed
        ]
        poll_response = json.dumps([[["task_123", task_info]]])
        poll_chunk = json.dumps(
            ["wrb.fr", RPCMethod.POLL_RESEARCH.value, poll_response, None, None]
        )

        # Step 3: Mock import response
        # Format: [[[id_array, title], [id_array, title], ...]]
        import_response = json.dumps([[
            [["src_001"], "First Article"],
            [["src_002"], "Second Article"],
        ]])
        import_chunk = json.dumps(
            ["wrb.fr", RPCMethod.IMPORT_RESEARCH.value, import_response, None, None]
        )

        # Add responses in order
        httpx_mock.add_response(
            content=f")]}}'\n{len(start_chunk)}\n{start_chunk}\n".encode(),
            method="POST",
        )
        httpx_mock.add_response(
            content=f")]}}'\n{len(poll_chunk)}\n{poll_chunk}\n".encode(),
            method="POST",
        )
        httpx_mock.add_response(
            content=f")]}}'\n{len(import_chunk)}\n{import_chunk}\n".encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            # Start research
            start_result = await client.research.start(
                notebook_id="nb_123",
                query="AI research query",
                mode="fast",
            )
            assert start_result is not None
            task_id = start_result["task_id"]

            # Poll for results
            poll_result = await client.research.poll("nb_123")
            assert poll_result["status"] == "completed"
            sources = poll_result["sources"]
            assert len(sources) == 3

            # Verify sources have expected format
            for src in sources:
                assert "url" in src
                assert "title" in src

            # User selects first 2 sources to import
            sources_to_import = sources[:2]

            # Import selected sources - THIS IS THE KEY TEST
            # The sources from poll() should work with import_sources()
            imported = await client.research.import_sources(
                notebook_id="nb_123",
                task_id=task_id,
                sources=sources_to_import,
            )

            # Verify import succeeded
            assert len(imported) == 2
            assert imported[0]["id"] == "src_001"
            assert imported[0]["title"] == "First Article"
            assert imported[1]["id"] == "src_002"
            assert imported[1]["title"] == "Second Article"
