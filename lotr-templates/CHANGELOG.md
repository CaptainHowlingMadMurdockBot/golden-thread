# LOTR template catalog: changelog

The catalog is released on its own schedule, separately from gt. Each version lists what a
`lotr template update` will change. Needs gt-lotr 0.5.0 or newer.

## Catalog 1 -- 2026-10-14

First catalog. Sixteen templates, all MCP servers, each with a fact sheet that gives the source
page and the verification state of every fact. None is independently verified yet; read `open` in
each sheet.

- linear, sentry, notion, gitlab: hosted MCP servers with browser sign-in (mcp-oauth).
- atlassian: the Atlassian Rovo MCP server (Confluence and Jira), browser sign-in (mcp-oauth).
  Transitions and comments are writes; four lookups are reads.
- pagerduty, google-drive, slack, m365: hosted servers that need an OAuth app you create first
  (mcp-oauth-preregistered). google-drive asks for offline access so it keeps a refresh token;
  slack needs PKCE turned on in your Slack app; m365 is Microsoft Work IQ and needs a tenant admin.
- github: GitHub's hosted MCP server with a token (mcp-http-key).
- playwright: Microsoft's @playwright/mcp, run with npx (stdio-npx).
- filesystem, git, fetch: the MCP project's reference servers (stdio; filesystem and git ask for a
  folder or repository, fetch asks for confirmation on every fetch).
- sqlite (mcp-sqlite) and postgres (Google's MCP Toolbox): local database servers. Arbitrary SQL
  asks first.
