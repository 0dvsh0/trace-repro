"""The MCP surface an agent sees.

The agent must be able to *see* the collision and *read* the ordered replay,
but it must not be able to hand back a patch it invented. These tests pin that
boundary.
"""

from __future__ import annotations

import io
import json

import pytest

from trace_repro.mcp.server import PROTOCOL_VERSION, MCPServer

from .synthetic import build_incident


@pytest.fixture()
def server(memory_store):
    build_incident(memory_store)
    return MCPServer(memory_store)


def call(server: MCPServer, name: str, arguments: dict | None = None) -> dict:
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })
    assert response is not None
    assert "error" not in response, response["error"]
    return json.loads(response["result"]["content"][0]["text"])


def test_initialize_advertises_tool_capability(server):
    response = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                              "params": {}})
    result = response["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "trace-repro"


def test_tools_list_includes_the_collision_query(server):
    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"] for t in response["result"]["tools"]}
    assert "get_cross_trace_collision_graph" in tools
    assert "minimal_replay_subgraph" in tools
    assert "upstream_mutator" in tools


def test_no_tool_can_edit_source_or_write_a_patch(server):
    """The agent may look; it may not act on the repository."""
    response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    for tool in response["result"]["tools"]:
        assert set(tool["inputSchema"]["properties"]) & {
            "path", "source", "patch", "diff", "command", "shell",
        } == set(), f"{tool['name']} exposes a write primitive"


def test_collision_tool_returns_cross_trace_writers(server):
    payload = call(server, "get_cross_trace_collision_graph", {"crash_span_id": "span_C"})
    endpoints = {c["other_endpoint"] for c in payload["collisions"]}
    assert endpoints == {"POST /webhooks/payment", "cart-expiry worker"}
    assert all(c["other_trace_id"] != "trace_C" for c in payload["collisions"])
    assert all(c["lock_held"] is False for c in payload["collisions"])


def test_collision_tool_decodes_payloads(server):
    payload = call(server, "get_cross_trace_collision_graph", {"crash_span_id": "span_C"})
    for c in payload["collisions"]:
        assert isinstance(c["other_payload"], dict)


def test_collision_tool_says_so_when_there_is_nothing(server):
    payload = call(server, "get_cross_trace_collision_graph", {"crash_span_id": "span_A"})
    assert payload["collisions"] == []
    assert "not state-interference" in payload["verdict"]


def test_replay_tool_returns_ordered_offsets(server):
    payload = call(server, "minimal_replay_subgraph", {"crash_span_id": "span_C"})
    offsets = [s["offset_ms"] for s in payload["replay"]]
    assert offsets == sorted(offsets)
    assert offsets[0] == pytest.approx(0.0, abs=0.1)
    assert len(payload["replay"]) == 3
    assert payload["crash_payload"]["order_id"] if "order_id" in payload["crash_payload"] \
        else payload["crash_payload"] == {"order_id": 8812, "amount_paise": 450000}


def test_replay_tool_names_the_mutators(server):
    payload = call(server, "minimal_replay_subgraph", {"crash_span_id": "span_C"})
    functions = {s["function"] for s in payload["replay"]}
    assert {"apply_webhook", "expire_cart"} <= functions


def test_upstream_tool_lists_every_unlocked_writer(server):
    payload = call(server, "upstream_mutator",
                   {"entity_type": "orders", "primary_key": "8812",
                    "crash_span_id": "span_C"})
    assert payload["must_repair_all_unlocked_writers"] == ["apply_webhook", "expire_cart"]
    assert "lost update" in payload["note"]


def test_locate_crash_finds_the_error_span(server):
    payload = call(server, "locate_crash", {"error_type": "InvalidStateError"})
    assert payload["count"] == 1
    assert payload["error_spans"][0]["span_id"] == "span_C"


def test_graph_stats(server):
    payload = call(server, "graph_stats")
    assert payload["spans"] == 3
    assert payload["traces"] == 3
    assert payload["backend"] == "memory"


def test_missing_argument_is_a_clear_error(server):
    response = server.handle({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "get_cross_trace_collision_graph", "arguments": {}},
    })
    assert response["error"]["message"] == "missing required argument: 'crash_span_id'"


def test_unknown_tool_is_rejected(server):
    response = server.handle({
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {"name": "rm_minus_rf", "arguments": {}},
    })
    assert "rm_minus_rf" in response["error"]["message"]


def test_unknown_method_is_rejected(server):
    response = server.handle({"jsonrpc": "2.0", "id": 11, "method": "drop_database"})
    assert response["error"]["code"] == -32601


def test_notifications_get_no_response(server):
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ping(server):
    response = server.handle({"jsonrpc": "2.0", "id": 12, "method": "ping"})
    assert response["result"] == {}


def test_stdio_transport_round_trip(server):
    """Drive the server exactly the way an MCP client would."""
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "get_cross_trace_collision_graph",
                    "arguments": {"crash_span_id": "span_C"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "shutdown"},
    ]
    stdin = io.StringIO("\n".join(json.dumps(line) for line in lines) + "\n")
    stdout = io.StringIO()
    server.serve_forever(stdin, stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert [r["id"] for r in responses] == [1, 2, 3, 4]
    assert len(responses[2]["result"]["content"][0]["text"]) > 0


def test_malformed_input_does_not_crash_the_server(server):
    stdin = io.StringIO("not json\n")
    stdout = io.StringIO()
    server.serve_forever(stdin, stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == -32700


def test_instructions_forbid_stack_trace_only_fixing(server):
    response = server.handle({"jsonrpc": "2.0", "id": 13, "method": "initialize",
                              "params": {}})
    text = response["result"]["instructions"].lower()
    assert "never propose a fix from a stack trace alone" in text
