import os

import httpx
import pytest
import respx

from jira_mcp.adf import adf_to_text, text_to_adf
from jira_mcp.client import JiraClient, JiraConfigError, JiraError


# ---------------------------------------------------------------- ADF


def test_adf_to_text_flattens_paragraphs():
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "First line."}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Second."}]},
        ],
    }
    assert adf_to_text(doc) == "First line.\nSecond.\n"


def test_adf_to_text_handles_none_and_mentions():
    assert adf_to_text(None) == ""
    doc = {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": "cc "},
            {"type": "mention", "attrs": {"text": "@lead"}},
        ],
    }
    assert adf_to_text(doc) == "cc @lead\n"


def test_text_to_adf_round_trips():
    adf = text_to_adf("hello\nworld")
    assert adf["type"] == "doc"
    assert len(adf["content"]) == 2
    assert adf_to_text(adf) == "hello\nworld\n"


# ------------------------------------------------------------- client


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "me@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")


def test_missing_env_raises_config_error(monkeypatch):
    for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(JiraConfigError):
        JiraClient()


@respx.mock
def test_401_becomes_readable_error(env):
    respx.get("https://example.atlassian.net/rest/api/3/issue/PROJ-1").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(JiraError) as exc:
        JiraClient().get("/issue/PROJ-1")
    assert "credentials" in str(exc.value)


@respx.mock
def test_retries_on_429_then_succeeds(env, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)  # no real waiting in tests
    route = respx.get("https://example.atlassian.net/rest/api/3/issue/PROJ-1")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"key": "PROJ-1"}),
    ]
    data = JiraClient().get("/issue/PROJ-1")
    assert data == {"key": "PROJ-1"}
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_max_attempts(env, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    route = respx.get("https://example.atlassian.net/rest/api/3/issue/PROJ-1").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(JiraError) as exc:
        JiraClient().get("/issue/PROJ-1")
    assert "Gave up" in str(exc.value)
    assert route.call_count == JiraClient.MAX_ATTEMPTS


@respx.mock
def test_jira_error_body_is_flattened(env):
    respx.post("https://example.atlassian.net/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            400,
            json={"errorMessages": ["Error in the JQL Query: Expecting operator at position 8."]},
        )
    )
    with pytest.raises(JiraError) as exc:
        JiraClient().post("/search/jql", json={"jql": "project PROJ"})
    assert "position 8" in str(exc.value)
