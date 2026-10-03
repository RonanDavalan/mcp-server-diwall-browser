# Operator guide

How to install the server, prepare the browser and the encrypted directory, write the operator
profile, and answer each refusal. Every command uses the same example values: the user `alice`,
the repository cloned at `~/mcp-server-diwall-browser`, the encrypted directory kept on
disk at `~/Vaults/Main.enc` and opened at `~/Vaults/Main`, and sites under
`example.invalid`. Replace them with yours; each section says which values are yours to choose.

## The final layout

```
~/
├── mcp-server-diwall-browser/                     the repository; the server runs from here
│   └── .venv/                                     its virtual environment; the MCP client starts .venv/bin/python
├── .config/mcp-server-diwall-browser/             mode 700
│   └── profile.json                               mode 600: the operator profile (no secret in it)
└── Vaults/
    ├── Main.enc/                                  the encrypted directory on disk (gocryptfs)
    └── Main/                                      where the operator opens it; empty while it is closed
        └── mcp-server-diwall-browser/             mode 700, created once by the operator
            ├── credentials/                       mode 700: credential files (mode 600), written by the operator
            ├── browser-profile/chrome/            the dedicated browser profile
            ├── journal/operations.jsonl           the audit journal, one line per tool call
            ├── evidence/<YYYY-MM-DD>/<operation_id>/   captures taken around a locked action
            └── downloads/<operation_id>/          files that pages downloaded
```

The role of each component:

- The browser (Chrome, Chromium or Brave) is started by the operator, on the dedicated profile,
  with a debugging port on the loopback interface. The server attaches to it; it never starts or
  closes it.
- The MCP client (for example Claude Code) starts the server as a child process and talks to it
  over standard input and output. The language model works through the client.
- The server reads the operator profile on every call, attaches to the browser, and writes its
  journal, evidence and downloads in the encrypted directory only.
- gocryptfs keeps the encrypted directory. The operator opens and closes it; the server never
  does.
- `kdialog` or `zenity` shows the confirmation window on the operator's screen.

The server creates `journal/`, `evidence/` and `downloads/` itself, in mode 700, at its first
write, and only while the encrypted directory is open.

## 1. What you need

- Linux. This guide is written for Debian; the project's tests ran on Ubuntu 24.04 (see below).
- Python 3.12 or later.
- A browser of the Chromium family: Google Chrome, Chromium or Brave. The specification of the
  project asks for version 154 or later. Chrome 136 and later ignore the debugging port on the
  browser's default profile, which is one reason the server needs a dedicated profile (this
  behaviour was not measured by the project; the server refuses a default profile in any case).
  The tests ran on Chromium 141 in the project's environment and on Chrome 154 on the operator's
  machine. Brave was not tried.
- `fuse3` and `gocryptfs` for the encrypted directory.
- `kdialog` or `zenity` for the confirmation window.
- `git`, and `ss` (package `iproute2`) for the loopback check.

The package names were checked with `apt-cache policy` on Ubuntu 24.04, the only system available
to this project; they were not checked on Debian:

```
$ apt-cache policy python3-venv fuse3 gocryptfs kdialog zenity iproute2 git | grep -E "^[a-z]|Candidate"
python3-venv:
  Candidate: 3.12.3-0ubuntu2.1
fuse3:
  Candidate: 3.14.0-5build1
gocryptfs:
  Candidate: 2.4.0-1ubuntu0.24.04.3
kdialog:
  Candidate: 4:23.08.5-0ubuntu4
zenity:
  Candidate: 4.0.1-1build3
iproute2:
  Candidate: 6.1.0-1ubuntu6.4
git:
  Candidate: 1:2.43.0-1ubuntu7.3
```

`apt-cache policy chromium` showed no candidate there, so the browser's package name is not
given here. The installation command, not run by this project (its environment cannot install
system packages):

```
sudo apt install python3-venv fuse3 gocryptfs kdialog iproute2 git
```

Use `zenity` in place of `kdialog` outside KDE if you prefer.

## 2. Installation

Clone the repository. Replace the address with the one of your copy of the repository:

```
git clone https://git.example.invalid/alice/mcp-server-diwall-browser.git ~/mcp-server-diwall-browser
cd ~/mcp-server-diwall-browser
```

