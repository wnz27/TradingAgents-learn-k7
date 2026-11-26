"""Exercise the LongCat Responses wire format without credentials or network."""

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel

from cli.prompts import provider_default_url
from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.llm_clients.model_catalog import get_model_options


def test_longcat_requires_its_own_key_and_endpoint(monkeypatch):
    monkeypatch.delenv("LONGCAT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "unused-openai-key")
    with pytest.raises(ValueError, match="LONGCAT_API_KEY"):
        create_llm_client("longcat", "LongCat-2.5").get_llm()
    with pytest.raises(ValueError, match="only connects"):
        create_llm_client("longcat", "LongCat-2.5", base_url="https://relay.example/v1").get_llm()

    assert provider_default_url("longcat") == "https://api.longcat.chat/openai/v1"
    for tier in ("quick", "deep"):
        assert get_model_options("longcat", tier)[0][1] == "LongCat-2.5"


class Rating(BaseModel):
    rating: str


def test_longcat_can_replay_tool_results_and_parse_a_rating(monkeypatch):
    monkeypatch.setenv("LONGCAT_API_KEY", "test-longcat-key")
    payloads = []

    def respond(request):
        assert str(request.url) == "https://api.longcat.chat/openai/v1/responses"
        assert request.headers["Authorization"] == "Bearer test-longcat-key"
        payload = json.loads(request.content)
        assert payload["model"] == "LongCat-2.5"
        payloads.append(payload)
        if len(payloads) == 1:
            output = {
                "type": "function_call", "id": "fc_123", "status": "completed",
                "call_id": "call_123", "name": "quote", "arguments": '{"ticker":"AAPL"}',
            }
        else:
            output = {
                "type": "message", "id": "msg_123", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": '{"rating":"HOLD"}', "annotations": []}],
            }
        return httpx.Response(200, json={
            "id": "resp_123", "object": "response", "status": "completed", "created_at": 1,
            "model": "LongCat-2.5", "output": [output],
        })

    def quote(ticker: str) -> str:
        """Look up a ticker."""
        return f"{ticker} close is 100"

    question = HumanMessage(content="Look up AAPL and give a rating.")
    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        llm = create_llm_client("longcat", "LongCat-2.5", http_client=http_client).get_llm()
        call = llm.bind_tools([quote]).invoke([question])
        assert call.tool_calls[0]["name"] == "quote"
        answer = ToolMessage(content=quote("AAPL"), tool_call_id=call.tool_calls[0]["id"])
        result = llm.with_structured_output(Rating).invoke([question, call, answer])

    assert result == Rating(rating="HOLD")
    assert payloads[0]["tools"][0]["name"] == "quote"
    assert payloads[1]["input"][-2]["type"] == "function_call"
    assert payloads[1]["input"][-1] == {
        "type": "function_call_output", "call_id": "call_123", "output": "AAPL close is 100",
    }
    assert payloads[1]["text"]["format"]["type"] == "json_schema"
    assert "tool_choice" not in payloads[1]
