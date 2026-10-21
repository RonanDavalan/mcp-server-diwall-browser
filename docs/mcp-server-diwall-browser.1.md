% MCP-SERVER-DIWALL-BROWSER(1) mcp-server-diwall-browser | User Commands
% Ronan Davalan
% 2026-10-18

# NAME

mcp-server-diwall-browser - MCP server that gives a language model the eyes and hands of an operator in the operator's own browser

# SYNOPSIS

**mcp-server-diwall-browser**

**mcp-server-diwall-browser-credential-import** **\--source** *FILE* **\--name** *NAME* [**\--origin** *ORIGIN*...]

**mcp-server-diwall-browser-credential-import** **\--source-dir** *DIR*

**mcp-server-diwall-browser-setup** [**check** [**\--json**]]

**mcp-server-diwall-browser-setup** **create** [**\--temporary** | **\--crypt-dir** *DIR* **\--mount-dir** *DIR*] [**\--browser** *FAMILY*]

**mcp-server-diwall-browser-setup** {**open** | **browser** | **close**}

# DESCRIPTION

## Before first use

The server does not work without an encrypted directory: the profile of the browser it attaches to keeps the
signed-in sessions of the operator, and they must not sit on the disk in clear. There is no mode without
encryption. After installing the package, the first command, run by the operator in a terminal, is
**mcp-server-diwall-browser-setup**: with no argument it reads the machine, changes nothing and ends on the next
command to type (see **SETUP ASSISTANT**).

**mcp-server-diwall-browser-setup create \--temporary** makes a trial volume in memory, destroyed by **close**, by
the stop of the machine or by the end of the session; the signed-in sessions of the sites, the audit journal and
the evidence are lost with it. **mcp-server-diwall-browser-setup create** makes a durable volume. The operator types
the password at the prompt of **gocryptfs**(1), which the assistant never reads, and signs in to the sites in the
browser that **mcp-server-diwall-browser-setup browser** starts. After adding the server to an MCP client, restart
the client: a session that was already open does not see the new server.

## The server

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

Installing the program does not configure it. **mcp-server-diwall-browser-setup create** writes the operator
profile (see *Before first use* and **OPERATOR PROFILE**); then connect the server to an MCP client; with
Claude Code:

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
:   Act on an element named by its mark. A sensitive field is filled only from a credential file (**fill** with *credential_ref*), never from a text, and a password enters only a sensitive field. A click, a key and a text are cancelled if the page changes the target at the last instant. Accepting a **confirm** or **prompt** dialog asks the operator for confirmation.

**wait**
:   Wait for the load of the page, a text or an element.

**save_evidence**
:   Keep a capture in the encrypted directory.

# SETUP ASSISTANT

**mcp-server-diwall-browser-setup** is a program of its own, run by the operator in a terminal. The server never
mounts, unmounts or creates anything; no module of the server imports the assistant. The assistant hands the
terminal to **gocryptfs**(1) and never reads, keeps or passes on the password. Its messages are in French, its codes
in English. With no sub-command it runs **check**.

**check** [**\--json**]
:   Reads the machine and changes nothing; it needs neither a terminal nor a password. Reports the mechanism
    (**gocryptfs** on Linux), **fusermount3**, `/dev/fuse`, the install command of the package manager (`apt`, `dnf`,
    `zypper` or `pacman`) when something is missing, the closed **gocryptfs** volumes found in the usual places, the
    mounted volumes of another kind (LUKS, eCryptfs, EncFS, CryFS), which the server does not support, the operator
    profile and the state of its volume, the browsers found, the swap area (its encryption is not determined), the
    terminal and the screen, and ends on the next command. **\--json** prints the same report on one line.

**create** [**\--temporary**]
:   Makes a volume, the directories of the server in mode 700 and the operator profile, written only once the volume
    is seen mounted, with no site allowed. **\--temporary** makes the volume under `$XDG_RUNTIME_DIR`, which must be
    memory, and refuses otherwise. Without it the volume is `~/Vaults/Main.enc`, opened on `~/Vaults/Main`.

**create** **\--crypt-dir** *DIR* **\--mount-dir** *DIR* [**\--browser** *FAMILY*]
:   The same, for other absolute paths. A *DIR* that already holds a **gocryptfs** volume is opened, not initialised.
    *FAMILY* is `chrome`, `chromium` or `brave`; by default the first one found. A profile whose volume is open,
    closed but present or unreadable is never replaced; the volume paths of a profile whose volume is gone or is not
    **gocryptfs** are replaced, and its sites kept.

**open**
:   Opens the volume of the operator profile.

**browser**
:   Starts the browser on the profile kept in the open volume, detached from the terminal, with
    `--user-data-dir` and `--remote-debugging-port`. It refuses unless the volume is open.

**close**
:   Stops the browser, waits for it to end, then unmounts with `fusermount3 -u`, never lazily. A trial volume is
    destroyed.

**create**, **open**, **browser** and **close** refuse with `no_terminal` when they have no terminal. A refusal is one
line, `refus CODE : message`, on the error output; the codes are in the operator guide.

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
:   The sites the server reads and acts on: origins, or subdomain wildcards such as `https://*.example.invalid`. Under the `strict` policy, of any other page only the origin is returned.

`origin_policy`
:   `strict` (the default), `discovery` (the operator answers a window for each origin the list does not hold: `Refuser`, `Cette séance` or `Ajouter à la liste`) or `open` (every public site; local addresses and names stay on the list). Any other value is refused.

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
with the answers "Refuser" (the default) and "Autoriser", by **kdialog** or **zenity**. The window waits 90
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

`XDG_RUNTIME_DIR`
:   Where the setup assistant keeps a trial volume: memory, destroyed at the stop of the machine.

`XDG_CONFIG_HOME`
:   Base of the standard place of the operator profile.

`DISPLAY`, `WAYLAND_DISPLAY`
:   The display on which the confirmation window opens.

# PACKAGES

The server ships as native packages, installed from the folder that holds the downloaded file:

Debian 13, Ubuntu 24.04 and 26.04
:   `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb`

Fedora 44
:   `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm`

openSUSE Leap 16.0
:   `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm`

Arch Linux
:   `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst`

Installation, the start of the server by an ordinary account and removal are validated in containers on these systems. A container shows neither the confirmation window, nor the mounting of the encrypted directory, nor a browser. Debian 12 and Ubuntu 22.04 are not supported: their Python is older than 3.12.

# FILES

`/usr/bin/mcp-server-diwall-browser`, `/usr/bin/mcp-server-diwall-browser-credential-import`, `/usr/bin/mcp-server-diwall-browser-setup`
:   The three commands.

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

**mcp-server-diwall-browser-setup** exits with 0 when the command is done, with 1 after a refusal, which it prints as
one line `refus CODE : message` on its error output, and with 2 when its command line is not understood.

# SEE ALSO

**gocryptfs**(1), **fusermount3**(1), **kdialog**(1), **zenity**(1)

Project: <https://github.com/RonanDavalan/mcp-server-diwall-browser> and
<https://mcp-diwall.davalan.fr>

# AUTHOR

Ronan Davalan. The other contributors are listed in `CONTRIBUTORS.md`.