Check the Python version:

```
python3 --version
```

It must print `Python 3.12` or later. If it prints an older version, use an interpreter by its
versioned name in the next command, for example `python3.12` or `python3.13` in place of
`python3`.

Create the virtual environment and install the server with its test tools:

```
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

The versions this project tested are listed in `requirements.lock`.

Check the installation. The first command runs every test that needs no browser; the second
starts the server against a browser of its own, on a blank temporary profile, and observes a
local demo page. Neither touches your browser, your encrypted directory or your operator profile.

```
.venv/bin/python -m pytest -q -m "not browser"
.venv/bin/python scripts/smoke_test.py
```

Output on a fresh clone in the project's environment, with Python 3.12.3 and Chromium 141 (the
counts are those of this revision):

```
1205 passed, 54 deselected, 1 warning in 54.55s
```

The warning is an `IncompleteFieldDefinitionWarning` of `pydantic_settings`, a library the MCP SDK
uses; every test passes with it.

```
browser executable: chromium (Chrome/141.0.7390.37)
server                        0.0.1, configured: True
browser                       Chrome/141.0.7390.37, attached: True
encrypted directory           closed_or_absent (encrypted_dir_undetermined), action expected: operator_opens_directory
tab origin                    http://127.0.0.1:41687
marks                         4
image                         yes
tree lines                    9
redacted_fields               1
password value in the result  absent
elements                      1 textbox "User name", 2 textbox "Password", 3 button "Sign in", 4 link "Help"
result: conforming
```

The port in `tab origin` changes at every run: the demo page is served on a free port of
`127.0.0.1`. The smoke test looks for `google-chrome`, `google-chrome-stable`, `chromium`,
`chromium-browser` and `brave-browser` on your `PATH`, in that order. Without one, it prints
`smoke test: no Chromium-family browser found (looked for: google-chrome, google-chrome-stable, chromium, chromium-browser, brave-browser)`
and returns 3. It returns 0 when the result is as expected, and 1 when it differs (the line that
differs ends with `<- not as expected`). The encrypted directory line reads `closed_or_absent` on purpose: the smoke test gives the
server a directory that is not mounted. `--save-image ~/observe.png` writes the image
the model received.

With a browser on your `PATH`, `.venv/bin/python -m pytest -q` also runs the 54 browser tests;
each starts the browser headless on a blank temporary profile. On the same fresh clone:

```
1259 passed, 1 warning in 91.94s (0:01:31)
```

Then connect the server to your MCP client: the command and the configuration block are in the
`README.md`.

## 3. The dedicated browser profile

The server only attaches to a browser started on a profile of its own, never on your usual
profile. That profile lives in the encrypted directory, so the browser can only be started once the
encrypted directory is open (section 4).

Start the browser with one of these commands, the one of your browser:

```
google-chrome --user-data-dir=~/Vaults/Main/mcp-server-diwall-browser/browser-profile/chrome --remote-debugging-port=9222
chromium --user-data-dir=~/Vaults/Main/mcp-server-diwall-browser/browser-profile/chromium --remote-debugging-port=9222
brave-browser --user-data-dir=~/Vaults/Main/mcp-server-diwall-browser/browser-profile/brave --remote-debugging-port=9222
```

Adapt `~/Vaults/Main` to where you open the encrypted directory. The two values must be
the ones of your operator profile: `browser.profile_dir` is the `--user-data-dir` directory and
`browser.debug_port` is the port. Never add `--remote-debugging-address`: the port would be
reachable from the network, and the server refuses to attach (`debug_endpoint_not_loopback`).

Check that the port listens on the loopback interface only:

```
ss -ltn "sport = :9222"
```

The local address column must show `127.0.0.1:9222` or `[::1]:9222` only. `0.0.0.0:9222`, `*:9222`
or any other address means anyone who reaches the port controls the browser: close the browser and
start it again with the command above. `ss` was not available in the project's environment, so no
real output is shown here; there, Chromium 141 started with `--remote-debugging-port=9222`
listened on `127.0.0.1:9222` only, as read in `/proc/net/tcp`. The server makes the same check, on
`/proc/net/tcp` and `/proc/net/tcp6`, every time it attaches.

Sign in once to each of your sites in this browser, as you would in your usual browser. The server
works in these authenticated sessions; it types a password only from a credential file (section
6).

While the server is attached, this browser refuses every download, in your own tabs too, except in
the short window around one of the server's clicks or Enter keys. To download a file yourself, stop
the server or the MCP client: downloads work again as soon as the server's connection ends.

## 4. The encrypted directory

The server reads and writes only in `~/Vaults/Main/mcp-server-diwall-browser/` (the
layout at the top of this guide). It never mounts or unmounts the encrypted directory, never runs
`gocryptfs` or `fusermount3`, and never knows its password. When the encrypted directory is closed,
the server refuses to write; it has no fallback to an unencrypted place.

The commands below were not run by this project: its environment cannot mount a FUSE volume.

Create the encrypted directory once. `gocryptfs` prints a master key at this step: write it down
on paper or keep it in a password manager, never in a file of this disk.

```
mkdir -m 700 ~/Vaults ~/Vaults/Main.enc ~/Vaults/Main
gocryptfs -init ~/Vaults/Main.enc
```

Open it:

```
gocryptfs ~/Vaults/Main.enc ~/Vaults/Main
```

The first time it is open, create the server's directory, the credentials directory and the
browser profile directory:

```
mkdir -m 700 ~/Vaults/Main/mcp-server-diwall-browser
mkdir -m 700 ~/Vaults/Main/mcp-server-diwall-browser/credentials
mkdir -m 700 ~/Vaults/Main/mcp-server-diwall-browser/browser-profile
```

Close it in this order: first close the dedicated browser, which keeps files open in its profile,
then unmount:

```
fusermount3 -u ~/Vaults/Main
```

Never use `fusermount3 -u -z`: a lazy unmount detaches the directory at once while a process can
still write through the old path, which then leads to the unencrypted directory below.

A desktop tool that opens gocryptfs volumes can be used in place of these commands; this project
has not tried one. Set `secrets_dir` to where it opens the volume and `secrets_crypt_dir` to where
it keeps the encrypted files. The server accepts the mount only if it is a `fuse.gocryptfs` mount
of the server's own user, without the `allow_other` option.

## 5. The operator profile

The server reads the operator profile from `~/.config/mcp-server-diwall-browser/profile.json`
(`$XDG_CONFIG_HOME/mcp-server-diwall-browser/profile.json` when `XDG_CONFIG_HOME` is set), or from
the absolute path given in the environment variable `MCP_DIWALL_BROWSER_PROFILE`, which the
connection block of the `README.md` sets. The server reads it again on every call: a change
takes effect without a restart.

Create its directory and write the file with your editor:

```
mkdir -m 700 ~/.config/mcp-server-diwall-browser
vim ~/.config/mcp-server-diwall-browser/profile.json
chmod 600 ~/.config/mcp-server-diwall-browser/profile.json
chmod 700 ~/.config/mcp-server-diwall-browser
```

A complete example, with every key; the server accepts it as written. Adapt the paths, the
origins and the browser to yours:

```json
{
  "schema_version": 1,
  "secrets_dir": "~/Vaults/Main",
  "secrets_crypt_dir": "~/Vaults/Main.enc",
  "allowed_origins": ["https://account.example.invalid", "https://shop.example.invalid"],
  "confirm_each_change": ["https://account.example.invalid"],
  "local_model_origins": [],
  "declarations": {"local_model_client": false, "client_does_not_auto_confirm": false},
  "browser": {
    "family": "chrome",
    "profile_dir": "~/Vaults/Main/mcp-server-diwall-browser/browser-profile/chrome",
    "debug_port": 9222
  },
  "limits": {"min_action_delay_ms": 250, "max_actions_per_session": 200, "max_pages_per_session": 50}
}
```

- `schema_version`: always `1`.
- `secrets_dir`: where the encrypted directory is open (absolute path).
- `secrets_crypt_dir` (optional): the encrypted directory on disk. With it, the server tells a
  closed encrypted directory from one that was never created; without it, a closed one reads
  `closed_or_absent`.
- `allowed_origins`: the sites the server reads and acts on, each an exact origin (scheme, host,
  port), at most 200. Of any other page, only the origin is returned.
- `confirm_each_change`: origins of `allowed_origins` where every `click`, `fill`,
  `select_option`, Enter key and accepted dialog asks for a confirmation, not only payments,
  deletions and secret changes.
- `local_model_origins`: origins you want handled by a model that runs on this machine. The server
  shows them in `status` (`local_model_only`) and does nothing else with them: it cannot know where
  the model runs.
- `declarations.local_model_client`: your statement that the MCP client runs its model on this
  machine.
- `declarations.client_does_not_auto_confirm`: your statement that the MCP client never answers a
  confirmation by itself. Only with it may the server ask a confirmation through the client when no
  native window can be shown.
- The two declarations are your assertions: the server cannot check them, and shows them in
  `status` as written.
- `browser.family`: `chrome`, `chromium` or `brave`; it tells the server where that browser keeps
  its default profile, which it refuses.
- `browser.profile_dir`: the `--user-data-dir` of the dedicated profile (absolute path). `status`
  says in `profile_encrypted` whether this path lies in the open encrypted directory; that is what
  the profile says, not a proof of where the browser keeps its data.
- `browser.debug_port`: the `--remote-debugging-port`, from 1024 to 65535 (default 9222).
- `limits.min_action_delay_ms`: the least time between two actions, from 0 to 60000 (default 250).
- `limits.max_actions_per_session`: actions allowed to one server process, from 1 to 100000
  (default 200).
- `limits.max_pages_per_session`: pages opened or navigated to by one server process, from 1 to
  100000 (default 50).

The ceilings count an action when it starts, once the server is attached to the browser: a call
refused before reaching the browser does not count against the ceilings. Restarting the server
(the MCP client) starts the counts again.

A key that is not in this list is refused, and a key that would configure the locked actions (such
as `payment` or `deletion`) is refused with `redlist_in_profile`: only the code decides what is
locked.

## 6. A credential file

A credential file holds the user name and password of one site. It lives in
`~/Vaults/Main/mcp-server-diwall-browser/credentials/`, in mode 600, owned by the
server's user. Its name is lowercase letters, digits, `.`, `_` and `-`, at most 64 characters,
followed by `.json`: here `account.json`. Its keys are `allowed_origins` (required, not empty),
`username` and `password` (at least one of the two), and no other.

```json
{
  "allowed_origins": ["https://account.example.invalid"],
  "username": "alice",
  "password": "replace-with-the-real-password"
}
```

Write it with your editor, never with `echo` or a shell redirection, which would leave the
password in the shell's history:

```
vim ~/Vaults/Main/mcp-server-diwall-browser/credentials/account.json
chmod 600 ~/Vaults/Main/mcp-server-diwall-browser/credentials/account.json
```

The origin is exact: scheme, host and port. With the file above, a page of
`https://account.example.invalid` receives the credential; `http://account.example.invalid`,
`https://account.example.invalid:8443` and `https://www.account.example.invalid` do not
(`credential_not_bound`). The model asks for a value with `credential_ref` (`{"key": "password"}`,
or `{"key": "password", "name": "account"}` when several files are bound to one origin); the
value is typed by the server and never appears in a result.

