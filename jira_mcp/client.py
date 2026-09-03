"""
Thin HTTP client for Jira Cloud REST API v3.

Responsibilities:
  - Basic auth using email + API token (Jira Cloud standard)
  - Retry on 429 (rate limit) and 5xx with backoff, honouring Retry-After
  - Turn HTTP failures into readable JiraError messages the model can act on

Everything here is deliberately boring. The interesting part of an MCP
server is the tool descriptions and error surfaces, not the transport.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx


class JiraError(Exception):
    """Raised for any Jira API failure. Message is written for an LLM to read."""


class JiraConfigError(JiraError):
    """Raised when required environment variables are missing."""


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise JiraConfigError(
            f"Missing environment variable {name}. "
            "Set JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN before starting the server."
        )
    return value


class JiraClient:
    """HTTP client for Jira Cloud REST API v3 with retry/backoff and readable errors."""
    # Retry budget. Small on purpose — an agent looping on a failing call
    # is worse than a clear error it can reason about.
    MAX_ATTEMPTS = 3
    BASE_BACKOFF_SECONDS = 1.0
    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(self) -> None:
        base = _env("JIRA_BASE_URL").rstrip("/")
        email = _env("JIRA_EMAIL")
        token = _env("JIRA_API_TOKEN")

        self._http = httpx.Client(
            base_url=f"{base}/rest/api/3",
            auth=(email, token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=httpx.Timeout(20.0),
        )

    # ------------------------------------------------------------------ core

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a request with retry/backoff. Returns parsed JSON or None."""
        last_error: str = ""

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                resp = self._http.request(method, path, **kwargs)
            except httpx.TimeoutException:
                last_error = f"Request to {path} timed out."
                self._sleep(attempt, None)
                continue
            except httpx.HTTPError as exc:
                raise JiraError(f"Network error calling Jira: {exc}") from exc

            if resp.status_code in self.RETRYABLE_STATUS:
                last_error = f"Jira returned {resp.status_code} for {method} {path}."
                if attempt < self.MAX_ATTEMPTS:
                    self._sleep(attempt, resp.headers.get("Retry-After"))
                    continue
                break  # exhausted — fall through to the gave-up error below

            if resp.status_code == 401:
                raise JiraError(
                    "Jira rejected the credentials (401). "
                    "Check JIRA_EMAIL and JIRA_API_TOKEN, and that the token has not expired."
                )
            if resp.status_code == 403:
                raise JiraError(
                    f"Permission denied (403) for {method} {path}. "
                    "The account can authenticate but is not allowed to perform this action."
                )
            if resp.status_code == 404:
                raise JiraError(f"Not found (404): {path}. Check the issue key or project.")
            if resp.status_code >= 400:
                raise JiraError(self._format_jira_error(resp, method, path))

            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

        raise JiraError(f"Gave up after {self.MAX_ATTEMPTS} attempts. Last error: {last_error}")

    def _sleep(self, attempt: int, retry_after: str | None) -> None:
        """Honour Retry-After if Jira sent one, otherwise exponential backoff."""
        if retry_after and retry_after.isdigit():
            delay = float(retry_after)
        else:
            delay = self.BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
        time.sleep(min(delay, 30.0))

    @staticmethod
    def _format_jira_error(resp: httpx.Response, method: str, path: str) -> str:
        """Jira returns errors in a few shapes; flatten them into one sentence."""
        try:
            body = resp.json()
        except ValueError:
            return f"Jira returned {resp.status_code} for {method} {path}: {resp.text[:300]}"

        messages: list[str] = []
        if isinstance(body, dict):
            messages.extend(body.get("errorMessages", []))
            errors = body.get("errors", {})
            if isinstance(errors, dict):
                messages.extend(f"{k}: {v}" for k, v in errors.items())
        detail = "; ".join(messages) if messages else str(body)[:300]
        return f"Jira returned {resp.status_code} for {method} {path}: {detail}"

    # -------------------------------------------------------------- helpers

    def get(self, path: str, **kwargs: Any) -> Any:
        """Issue a GET request with retry/backoff."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        """Issue a POST request with retry/backoff."""
        return self.request("POST", path, **kwargs)

    def close(self) -> None:
        """Close the HTTP connection."""
        self._http.close()
