"""Tests for conversation functionality."""

import json

import pytest

from notebooklm import AskResult, NotebookLMClient
from notebooklm._chat import ChatAPI
from notebooklm._core import ClientCore
from notebooklm.auth import AuthTokens


@pytest.fixture
def auth_tokens():
    return AuthTokens(
        cookies={"SID": "test"},
        csrf_token="test_csrf",
        session_id="test_session",
    )


class TestAsk:
    @pytest.mark.asyncio
    async def test_ask_new_conversation(self, auth_tokens, httpx_mock):
        import re

        # Mock ask response (streaming chunks)
        inner_json = json.dumps(
            [
                [
                    "This is the answer. It is now long enough to be valid.",
                    None,
                    None,
                    None,
                    [1],
                ]
            ]
        )
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])

        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        httpx_mock.add_response(
            url=re.compile(r".*GenerateFreeFormStreamed.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.ask(
                notebook_id="nb_123",
                question="What is this?",
                source_ids=["test_source"],
            )

        assert isinstance(result, AskResult)
        assert result.answer == "This is the answer. It is now long enough to be valid."
        assert result.is_follow_up is False
        assert result.turn_number == 1

    @pytest.mark.asyncio
    async def test_ask_follow_up(self, auth_tokens, httpx_mock):
        inner_json = json.dumps(
            [
                [
                    "Follow-up answer. This also needs to be longer than twenty characters.",
                    None,
                    None,
                    None,
                    [1],
                ]
            ]
        )
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        _TEST_CONV_ID = "a1b2c3d4-0000-0000-0000-000000000002"
        async with NotebookLMClient(auth_tokens) as client:
            # Seed cache via core client
            client._core._conversation_cache[_TEST_CONV_ID] = [
                {"query": "Q1", "answer": "A1", "turn_number": 1}
            ]

            result = await client.chat.ask(
                notebook_id="nb_123",
                question="Follow up?",
                conversation_id=_TEST_CONV_ID,
                source_ids=["test_source"],
            )

        assert isinstance(result, AskResult)
        assert (
            result.answer
            == "Follow-up answer. This also needs to be longer than twenty characters."
        )
        assert result.is_follow_up is True
        assert result.turn_number == 2


class TestParseExchangeId:
    def test_extracts_exchange_id_from_response(self):
        """_parse_ask_response_with_references returns exchange_id from first[2][1]."""
        inner_json = json.dumps(
            [
                [
                    "The answer text.",
                    None,
                    ["conv-uuid-111", "exchange-uuid-222", 12345],
                    None,
                    [1],
                ]
            ]
        )
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        auth = AuthTokens(
            cookies={"SID": "test"},
            csrf_token="test_csrf",
            session_id="test_session",
        )
        core = ClientCore(auth)
        api = ChatAPI(core)

        _, _, exchange_id = api._parse_ask_response_with_references(response_body)
        assert exchange_id == "exchange-uuid-222"

    def test_returns_none_when_first2_missing(self):
        """Gracefully returns None if first[2] is absent."""
        inner_json = json.dumps([["The answer text.", None, None, None, [1]]])
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        auth = AuthTokens(
            cookies={"SID": "test"},
            csrf_token="test_csrf",
            session_id="test_session",
        )
        core = ClientCore(auth)
        api = ChatAPI(core)

        _, _, exchange_id = api._parse_ask_response_with_references(response_body)
        assert exchange_id is None


class TestAskExchangeId:
    @pytest.mark.asyncio
    async def test_ask_returns_exchange_id(self, auth_tokens, httpx_mock):
        """ask() should return the exchange_id from first[2][1]."""
        import re

        inner_json = json.dumps(
            [
                [
                    "The answer. Long enough to be valid for testing purposes.",
                    None,
                    ["conv-uuid-000", "exchange-uuid-abc", 99999],
                    None,
                    [1],
                ]
            ]
        )
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        httpx_mock.add_response(
            url=re.compile(r".*GenerateFreeFormStreamed.*"),
            content=response_body.encode(),
            method="POST",
        )

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.ask(
                notebook_id="nb_123",
                question="What is this?",
                source_ids=["test_source"],
            )

        assert result.exchange_id == "exchange-uuid-abc"

    @pytest.mark.asyncio
    async def test_ask_follow_up_accepts_exchange_id(self, auth_tokens, httpx_mock):
        """Follow-up with exchange_id should succeed and return new exchange_id."""
        inner_json = json.dumps(
            [
                [
                    "Follow-up answer. Long enough to be valid for testing.",
                    None,
                    ["conv-uuid-000", "exchange-uuid-xyz", 99999],
                    None,
                    [1],
                ]
            ]
        )
        chunk_json = json.dumps([["wrb.fr", None, inner_json]])
        response_body = f")]}}'\n{len(chunk_json)}\n{chunk_json}\n"

        httpx_mock.add_response(content=response_body.encode(), method="POST")

        async with NotebookLMClient(auth_tokens) as client:
            result = await client.chat.ask(
                notebook_id="nb_123",
                question="Follow up?",
                conversation_id="conv-uuid-000",
                exchange_id="exchange-uuid-abc",
                source_ids=["test_source"],
            )

        assert result.exchange_id == "exchange-uuid-xyz"