## 7. What the model sees and what the server keeps

| What leaves | Through what | Who decides |
|---|---|---|
| Nothing of the secrets | Resolved in memory in the server's process at typing time, sent to the browser over the local debugging connection; never in a tool output, a log, a report, an address or a command argument | Nobody: hard lock |
| Nothing of the sensitive fields on screen | Masked in every capture; values redacted in the tree; if masking fails, the capture is not published | Nobody: hard lock |
| The content of the pages of the allowed origins | Capture, accessibility tree and elements, read by the model; its provider receives them if the model is hosted | The operator: through the allowed origins and the choice of the model |
| The content of pages outside the allowed origins | Nothing: only the origin is returned | Nobody: hard lock |
| The addresses of the pages | Origin and path; values of sensitive parameters redacted; fragment redacted; never credentials in the address | Nobody: hard lock |
| The titles and addresses of the browser's other tabs | Nothing | Nobody: hard lock |
| Error and exception messages | The same exit point as everything else | Nobody: hard lock |
| A file of the local disk | Nothing: no navigation to a local file, no upload | Nobody: hard lock |
| The audit journal and the evidence | Nothing: written locally, in the encrypted directory for what comes from an authenticated page | Nobody |
| Any other emission to a third party | None exists | The operator: a new emission is an explicit choice |

