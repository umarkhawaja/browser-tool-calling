"""Tests for the Ollama chat client. httpx is mocked with respx, so these run
without a live Ollama server."""

import json

import httpx
import pytest
import respx

from app.config import OLLAMA_URL
from app.llm import (
    _ELIDED,
    CONTEXT_BUDGET_CHARS,
    LLMError,
    chat_json,
    chat_tools,
    fit_to_context,
)


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def _ndjson(*chunks: dict) -> httpx.Response:
    """Ollama's streaming wire format: one JSON object per line."""
    lines = [json.dumps(c) for c in chunks]
    return httpx.Response(200, text="\n".join(lines) + "\n")


@respx.mock
async def test_parses_valid_json():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_reply('{"action": "done", "answer": "hi"}')
    )
    out = await chat_json([{"role": "user", "content": "x"}])
    assert out == {"action": "done", "answer": "hi"}


@respx.mock
async def test_invalid_json_raises_llmerror():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply("not json at all"))
    with pytest.raises(LLMError):
        await chat_json([{"role": "user", "content": "x"}])


@respx.mock
async def test_connection_failure_raises_llmerror():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(LLMError):
        await chat_json([{"role": "user", "content": "x"}])


# --- chat_tools: streaming reassembly --------------------------------------
async def _collect(messages=None, tools=None):
    seen = []

    async def on_token(delta):
        seen.append(delta)

    msg = await chat_tools(messages or [], tools or [], on_token)
    return msg, seen


@respx.mock
async def test_stream_reassembles_text_and_reports_tokens():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_ndjson(
            {"message": {"content": "The top "}, "done": False},
            {"message": {"content": "story is Rust."}, "done": False},
            {"message": {"content": ""}, "done": True},
        )
    )
    msg, seen = await _collect()
    assert msg["content"] == "The top story is Rust."
    assert seen == ["The top ", "story is Rust."]
    # No tool call was made, so the key is absent rather than an empty list —
    # this message gets fed straight back to Ollama on the next turn.
    assert "tool_calls" not in msg


@respx.mock
async def test_stream_collects_tool_calls():
    # Ollama delivers each tool call whole, in its own chunk.
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_ndjson(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "go_to_url",
                                "arguments": {"url": "https://x.com"},
                            }
                        }
                    ],
                },
                "done": False,
            },
            {"message": {"content": ""}, "done": True},
        )
    )
    msg, seen = await _collect()
    assert msg["tool_calls"][0]["function"]["name"] == "go_to_url"
    assert msg["tool_calls"][0]["function"]["arguments"] == {"url": "https://x.com"}
    assert seen == []  # nothing to show the user on a pure tool-call turn


@respx.mock
async def test_stream_tolerates_blank_and_malformed_lines():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=httpx.Response(
            200,
            text='{"message": {"content": "ok"}, "done": false}\n'
            "\n"
            "not json\n"
            '{"message": {"content": ""}, "done": true}\n',
        )
    )
    msg, _ = await _collect()
    assert msg["content"] == "ok"


@respx.mock
async def test_stream_error_line_raises_llmerror():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_ndjson({"error": "model not found"})
    )
    with pytest.raises(LLMError, match="model not found"):
        await chat_tools([], [])


@respx.mock
async def test_stream_http_error_raises_llmerror():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(LLMError):
        await chat_tools([], [])


# --- fit_to_context --------------------------------------------------------
# What the model is shown has to stay inside `num_ctx`, because the alternative
# is Ollama truncating from the front in silence and taking the task with it.
def _transcript(steps: int, page: str = "PAGE " * 1000) -> list[dict]:
    """A system prompt, a task, then `steps` tool-calling turns over a big page."""
    messages: list[dict] = [
        {"role": "system", "content": "SYSTEM RULES"},
        {"role": "user", "content": "Task: book a table\n\nInteractive elements: ..."},
    ]
    for step in range(steps):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "extract_text", "arguments": {}}}],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_name": "extract_text",
                "content": f"Read step {step}.\n\n{page}",
            }
        )
    return messages


def _size(messages) -> int:
    return len(json.dumps(messages))


def test_a_transcript_that_already_fits_is_left_alone():
    messages = _transcript(2, page="a short page")
    assert fit_to_context(messages) == messages


def test_the_opening_survives_a_transcript_far_over_budget():
    fitted = fit_to_context(_transcript(40))

    assert _size(fitted) <= CONTEXT_BUDGET_CHARS
    assert fitted[0]["content"] == "SYSTEM RULES"
    assert "book a table" in fitted[1]["content"], "the task must never be trimmed"


def test_the_newest_result_is_kept_whole_and_older_ones_come_down_to_a_line():
    messages = _transcript(40)
    fitted = fit_to_context(messages)

    assert fitted[-1] == messages[-1], "the model acts on the newest listing"
    elided = [m for m in fitted if _ELIDED in (m.get("content") or "")]
    assert elided, "older results should be summarised, not only dropped"
    assert "Read step" in elided[0]["content"], "a summary should say what it was"


def test_earlier_steps_are_summarised_before_recent_ones_are_kept_whole():
    # A line per old turn costs a twentieth of the turn itself, so spending the
    # window on another verbatim page listing while whole steps fall off the
    # start is the wrong trade: the model loses any record of what it already
    # tried and is free to do it again. Sweeping the budget finds the band where
    # taking the recent turns whole greedily squeezes the summaries out — on a
    # real page that band is exactly where a 15-step run lands.
    steps = 15
    messages = _transcript(steps)

    for budget in range(6000, _size(messages), 250):
        fitted = fit_to_context(messages, budget=budget)
        results = [m for m in fitted if m.get("role") == "tool"]
        summarised = [m for m in results if _ELIDED in m["content"]]
        verbatim_old = len(results) - len(summarised) - 1  # the newest is whole
        dropped = steps - len(results)
        assert not (dropped and verbatim_old), (
            f"at budget {budget}: {dropped} step(s) dropped outright while "
            f"{verbatim_old} older one(s) were kept in full"
        )


def test_trimming_drops_whole_turns_so_no_tool_result_is_orphaned():
    # Ollama rejects a tool result it cannot trace to a tool call. Sweeping the
    # budget walks the cut across every boundary in the transcript: a trimmer
    # working message by message severs a pair at some of them and looks correct
    # at all the rest, so one fixed budget is not enough to catch it.
    messages = _transcript(12, page="PAGE " * 200)

    for budget in range(400, _size(messages), 311):
        fitted = fit_to_context(messages, budget=budget)
        for i, message in enumerate(fitted):
            if message.get("role") != "tool":
                continue
            previous = next(
                (m for m in reversed(fitted[:i]) if m.get("role") != "tool"), None
            )
            assert previous is not None and previous.get("tool_calls"), (
                f"at budget {budget}, the tool result at {i} answers no tool call"
            )


def test_a_budget_too_small_for_one_turn_still_keeps_the_task_and_the_page():
    # The floor: rather than return something unusable, it keeps the opening and
    # the current page and goes over. The element and extract_text caps are what
    # keep a real run away from here.
    fitted = fit_to_context(_transcript(10), budget=200)

    assert "book a table" in fitted[1]["content"]
    assert fitted[-1]["content"].startswith("Read step 9.")
