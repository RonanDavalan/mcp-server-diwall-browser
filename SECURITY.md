# Security Policy

mcp-server-diwall-browser acts in a browser where its operator is signed in, and handles
credentials kept in an encrypted directory. A vulnerability here has a real cost for whoever runs
it: this policy exists so that it is reported before it is exploited.

## Supported versions

Only the latest published release is supported. A fix lands in the next release, not as a backport
to an older tag.

## Reporting a vulnerability

**Do not open a public GitHub issue.** A public issue discloses the vulnerability before a fix
exists, exposing everyone who runs the server.

Report privately through GitHub Security Advisories:
<https://github.com/RonanDavalan/mcp-server-diwall-browser/security/advisories/new>

If that path is unavailable to you, open a regular issue that asks for another private contact,
without any detail of the vulnerability.

What counts as a vulnerability here includes a page whose content makes the server act, a secret
that reaches a tool result, a log or a capture, a way around the list of allowed sites or around
the confirmation of a payment, a deletion or a change of secret, and a debugging port that the
server accepts although it is reachable beyond the loopback interface.

## What to expect

- An acknowledgement within **72 hours**.
- A coordinated disclosure made in good faith is never met with legal action.
- A fix ships as a normal release once checked. The report stays private until then, and you are
  credited in the release notes if you wish.

## Scope

This policy covers the code of this repository, `src/mcp_server_diwall_browser/` and its tests. It
does not cover:

- unofficial forks or modified copies;
- versions older than the current release (see "Supported versions");
- the browser, the MCP client, the language model and its provider, or gocryptfs, which are
  separate programs with their own policies;
- the project's website, a separate codebase.