What the model's provider keeps of what it receives is not confirmed by this project. A frame of
another origin embedded in a page of the allowed origins is transmitted with that page: the server
masks frames in the image, but it cannot filter what a model receives from elsewhere.

## 8. What the server never does

- It never mounts or unmounts the encrypted directory, never runs `gocryptfs` or `fusermount3`,
  and never knows the password of the encrypted directory.
- It opens no listening port. It connects only to the browser's debugging port on `127.0.0.1`.
- It runs no script that the model or a page provides; the only scripts it sends to a page are
  the constant ones written in its code.
- It uploads no file and opens no local file.
- It does not read or write the clipboard.
- It does not show or read the tabs you opened yourself; it lists only the tabs it opened.
- It does not start or close the browser; its tabs stay open when it stops.
- It never pays, deletes or changes a secret without a confirmation.
- It never changes the operator profile or the allowed origins.

## 9. The confirmation

Before a payment, an irreversible deletion or a change of secret (and before every change on an
origin of `confirm_each_change`), the server opens a window on your screen, titled
`Confirmation du serveur MCP`, with two buttons, `Autoriser` and `Refuser`. Refusal is the default
button when the window tool offers that choice. The window waits 90 seconds, then counts as no
answer (`confirmation_expired`). One answer covers one action only.

