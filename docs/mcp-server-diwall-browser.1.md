% MCP-SERVER-DIWALL-BROWSER(1) mcp-server-diwall-browser | User Commands
% Ronan Davalan
% 2026-10-18

# NAME

mcp-server-diwall-browser - MCP server that gives a language model the eyes and hands of an operator in the operator's own browser

# SYNOPSIS

**mcp-server-diwall-browser**

**mcp-server-diwall-browser-credential-import** **\--source** *FILE* **\--name** *NAME* [**\--origin** *ORIGIN*...]

**mcp-server-diwall-browser-credential-import** **\--source-dir** *DIR*

# DESCRIPTION

**mcp-server-diwall-browser** is a Model Context Protocol (MCP) server. It attaches to a
browser of the Chromium family (Google Chrome, Chromium or Brave) that the operator has
started, through the Chrome DevTools Protocol, and gives the language model that the MCP
client runs the same screenshot and the same accessibility tree as the operator, and the
means to act in the same signed-in session.

The program takes no option. An MCP client starts it as a child process and talks to it on its
standard input and output; it opens no listening port and ends when the client closes its
input. It connects only to the debugging port of the browser, on the loopback interface. It
never starts or closes the browser, and never mounts or unmounts the encrypted directory.

The browser must run on a profile of its own, never on the usual profile of the operator:

    google-chrome --user-data-dir=DIR --remote-debugging-port=9222

The server refuses a default profile (`profile_dir_is_default`) and a debugging port that is
reachable beyond the loopback interface (`debug_endpoint_not_loopback`). Credentials, captures,
downloads and the audit journal are kept in an encrypted directory (gocryptfs) that the
operator opens; while it is closed, the server writes nothing.

Installing the program does not configure it. Write the operator profile first (see
**OPERATOR PROFILE**), then connect the server to an MCP client; with Claude Code:

    claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser

The installation, the browser, the encrypted directory and every refusal code are described in
the operator guide (see **FILES**).

# TOOLS

The server offers fourteen tools to the model.

**status**
:   What is configured, open, attached and allowed, and what blocks. It works with nothing configured.

**list_tabs**, **open_tab**, **close_tab**
:   The tabs of this server. The server works only in the tabs it opened, never in a tab of the operator.

**navigate**
:   Load an http or https address in one of its tabs.

**observe**
:   Return the image with numbered marks, the accessibility tree and the list of elements. The marks hold for that observation only.

**click**, **fill**, **select_option**, **press_key**, **scroll**, **handle_dialog**
:   Act on an element named by its mark. A sensitive field is filled only from a credential file (**fill** with *credential_ref*), never from a text.

**wait**
:   Wait for the load of the page, a text or an element.

**save_evidence**
:   Keep a capture in the encrypted directory.

# OPERATOR PROFILE

The server reads the operator profile on every call, so a change takes effect without a
restart. It is a JSON file found at `$XDG_CONFIG_HOME/mcp-server-diwall-browser/profile.json`,
or `~/.config/mcp-server-diwall-browser/profile.json` when `XDG_CONFIG_HOME` is not set, or at
the absolute path named by the variable `MCP_DIWALL_BROWSER_PROFILE`. The file is in mode 600
and its directory in mode 700; the server refuses it otherwise (`profile_invalid`). The file
holds no secret.

`schema_version`
:   Always `1`.

`secrets_dir`
:   Where the encrypted directory is open (absolute path).

`secrets_crypt_dir`
:   The encrypted directory on disk (optional).

`allowed_origins`
:   The sites the server reads and acts on: origins, or subdomain wildcards such as `https://*.example.org`. Of any other page, only the origin is returned.

`confirm_each_change`
:   Origins of `allowed_origins` where every change asks for a confirmation.

`local_model_origins`, `declarations`
:   Statements of the operator, shown by **status** and used for nothing else.

`browser`
:   `family` (`chrome`, `chromium` or `brave`), `profile_dir` (the `--user-data-dir` of the dedicated profile) and `debug_port` (from 1024 to 65535, 9222 by default).

`limits`
:   `min_action_delay_ms` (250 by default), `max_actions_per_session` (200) and `max_pages_per_session` (50).

A key that would configure the locked actions (payment, deletion) is refused: only the code
decides what is locked. A complete example is in the operator guide.

# CONFIRMATION

Before a payment, an irreversible deletion or a change of secret, and before every change on an
origin of `confirm_each_change`, the server opens a window titled "Confirmation du serveur MCP"
with the buttons "Autoriser" and "Refuser", by **kdialog** or **zenity**. The window waits 90
seconds, then counts as no answer; one answer covers one action. It needs a display in the
environment of the server (`DISPLAY` or `WAYLAND_DISPLAY`); **status** reports the channel in
`confirmation_channel`.

# CREDENTIAL IMPORT

**mcp-server-diwall-browser-credential-import** converts a credential file of another format,
kept in the open encrypted directory, into a file the server reads, inside its own process and
without showing any value. Run it yourself, in your shell, with the encrypted directory open.

**\--source** *FILE*
:   Absolute path of the old file, under the encrypted directory. Required with **\--name** unless **\--source-dir** is given.

**\--name** *NAME*
:   Label of the new file, which becomes `NAME.json` in the `credentials/` directory.

**\--origin** *ORIGIN*
:   A full origin (`https://host`, with a port if it is not the default one). May be repeated. Without it, each host of the old file becomes `https://host`.

**\--source-dir** *DIR*
:   Absolute path of a directory of old files; each file ending in `.json` is converted in turn. Excludes the three options above.

It prints one line of JSON per file on its standard output. Only the user name and the password
are carried over; a second factor is not. It overwrites nothing and deletes nothing.

# ENVIRONMENT

`MCP_DIWALL_BROWSER_PROFILE`
:   Absolute path of the operator profile, in place of the standard place.

`XDG_CONFIG_HOME`
:   Base of the standard place of the operator profile.

`DISPLAY`, `WAYLAND_DISPLAY`
:   The display on which the confirmation window opens.

# FILES

`/usr/bin/mcp-server-diwall-browser`, `/usr/bin/mcp-server-diwall-browser-credential-import`
:   The two commands.

`/usr/lib/mcp-server-diwall-browser`
:   The program and its Python virtual environment, created by the package at installation, which needs a network connection for that step.

`/usr/share/doc/mcp-server-diwall-browser/OPERATOR_GUIDE.md`
:   The operator guide: installation, browser, encrypted directory, profile, credentials and refusal codes.

`~/.config/mcp-server-diwall-browser/profile.json`
:   The operator profile.

*secrets_dir*`/mcp-server-diwall-browser/`
:   Written by the server in the open encrypted directory only: `credentials/`, `browser-profile/`, `journal/operations.jsonl`, `evidence/`, `downloads/` and `run/tabs.json`.

# EXIT STATUS

**mcp-server-diwall-browser** exits with 0 when the MCP client closes its input. A refusal of a
tool is a result returned to the client, with a code, not an exit status.

**mcp-server-diwall-browser-credential-import** exits with 0 when every file is carried over and
with 1 otherwise, after printing one line `error: CODE` on its error output.

# SEE ALSO

**gocryptfs**(1), **fusermount3**(1), **kdialog**(1), **zenity**(1)

Project: <https://github.com/RonanDavalan/mcp-server-diwall-browser> and
<https://mcp.diwall.davalan.fr>

# AUTHOR

Ronan Davalan. The other contributors are listed in `CONTRIBUTORS.md`.
