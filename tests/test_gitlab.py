"""Tests for GitLab CI/CD integration, report generators, and GitLab Duo adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from trace_repro.models import Incident, Interference
from trace_repro.pipeline.gitlab import (
    GitLabCIAdapter,
    GitLabClient,
    GitLabEnv,
    gitlab_section_end,
    gitlab_section_start,
    is_in_gitlab_ci,
)
from trace_repro.pipeline.heal import HealResult, PipelineRun
from trace_repro.repair.patch import RepairResult


def test_gitlab_section_formatting():
    start = gitlab_section_start("stage_1", "Stage 1: Plan", collapsed=True)
    assert "\033[0Ksection_start:" in start
    assert ":stage_1[collapsed=true]\r\033[0K" in start
    assert "Stage 1: Plan" in start

    end = gitlab_section_end("stage_1")
    assert "\033[0Ksection_end:" in end
    assert ":stage_1\r\033[0K" in end


def test_is_in_gitlab_ci_detection(monkeypatch):
    monkeypatch.delenv("GITLAB_CI", raising=False)
    assert not is_in_gitlab_ci()

    monkeypatch.setenv("GITLAB_CI", "true")
    assert is_in_gitlab_ci()

    monkeypatch.setenv("GITLAB_CI", "1")
    assert is_in_gitlab_ci()


def test_gitlab_env_from_environ(monkeypatch):
    monkeypatch.setenv("GITLAB_CI", "true")
    monkeypatch.setenv("CI_PROJECT_ID", "4242")
    monkeypatch.setenv("CI_PIPELINE_ID", "999")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test-secret")

    env = GitLabEnv.from_environ()
    assert env.is_ci
    assert env.project_id == "4242"
    assert env.pipeline_id == "999"
    assert env.token == "glpat-test-secret"


def test_generate_junit_xml(tmp_path: Path):
    inc = Incident(
        incident_id="incident_8812",
        crash_trace_id="tr_crash",
        crash_span_id="sp_crash",
        crash_function="capture_payment",
        crash_file="checkout.py",
        crash_endpoint="POST /checkout/capture",
        entity_key="orders:8812",
        entity_type="orders",
        primary_key="8812",
        upstream_mutator="apply_webhook",
    )
    red = PipelineRun(
        name="pipeline#1",
        exit_code=1,
        duration_s=1.2,
        stdout="AssertionError: reproduced",
        stderr="",
    )
    green = PipelineRun(
        name="pipeline#2",
        exit_code=0,
        duration_s=1.4,
        stdout="2 passed in 1.4s",
        stderr="",
    )
    result = HealResult(incident=inc, compiled=None, red=red, green=green)

    adapter = GitLabCIAdapter()
    xml_file = tmp_path / "junit.xml"
    xml_str = adapter.generate_junit_xml(result, xml_file)

    assert xml_file.exists()
    assert 'testsuites name="trace-repro-gitlab"' in xml_str
    assert 'name="pipeline_1_deterministic_reproduction"' in xml_str
    assert 'name="pipeline_2_self_healing_verification"' in xml_str
    assert 'failures="0"' in xml_str  # because reproduction succeeded (red failed) and green passed!


def test_generate_code_quality_report(tmp_path: Path):
    inc = Incident(
        incident_id="incident_8812",
        crash_trace_id="tr_crash",
        crash_span_id="sp_crash",
        crash_function="capture_payment",
        crash_file="checkout.py",
        crash_endpoint="POST /checkout/capture",
        entity_key="orders:8812",
        entity_type="orders",
        primary_key="8812",
        upstream_mutator="apply_webhook",
        interferences=[
            Interference(
                entity_key="orders:8812",
                entity_type="orders",
                primary_key="8812",
                other_trace_id="tr_1",
                other_span_id="sp_1",
                other_endpoint="POST /webhooks",
                other_payload="{}",
                mutator_function="apply_webhook",
                mutator_file="webhooks.py",
                crash_function="capture_payment",
                crash_file="checkout.py",
                write_ts_ns=1000,
                read_ts_ns=2000,
                lock_held=False,
                hazard="WAW",
            )
        ],
    )
    result = HealResult(incident=inc, compiled=None)
    adapter = GitLabCIAdapter()
    out_file = tmp_path / "gl-code-quality-report.json"
    items = adapter.generate_code_quality_report(result, out_file)

    assert out_file.exists()
    assert len(items) == 1
    assert items[0]["check_name"] == "concurrency/waw-unlocked-write"
    assert items[0]["severity"] == "critical"
    assert "apply_webhook" in items[0]["description"]
    assert items[0]["location"]["path"] == "webhooks.py"


def test_generate_mr_description():
    inc = Incident(
        incident_id="incident_8812",
        crash_trace_id="tr_crash",
        crash_span_id="sp_crash",
        crash_function="capture_payment",
        crash_file="checkout.py",
        crash_endpoint="POST /checkout/capture",
        entity_key="orders:8812",
        entity_type="orders",
        primary_key="8812",
        upstream_mutator="apply_webhook",
    )
    repair = RepairResult(
        incident_id="incident_8812",
        plans=[],
        diff="--- a/webhooks.py\n+++ b/webhooks.py\n@@ -1 +1 @@\n-unlocked\n+locked\n",
        files_changed=["webhooks.py"],
        summary="repaired 1 file",
        applied=True,
    )
    result = HealResult(
        incident=inc,
        compiled=None,
        repair=repair,
        graph_diff={"lock_held_before": [False], "lock_held_after": [True], "resolved_writers": ["apply_webhook"]},
    )
    adapter = GitLabCIAdapter()
    desc = adapter.generate_mr_description(result)

    assert "Autonomous Self-Healing" in desc
    assert "orders:8812" in desc
    assert "apply_webhook" in desc
    assert "FalkorDB Graph Governance Proof" in desc
    assert "+locked" in desc


def test_gitlab_client_request(monkeypatch):
    client = GitLabClient(token="secret-token", api_url="https://gitlab.example.com/api/v4", project_id="123")

    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps({"id": 1, "iid": 10, "web_url": "https://gitlab.example.com/mr/10"}).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp

    with patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        mr = client.create_merge_request("heal/inc", "main", "Fix race", "Desc")
        assert mr["iid"] == 10
        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Private-token") == "secret-token"
        assert req.get_method() == "POST"
        assert "projects/123/merge_requests" in req.full_url