Its text is built by `confirm.build_text` from what the server reads in the browser at that
moment. For a click on a payment button, this project's session produced:

```
Origine : https://shop.example.invalid
Outil : click — geste : clic
Texte tiré de la page : button "Payer maintenant"
Cible du formulaire : https://shop.example.invalid
Montants détectés (heuristique) : 42,00 €
Catégories : paiement
Signaux : vocabulary_payment, card_field_on_page, card_field_in_form
```

What to read before answering:

- `Origine`: is it the site you expect?
- `Outil` and `geste`: what the model is about to do.
- `Texte tiré de la page`: the label of the element, written by the page; a hostile page can lie.
- `Cible du formulaire`: where the form sends its data; an origin you do not expect is a reason to
  refuse.
- `Montants détectés`: amounts found on the page by a heuristic; it can miss or misread one.
- `Catégories` and `Signaux`: why the server asks.

A real `kdialog` or `zenity` window was not shown in this project's environment (no display); the
tests use fake programs in their place.

The window belongs to the server, not to the MCP client: the client acts for the model and could
answer by itself, while the server's window is answered by a person, outside the conversation. The
server asks through the client only if you declared `client_does_not_auto_confirm` and no window can
be shown.

The window needs a display: `DISPLAY` or `WAYLAND_DISPLAY` in the environment of the server's
process. `status` says which way a confirmation would be asked, in `confirmation_channel`:
`native` (the window), `elicitation` (through the client) or `none` (every action that needs a
confirmation is refused). The MCP SDK's own client starts a server with `HOME`, `LOGNAME`, `PATH`,
`SHELL`, `TERM` and `USER` only (read in its source, version 1.25.0); what other clients pass is not
confirmed. If `status` says `none` while `kdialog` or `zenity` is installed, add the variable to the
`env` block of the connection, for example `"DISPLAY": ":0"`; `echo $DISPLAY` in your desktop
session gives your value.

## 10. Backups

The open encrypted directory must enter no backup: a backup of `~/Vaults/Main` would hold
your credentials, journal and evidence unencrypted. Exclude it by file system boundary, for example
`rsync --one-file-system`, or by excluding the directory in your backup tool. The encrypted
directory on disk, `~/Vaults/Main.enc`, can be backed up elsewhere. Keep the master key
that `gocryptfs -init` printed off this disk.

This project does not know your backup tool: check the exclusion in its configuration.

## 11. Troubleshooting

To know the server's version, call `status` and read `server_version`. The version that the server
announces when a session opens is the one of the MCP SDK (`1.25.0`), not the server's: measured in
this project's session, the announcement read `mcp-server-diwall-browser 1.25.0` while `status` said
`0.0.1`.

Every refusal has a code and a message for the operator, in French, in `server.error`. When
`action_expected` is set, the next step is yours or that tool's. The messages below are the ones of
the code, with the example values in place of the variable parts.

