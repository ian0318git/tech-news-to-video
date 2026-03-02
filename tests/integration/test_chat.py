"""Integration tests for ChatAPI."""

import pytest
from pytest_httpx import HTTPXMock

from notebooklm import NotebookLMClient
from notebooklm.rpc import ChatGoal, ChatResponseLength, RPCMethod
from notebooklm.types import ChatMode


class TestChatAPI:
    """Integration tests for the ChatAPI."""

    @pytest.mark.asyncio
    async def test_get_last_conversation_id(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test get_last_conversation_id returns the most recent conversation ID."""
        response = build_rpc_response(
            RPCMethod.GET_LAST_CONVERSATION_ID,
            [[["conv_001"]]],
        )
        httpx_mock.add_response(content=response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.get_last_conversation_id("nb_123")

        assert result == "conv_001"
        request = httpx_mock.get_request()
        assert RPCMethod.GET_LAST_CONVERSATION_ID in str(request.url)

    @pytest.mark.asyncio
    async def test_get_history(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test get_history returns Q&A pairs from the last conversation."""
        # First call: get_last_conversation_id
        id_response = build_rpc_response(
            RPCMethod.GET_LAST_CONVERSATION_ID,
            [[["conv_001"]]],
        )
        # Second call: get_conversation_turns
        # API returns individual turns newest-first: A2, Q2, A1, Q1
        turns_response = build_rpc_response(
            RPCMethod.GET_CONVERSATION_TURNS,
            [
                [
                    [None, None, 2, None, [["Answer to second question."]]],
                    [None, None, 1, "Second question?"],
                    [None, None, 2, None, [["Answer to first question."]]],
                    [None, None, 1, "First question?"],
                ]
            ],
        )
        httpx_mock.add_response(content=id_response.encode())
        httpx_mock.add_response(content=turns_response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.get_history("nb_123")

        # get_history reverses API order to return oldest-first
        assert len(result) == 2
        assert result[0] == ("First question?", "Answer to first question.")
        assert result[1] == ("Second question?", "Answer to second question.")

    @pytest.mark.asyncio
    async def test_get_conversation_turns(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test getting conversation turns for a specific conversation.

        The khqZz RPC returns Q&A turns for a conversation:
          turn[2] == 1: user question, text at turn[3]
          turn[2] == 2: AI answer, text at turn[4][0][0]
        Turns are returned newest-first; limit=2 yields the latest Q&A pair.
        """
        response = build_rpc_response(
            RPCMethod.GET_CONVERSATION_TURNS,
            [
                [
                    [None, None, 1, "What is machine learning?"],
                    [None, None, 2, None, [["Machine learning is a branch of AI."]]],
                ]
            ],
        )
        httpx_mock.add_response(content=response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.get_conversation_turns("nb_123", "conv_001", limit=2)

        assert result is not None
        turns = result[0]
        assert len(turns) == 2

        # Turn type 1: user question
        assert turns[0][2] == 1
        assert turns[0][3] == "What is machine learning?"

        # Turn type 2: AI answer
        assert turns[1][2] == 2
        assert turns[1][4][0][0] == "Machine learning is a branch of AI."

        request = httpx_mock.get_request()
        assert RPCMethod.GET_CONVERSATION_TURNS in str(request.url)

    @pytest.mark.asyncio
    async def test_get_conversation_turns_empty(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test get_conversation_turns handles empty turn list gracefully."""
        response = build_rpc_response(
            RPCMethod.GET_CONVERSATION_TURNS,
            [[]],
        )
        httpx_mock.add_response(content=response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.get_conversation_turns("nb_123", "conv_001")

        assert result is not None
        assert result[0] == []

    @pytest.mark.asyncio
    async def test_history_save_as_note(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test the combined get_history + notes.create flow for 'history --save'."""
        from notebooklm.cli.chat import _format_all_qa

        # get_last_conversation_id
        id_response = build_rpc_response(
            RPCMethod.GET_LAST_CONVERSATION_ID,
            [[["conv_001"]]],
        )
        # get_conversation_turns (chronological: Q, A, Q, A)
        turns_response = build_rpc_response(
            RPCMethod.GET_CONVERSATION_TURNS,
            [
                [
                    [None, None, 1, "What is ML?"],
                    [None, None, 2, None, [["Machine learning is a type of AI."]]],
                    [None, None, 1, "Explain AI"],
                    [None, None, 2, None, [["AI stands for Artificial Intelligence."]]],
                ]
            ],
        )
        create_response = build_rpc_response(RPCMethod.CREATE_NOTE, [["new_note_id"]])
        update_response = build_rpc_response(RPCMethod.UPDATE_NOTE, None)

        httpx_mock.add_response(content=id_response.encode())
        httpx_mock.add_response(content=turns_response.encode())
        httpx_mock.add_response(content=create_response.encode())
        httpx_mock.add_response(content=update_response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            qa_pairs = await client.chat.get_history("nb_123")
            content = _format_all_qa(qa_pairs)
            note = await client.notes.create("nb_123", "Chat History", content)

        assert note.id == "new_note_id"
        assert note.title == "Chat History"
        assert "What is ML?" in note.content
        assert "Machine learning" in note.content
        assert "Explain AI" in note.content

        requests = httpx_mock.get_requests()
        assert RPCMethod.GET_LAST_CONVERSATION_ID in str(requests[0].url)
        assert RPCMethod.GET_CONVERSATION_TURNS in str(requests[1].url)
        assert RPCMethod.CREATE_NOTE in str(requests[2].url)

    @pytest.mark.asyncio
    async def test_get_history_empty(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test getting empty conversation history when no conversations exist."""
        response = build_rpc_response(RPCMethod.GET_LAST_CONVERSATION_ID, [])
        httpx_mock.add_response(content=response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.get_history("nb_123")

        assert result == []

    @pytest.mark.asyncio
    async def test_configure_default_mode(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test configuring chat with default settings."""
        response = build_rpc_response(RPCMethod.RENAME_NOTEBOOK, None)
        httpx_mock.add_response(content=response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            await client.chat.configure("nb_123")

        request = httpx_mock.get_request()
        assert RPCMethod.RENAME_NOTEBOOK in str(request.url)

    @pytest.mark.asyncio
    async def test_configure_learning_guide_mode(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test configuring chat as learning guide."""
        response = build_rpc_response(RPCMethod.RENAME_NOTEBOOK, None)
        httpx_mock.add_response(content=response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            await client.chat.configure(
                "nb_123",
                goal=ChatGoal.LEARNING_GUIDE,
                response_length=ChatResponseLength.LONGER,
            )

        request = httpx_mock.get_request()
        assert RPCMethod.RENAME_NOTEBOOK in str(request.url)

    @pytest.mark.asyncio
    async def test_configure_custom_mode_without_prompt_raises(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
    ):
        """Test that CUSTOM mode without prompt raises ValidationError."""
        from notebooklm.exceptions import ValidationError

        async with NotebookLMClient(auth_tokens) as client:
            with pytest.raises(ValidationError, match="custom_prompt is required"):
                await client.chat.configure("nb_123", goal=ChatGoal.CUSTOM)

    @pytest.mark.asyncio
    async def test_configure_custom_mode_with_prompt(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test configuring chat with custom prompt."""
        response = build_rpc_response(RPCMethod.RENAME_NOTEBOOK, None)
        httpx_mock.add_response(content=response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            await client.chat.configure(
                "nb_123",
                goal=ChatGoal.CUSTOM,
                custom_prompt="You are a helpful tutor.",
            )

        request = httpx_mock.get_request()
        assert RPCMethod.RENAME_NOTEBOOK in str(request.url)

    @pytest.mark.asyncio
    async def test_set_mode(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
        build_rpc_response,
    ):
        """Test setting chat mode with predefined config."""
        response = build_rpc_response(RPCMethod.RENAME_NOTEBOOK, None)
        httpx_mock.add_response(content=response.encode())

        async with NotebookLMClient(auth_tokens) as client:
            await client.chat.set_mode("nb_123", ChatMode.CONCISE)

        request = httpx_mock.get_request()
        assert RPCMethod.RENAME_NOTEBOOK in str(request.url)

    def test_get_cached_turns_empty(self, auth_tokens):
        """Test getting cached turns for new conversation."""
        client = NotebookLMClient(auth_tokens)
        turns = client.chat.get_cached_turns("nonexistent_conv")
        assert turns == []

    def test_clear_cache(self, auth_tokens):
        """Test clearing conversation cache."""
        client = NotebookLMClient(auth_tokens)
        result = client.chat.clear_cache("some_conv")
        assert result is False

    def test_clear_all_cache(self, auth_tokens):
        """Test clearing all conversation caches."""
        client = NotebookLMClient(auth_tokens)
        result = client.chat.clear_cache()
        assert result is True


class TestChatReferences:
    """Integration tests for chat references and citations."""

    @pytest.mark.asyncio
    async def test_ask_with_citations_returns_references(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
    ):
        """Test ask() returns references when citations are present."""
        import json
        import re

        # Build a realistic response with citations
        # Structure discovered via API analysis:
        # cite[1][4] = [[passage_wrapper]] where passage_wrapper[0] = [start, end, nested]
        # nested = [[inner]] where inner = [start2, end2, text]
        inner_data = [
            [
                "Machine learning is a subset of AI [1]. It uses algorithms to learn from data [2].",
                None,
                ["chunk-001", "chunk-002", 987654],
                None,
                [
                    [],
                    None,
                    None,
                    [
                        # First citation
                        [
                            ["chunk-001"],
                            [
                                None,
                                None,
                                0.95,
                                [[None]],
                                [  # cite[1][4] - text passages
                                    [  # passage_wrapper
                                        [  # passage_data
                                            100,  # start_char
                                            250,  # end_char
                                            [  # nested passages
                                                [  # nested_group
                                                    [  # inner
                                                        50,
                                                        120,
                                                        "Machine learning is a branch of artificial intelligence.",
                                                    ]
                                                ]
                                            ],
                                        ]
                                    ]
                                ],
                                [[[["11111111-1111-1111-1111-111111111111"]]]],
                                ["chunk-001"],
                            ],
                        ],
                        # Second citation
                        [
                            ["chunk-002"],
                            [
                                None,
                                None,
                                0.88,
                                [[None]],
                                [
                                    [
                                        [
                                            300,
                                            450,
                                            [
                                                [
                                                    [
                                                        280,
                                                        380,
                                                        "Algorithms learn patterns from training data.",
                                                    ]
                                                ]
                                            ],
                                        ]
                                    ]
                                ],
                                [[[["22222222-2222-2222-2222-222222222222"]]]],
                                ["chunk-002"],
                            ],
                        ],
                    ],
                    1,
                ],
            ]
        ]
        inner_json = json.dumps(inner_data)
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        httpx_mock.add_response(
            url=re.compile(r".*GenerateFreeFormStreamed.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.ask(
                notebook_id="test_nb",
                question="What is machine learning?",
                source_ids=["src_001"],
            )

        # Verify answer
        assert "Machine learning" in result.answer
        assert "[1]" in result.answer
        assert "[2]" in result.answer

        # Verify references
        assert len(result.references) == 2

        # First reference
        ref1 = result.references[0]
        assert ref1.source_id == "11111111-1111-1111-1111-111111111111"
        assert ref1.citation_number == 1
        assert "artificial intelligence" in ref1.cited_text

        # Second reference
        ref2 = result.references[1]
        assert ref2.source_id == "22222222-2222-2222-2222-222222222222"
        assert ref2.citation_number == 2
        assert "training data" in ref2.cited_text

    @pytest.mark.asyncio
    async def test_ask_without_citations(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
    ):
        """Test ask() works when no citations are in the response."""
        import json
        import re

        inner_data = [
            [
                "This is a simple answer without any source citations.",
                None,
                [12345],
                None,
                [[], None, None, [], 1],
            ]
        ]
        inner_json = json.dumps(inner_data)
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        httpx_mock.add_response(
            url=re.compile(r".*GenerateFreeFormStreamed.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.ask(
                notebook_id="test_nb",
                question="Simple question",
                source_ids=["src_001"],
            )

        assert result.answer == "This is a simple answer without any source citations."
        assert len(result.references) == 0

    @pytest.mark.asyncio
    async def test_references_include_char_positions(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
    ):
        """Test that references include character position information."""
        import json
        import re

        inner_data = [
            [
                "Answer with citation [1].",
                None,
                ["chunk-001", 12345],
                None,
                [
                    [],
                    None,
                    None,
                    [
                        [
                            ["chunk-001"],
                            [
                                None,
                                None,
                                0.9,
                                [[None]],
                                [
                                    [
                                        [
                                            1000,  # start_char
                                            1500,  # end_char
                                            [[[[950, 1100, "Cited passage text."]]]],
                                        ]
                                    ]
                                ],
                                [[[["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]]]],
                                ["chunk-001"],
                            ],
                        ],
                    ],
                    1,
                ],
            ]
        ]
        inner_json = json.dumps(inner_data)
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        httpx_mock.add_response(
            url=re.compile(r".*GenerateFreeFormStreamed.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.ask(
                notebook_id="test_nb",
                question="Question",
                source_ids=["src_001"],
            )

        assert len(result.references) == 1
        ref = result.references[0]
        assert ref.start_char == 1000
        assert ref.end_char == 1500
        assert ref.chunk_id == "chunk-001"

    @pytest.mark.asyncio
    async def test_ask_returns_answer_when_marker_absent(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
    ):
        """Test ask() extracts answer when API response lacks type_info[-1]==1 marker.

        Regression test for issue #118: Google's API may change or omit the answer
        marker, causing the parser to fall back to the longest unmarked text chunk.
        """
        import json
        import re

        # Response with no trailing `1` marker in type_info — simulates changed API format
        inner_data = [
            [
                "This is a valid answer returned without the answer marker.",
                None,
                ["chunk-001", 12345],
                None,
                [[], None, None, []],  # type_info has no trailing 1
            ]
        ]
        inner_json = json.dumps(inner_data)
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        httpx_mock.add_response(
            url=re.compile(r".*GenerateFreeFormStreamed.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.ask(
                notebook_id="test_nb",
                question="What does this say?",
                source_ids=["src_001"],
            )

        assert result.answer == "This is a valid answer returned without the answer marker."
        assert result.conversation_id is not None
        assert result.is_follow_up is False

    @pytest.mark.asyncio
    async def test_ask_prefers_marked_over_unmarked_in_streaming_response(
        self,
        auth_tokens,
        httpx_mock: HTTPXMock,
    ):
        """Test ask() picks the marked answer when response has both marked and unmarked chunks.

        Streaming responses can contain multiple chunks. The marked answer chunk
        (type_info[-1]==1) must win even when an unmarked chunk has longer text.
        """
        import json
        import re

        # Streaming response: first chunk is a longer unmarked preamble,
        # second chunk is the shorter but marked real answer.
        preamble = [
            [
                "This is a long preamble or status message that is not the real answer to the question at all.",
                None,
                ["chunk-001", 11111],
                None,
                [[], None, None, []],  # no marker
            ]
        ]
        answer = [
            [
                "The real answer.",
                None,
                ["chunk-002", 22222],
                None,
                [[], None, None, [], 1],  # marked
            ]
        ]

        def make_chunk(inner_data):
            inner_json = json.dumps(inner_data)
            chunk_json = json.dumps([["wrb.fr", None, inner_json]])
            return f"{len(chunk_json)}\n{chunk_json}"

        response_body = f")]}}'\n{make_chunk(preamble)}\n{make_chunk(answer)}\n"

        httpx_mock.add_response(
            url=re.compile(r".*GenerateFreeFormStreamed.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.ask(
                notebook_id="test_nb",
                question="What is the answer?",
                source_ids=["src_001"],
            )

        assert result.answer == "The real answer."
