"""Tests for FalkorDB Cloud, SSL/TLS, and credentials handling in graph facade and store."""

from __future__ import annotations

from trace_repro.graph.facade import open_graph, parse_graph_url


def test_falkordb_cloud_ssl_url_parsing():
    url = "falkordbs://app-user:secret123@my-cluster.falkordb.cloud:16379/incident_prod"
    parts = parse_graph_url(url)

    assert parts["scheme"] == "falkordbs"
    assert parts["ssl"] is True
    assert parts["host"] == "my-cluster.falkordb.cloud"
    assert parts["port"] == 16379
    assert parts["username"] == "app-user"
    assert parts["password"] == "secret123"
    assert parts["graph"] == "incident_prod"


def test_falkordb_query_param_ssl():
    url = "falkordb://localhost:6379/test_graph?ssl=true"
    parts = parse_graph_url(url)

    assert parts["ssl"] is True
    assert parts["graph"] == "test_graph"
    assert parts["host"] == "localhost"
    assert parts["port"] == 6379


def test_falkordb_password_only():
    url = "falkordb://:onlypassword@remote-redis.com:6380/secure_graph"
    parts = parse_graph_url(url)

    assert parts["password"] == "onlypassword"
    assert parts["graph"] == "secure_graph"
    assert parts["host"] == "remote-redis.com"
    assert parts["port"] == 6380


def test_falkordb_netloc_with_port():
    url = "falkordb://my-host.internal:6379"
    parts = parse_graph_url(url)

    assert parts["host"] == "my-host.internal"
    assert parts["port"] == 6379
    assert parts["graph"] == "trace_prod"


def test_open_graph_falkordbs_fallback(monkeypatch):
    # When FalkorDB is not reachable, fallback to MemoryStore works seamlessly
    store = open_graph("falkordbs://some-unreachable-cloud:9999/prod", allow_fallback=True)
    assert store.name == "memory"