`not_configured`: no operator profile is found, or a required key is missing.
`profil de l'opérateur introuvable : le fournir dans le fichier ~/.config/mcp-server-diwall-browser/profile.json (mode 600, répertoire 700) ou désigner un fichier par la variable d'environnement MCP_DIWALL_BROWSER_PROFILE (chemin absolu)`
or `valeur obligatoire absente du profil : browser ; …`. Write the operator profile (section 5); `status` shows `configured: false` and where to put it.

`profile_invalid`: the operator profile cannot be used as written; the message says where. For
example `le profil ~/.config/mcp-server-diwall-browser/profile.json est lisible par d'autres : corriger avec : chmod 600 ~/.config/mcp-server-diwall-browser/profile.json`,
`le répertoire du profil ~/.config/mcp-server-diwall-browser n'est pas privé : corriger avec : chmod 700 ~/.config/mcp-server-diwall-browser`,
`le profil n'est pas un document JSON valide (clés en double comprises)`,
`profil refusé : browser.debug_port (…)`, `profil refusé : allowed_origins[0] (…)`,
`la variable MCP_DIWALL_BROWSER_PROFILE doit désigner un chemin absolu`. Run the command the
message gives, or correct the key it names.

`redlist_in_profile`: `le profil contient la clé « payment » : la liste rouge n'est pas configurable, retirer cette clé`.
Remove that key from the operator profile.

`profile_dir_is_default`: `le profil déclaré (browser.profile_dir) est le répertoire de données par défaut du navigateur ou se trouve dedans : le serveur ne s'attache jamais au profil par défaut de l'opérateur ; créer un répertoire dédié et lancer le navigateur avec --user-data-dir=<ce répertoire>`.
Use the dedicated profile of section 3, in `browser.profile_dir` and on the browser's command line.

`debug_endpoint_not_loopback`: `le port de débogage du navigateur est joignable hors de la boucle locale : quiconque l'atteint prend le contrôle du navigateur ; l'opérateur doit relancer le navigateur sans --remote-debugging-address`.
Close the browser, start it again with the command of section 3, and check with
`ss -ltn "sport = :9222"`.

`debug_endpoint_undetermined`: `le serveur ne peut pas vérifier que le port de débogage n'écoute qu'en boucle locale : refus par précaution`.
The server could not read `/proc/net/tcp`, or `/proc/net/tcp6` on a system with IPv6; check that
`cat /proc/net/tcp /proc/net/tcp6` works for your user.

`browser_unreachable`: `le navigateur ne répond pas sur le port de débogage du profil : l'opérateur doit le lancer avec --remote-debugging-port=<port> et --user-data-dir=<profil dédié>`,
or `le navigateur ne répond pas : vérifier qu'il est lancé avec le port de débogage du profil`.
Start the browser (section 3) with the port of `browser.debug_port`.

`encrypted_dir_closed` and `encrypted_dir_undetermined`: `le répertoire chiffré est fermé ou non confirmé : l'opérateur doit l'ouvrir, puis relancer`
(`action_expected`: `operator_opens_directory`). Open the encrypted directory (section 4), then ask
the model to try again. If `secrets_crypt_dir` holds no volume, the message is
`le répertoire chiffré n'est pas confirmé : le répertoire chiffré déclaré dans le profil (secrets_crypt_dir) ne porte pas de volume initialisé ; l'opérateur doit vérifier le profil`:
correct `secrets_crypt_dir`.

`mount_not_gocryptfs`: `le répertoire déclaré n'est pas un volume gocryptfs monté pour l'utilisateur du serveur seul : refus par précaution`.
Something else is mounted at `secrets_dir`, or the volume is mounted by another user or with
`allow_other`. Check with `findmnt ~/Vaults/Main`; unmount and open it again as in
section 4.

`mount_unreadable`: `l'état de montage du répertoire chiffré ne peut pas être lu : refus par précaution`.
The server could not read `/proc/self/mountinfo` or the directory's state; check that
`cat /proc/self/mountinfo` works for your user.

`project_root_missing`: `le répertoire chiffré est ouvert mais le sous-répertoire du projet n'existe pas : l'opérateur doit le créer (mkdir -m 700 "<secrets_dir>/mcp-server-diwall-browser"), puis relancer`.
Run `mkdir -m 700 ~/Vaults/Main/mcp-server-diwall-browser`.

