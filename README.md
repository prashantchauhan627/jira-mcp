# jira-mcp

A small, read-first [Model Context Protocol](https://modelcontextprotocol.io) server for Jira Cloud, in Python.

It gives an AI client (Claude Code, Claude Desktop, Cursor, or anything that speaks MCP) a scoped set of Jira tools: fetch an issue with its full comment thread, search with JQL, list workflow transitions — and, only when explicitly enabled, add comments and change status.

## Why this exists

Most of the time an agent needs from Jira is *context*: what does this ticket actually say, including the constraint buried three comments down? This server makes that a single tool call, so the agent works from the real ticket rather than a pasted summary.

It is deliberately opinionated about three things:

1. **Descriptions are the API.** The model only sees tool names, descriptions, and parameter schemas. Every description says what the tool does, when to use it, and what it does *not* return. That is what drives correct tool selection.
2. **Errors are readable.** Every failure raises with a sentence the model can act on — invalid credentials, unknown key, JQL parse error with position. Nothing returns an empty result on failure, because that is the exact condition under which agents confabulate.
3. **Reads and writes are scoped differently.** The server is read-only by default. Write tools are not registered at all unless `JIRA_ALLOW_WRITES=true`, so a read-only server cannot mutate anything regardless of what the model is asked to do.

## Tools

| Tool | Mode | What it does |
|---|---|---|
| `get_issue(issue_key)` | read | Full issue: summary, description, status, assignee, labels, and all comments |
| `search_issues(jql, max_results=10)` | read | JQL search returning compact `{key, summary, status, assignee}` rows |
| `list_transitions(issue_key)` | read | Available workflow transitions with ids |
| `add_comment(issue_key, body)` | **write** | Post a plain-text comment |
| `transition_issue(issue_key, transition_id)` | **write** | Change status using an id from `list_transitions` |

Plus one resource, `jira://issue/{key}`, returning summary and description as text.

## Setup

```bash
git clone <this repo> && cd jira-mcp
pip install -e ".[dev]"
cp .env.example .env    # then fill in your values
```

You need a Jira Cloud API token from https://id.atlassian.com/manage-profile/security/api-tokens.

Required environment variables:

```
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=...
JIRA_ALLOW_WRITES=false     # optional; default read-only
```

## Try it before connecting a client

The MCP Inspector gives you a browser UI to list tools, call them by hand, and see the raw JSON-RPC:

```bash
npx @modelcontextprotocol/inspector python -m jira_mcp.server
```

## Connect to Claude Code

```bash
claude mcp add jira -e JIRA_BASE_URL=https://your-site.atlassian.net \
                    -e JIRA_EMAIL=you@example.com \
                    -e JIRA_API_TOKEN=... \
                    -- python -m jira_mcp.server
```

Then: *"What does PROJ-142 say, including the comments?"*

## Connect to Claude Desktop

Add to `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "jira": {
      "command": "python",
      "args": ["-m", "jira_mcp.server"],
      "cwd": "/absolute/path/to/jira-mcp",
      "env": {
        "JIRA_BASE_URL": "https://your-site.atlassian.net",
        "JIRA_EMAIL": "you@example.com",
        "JIRA_API_TOKEN": "..."
      }
    }
  }
}
```

## How it works

```
Claude ──stdio (JSON-RPC)──▶ jira_mcp/server.py ──HTTPS──▶ Jira Cloud REST v3
                              │
                              ├─ client.py   auth, retry/backoff, readable errors
                              └─ adf.py      Atlassian Document Format ⇄ plain text
```

- **Transport:** stdio. The client launches the server as a subprocess and speaks JSON-RPC over stdin/stdout.
- **Schemas:** generated from Python type hints by the SDK. Pydantic models define the result shapes.
- **Retry:** up to 3 attempts on 429 and 5xx, honouring `Retry-After`, then a clear "gave up" error. Small on purpose — an agent looping on a failing call is worse than an error it can reason about.
- **ADF:** Jira v3 returns rich text as a JSON tree. `adf.py` flattens it on read and builds minimal ADF on write.

## Tests

```bash
pytest
```

Covers ADF conversion, credential errors, 401/403/404 mapping, retry on 429, exhaustion on repeated 5xx, and JQL error flattening. No network — `respx` mocks the HTTP layer.

## Design notes

**Why the model gets the comment thread, not just the description.** In practice the description is a summary and the comments are where decisions and constraints live. An agent working from the description alone misses them.

**Why write tools are conditionally registered rather than gated at call time.** If a tool isn't in `tools/list`, the model can't be talked into calling it. Registration-time scoping is a stronger boundary than a runtime check.

**Why ticket content is treated as data.** The server's `instructions` tell the client to reason over ticket text, not follow it. A work-tracking system connected to an agent is a prompt-injection surface if retrieved text is treated as trusted.

## License

MIT