`volume_path_refused`: `un répertoire du volume chiffré n'est pas privé (mode 700, propriétaire du serveur) : refus par précaution`,
`un chemin du répertoire chiffré passe par un lien symbolique : refus par précaution` or
`un chemin du répertoire chiffré mène hors du volume : refus par précaution`. Give the server's
directories mode 700 (`chmod 700 ~/Vaults/Main/mcp-server-diwall-browser`, and the same
for the subdirectory named in `mount_reason`), and replace any symbolic link with a real
directory.

`origin_not_allowed`: `cette page n'est pas sur un site autorisé par le profil de l'opérateur : le serveur ne la lit pas`,
or for an action `cette page n'est pas sur un site autorisé par le profil : aucune action n'y est faite`.
Add the exact origin to `allowed_origins` if you want the server to work there.

`forbidden_scheme`: `seules les adresses http et https peuvent être ouvertes`. Nothing to do: the
server opens no `file:`, `javascript:` or other address.

`tab_not_owned`: `cet onglet n'existe pas, est fermé, ou n'a pas été ouvert par le serveur : utiliser un onglet de la liste du serveur ou en ouvrir un`.
The model should call `list_tabs` or `open_tab`. The server never works in a tab you opened.

`navigation_failed`: `la navigation a échoué (net::ERR_CONNECTION_REFUSED)` (the browser's error, here for a port where
nothing listens) or `adresse refusée (…)`. Check the address and your network in the dedicated
browser.

`snapshot_stale`: `la page a changé depuis l'observation : observer à nouveau avant d'agir`, or
`cet instantané n'existe plus ou n'est pas le dernier : observer à nouveau la page`. The model
observes again; nothing for you to do.

`element_stale`: `cet élément n'est plus celui de l'observation : observer à nouveau avant d'agir`,
`ce repère n'existe pas dans l'instantané : observer à nouveau avant d'agir`, or
`la boîte de dialogue a changé pendant la confirmation : rien n'a été accepté`. The model observes
again.

`element_obscured`: `un autre élément recouvre la cible à cet endroit : rien n'a été cliqué`,
`le curseur n'est pas dans l'élément visé : rien n'a été saisi`, or
`une boîte de dialogue est ouverte dans cet onglet : la traiter (handle_dialog) avant toute autre action`
(`action_expected`: `handle_dialog`). Close what covers the element (a banner, a pop-up) or let the
model answer the dialog.

`unsupported_element`: the action does not apply, for example
`seule une liste de choix native (select) se règle ainsi`,
`cet élément ne se remplit pas : un champ de texte ou une zone de texte est attendu`,
`la touche Entrée exige un repère (snapshot_id et mark)`,
`aucune boîte de dialogue n'est ouverte dans cet onglet`. The model chooses another way.

`option_not_found`: `aucune option de cette liste ne porte ce texte ou cette valeur`. The model
observes the list again.

`page_changed_during_capture`: `la page a changé d'adresse pendant la lecture : rien n'en est publié ; observer à nouveau`.
The model observes again.

`capture_unavailable`: `l'image n'est pas publiée : un masque n'a pas pu être posé`. No image with
every mask in place could be made, so none is published or kept as evidence. Accepting a dialog
that asks for a payment, a deletion or a change of secret also ends here: answer that dialog
yourself in the browser.

`sensitive_value_refused`: `ce champ est sensible : il ne se remplit que par un identifiant du répertoire chiffré (credential_ref), jamais par un texte`.
Write a credential file for this origin (section 6).

`credential_not_bound`: `aucun identifiant lié à l'origine https://account.example.invalid : un fichier d'identifiants doit lister cette origine dans allowed_origins`
(the wording of the second part depends on the request), or
`la page n'a pas d'origine web : aucun identifiant n'y est saisi`. Add the exact origin to the
`allowed_origins` of the right credential file.

`credential_ambiguous`: `plusieurs fichiers d'identifiants sont liés à l'origine https://account.example.invalid : account.json, account-admin.json`.
The model names one file in `credential_ref`; or keep one file per origin.

`credential_invalid`: `fichier d'identifiants « account.json » refusé (bad_mode) : corriger avec : chmod 600 account.json`;
the reason in parentheses is one of `bad_mode`, `bad_owner`, `not_regular_file`, `too_large`,
`not_json`, `unknown_keys`, `missing_allowed_origins`, `bad_origin`, `no_username_or_password`,
`key_missing`. For the directory:
`répertoire d'identifiants « credentials » refusé (bad_mode) : corriger avec : chmod 700 credentials`.
Run the command given, in `~/Vaults/Main/mcp-server-diwall-browser/credentials/`, or
correct the file as section 6 says.

`redlist_refused`: declared among the codes; no part of the server returns it in this revision.

`confirmation_denied`: `l'opérateur a refusé l'action : rien n'a été exécuté`. You refused; the
model must not ask again.

`confirmation_expired`: `la confirmation n'a pas été donnée dans le délai : rien n'a été exécuté`.
Nobody answered within 90 seconds.

`confirmation_unavailable`: `aucune fenêtre de confirmation ne peut être ouverte (kdialog ou zenity et un affichage sont requis) : rien n'a été exécuté`
(`action_expected`: `operator_installs_kdialog_or_zenity`). Install `kdialog` or `zenity`
(`sudo apt install kdialog`), and give the server a display (section 9).

`limit_reached`: `le plafond de la session est atteint (actions) : le serveur n'agit plus ; l'opérateur relance le serveur pour repartir, ou relève le plafond dans son profil`
(or `(pages)`; `action_expected`: `operator_restarts_or_raises_limit`). Restart the MCP client, or
raise `limits.max_actions_per_session` or `limits.max_pages_per_session`. A call refused before
reaching the browser does not count against the ceilings.

`journal_unavailable`: `le journal d'audit ne peut pas être écrit : action refusée par précaution`.
Check that the encrypted directory is open and that
`~/Vaults/Main/mcp-server-diwall-browser/journal` is yours, in mode 700.

`wait_timeout`: `la condition attendue n'est pas apparue dans le délai`. What the model waited for
did not come; nothing for you to do.

`invalid_arguments`: `paramètres refusés, rien n'a été fait : approved (inconnu)`. The model sent a
parameter the tool does not have, or a value of the wrong kind; nothing was done.

`internal_error`: `erreur interne du serveur`. An unexpected failure; the server's standard error
(kept by the MCP client) holds the name of the tool and the type of the failure, never page data.

## 12. Known limits

- While the server is attached, the browser refuses every download, in your own tabs too, except
  around one of the server's clicks or Enter keys; stop the server or the MCP client to download
  yourself.
- A download that a page starts more than half a second after a load ends is refused; download it
  yourself.
- Accepting a dialog whose message asks for a payment, a deletion or a change of secret is always
  refused (`capture_unavailable`): no evidence can be taken while a dialog is open. Answer it
  yourself.
- While a dialog is open, every other action in that tab is refused at once (`element_obscured`).
- Visibility is approximate: the server does not compute contrast or `overflow` clipping, so text
  you cannot read can still reach the model.
- The text inside frames is never read; frames are masked in the image.
- Fields the server deems sensitive are masked in the image and their values withheld; a field it
  does not recognise as sensitive is shown.
- The detection of payments, deletions and changes of secret relies on words and on card fields;
  measured on 100 common button labels, it missed none and locked 2 that a careful reader would
  not (`Effacer le formulaire`, `Supprimer le filtre`). Known misses: `Reset your password`,
  `Regenerate API key`, `Finaliser l'achat`, `Valider mon panier`.
- `local_model_origins` and the two declarations are shown in `status` and change nothing else.
- `profile_encrypted` in `status` says where the declared profile lies, not where the browser
  keeps its data.
- One server process allows 200 actions and 50 pages by default, with at least 250 ms between two
  actions; a call refused before reaching the browser does not count.
- A confirmation window waits 90 seconds, then counts as no answer.
- `intent` is kept to 500 characters.
- The version announced when a session opens is the MCP SDK's; `status` gives the server's.
- What the model's provider keeps of what it receives is not confirmed by this project.
- Not tested: a real `kdialog` or `zenity` window, a real gocryptfs volume, Brave, a browser
  profile kept in the encrypted directory, and the confirmation asked through a desktop MCP client.
