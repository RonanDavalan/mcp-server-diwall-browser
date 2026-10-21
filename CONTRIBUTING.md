# Contributing

mcp-server-diwall-browser lets a language model see and act in a browser where its operator is
signed in. A change here can reach real sessions, so a contribution is welcome in the measure that
it can be checked: a test that fails without it, a command that shows the result, a sentence that
can be compared with the code.

## Prepare the environment

You need Python 3.12 or later and `git`. From a clone of the repository:

```
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

`requirements.lock` lists the versions this project tested. Nothing else is installed on your
machine, and nothing touches your browser, your encrypted directory or your operator profile.

## Run the tests

```
.venv/bin/python -m pytest -q -m "not browser"
.venv/bin/python scripts/smoke_test.py
```

The first command runs every test that needs no browser; it is the suite that runs on every push
(`.github/workflows/ci.yml`). The second starts the server against a browser of its own, on a blank
temporary profile, and needs a Chromium-family browser on your `PATH`. With such a browser,
`.venv/bin/python -m pytest -q` also runs the browser tests, each on a blank temporary profile.

A change to the behaviour comes with a test, written first and seen failing, then passing. A test
is never loosened to make it pass.

## What is welcome

- **A review of a translation.** The guides, the architecture document, the manual page and the
  README exist in French, German and Spanish under `docs/fr-FR/`, `docs/de-DE/` and `docs/es-ES/`.
  A wrong word, a stiff sentence or a typography mistake is worth an issue (use the translation
  template) or a pull request. A translation keeps the structure of the English file: the same
  sections in the same order, and the code blocks and commands unchanged.
- **A package for another distribution.** The project ships a `.deb`, two `.rpm` and an Arch
  package. Open an issue that names the distribution and its release before you start, so that the
  way the files are laid out under `/usr/lib` is agreed first.
- **A test.** A case the suite does not cover, a regression you hit, a page that fools the server
  (a hostile page is the case the project is built around).
- **A report of a defect**, with the exact command, what you saw and what you expected, using the
  default template. State your browser and its version, and your operating system.
- **A correction of the documentation** that you checked by running the commands it gives.

## What to keep out

- No real password, token, cookie, session identifier or personal address in an issue, a pull
  request, a log or a capture: replace the value with `<redacted>`. Use `~/` in paths, not your home
  directory.
- No change that loosens a safeguard (the loopback-only debugging port, the list of allowed sites,
  the human confirmation of a payment, a deletion or a change of secret) without an issue that makes
  the case first.

## Report a vulnerability

Not in a public issue. Follow [SECURITY.md](SECURITY.md): a private report through GitHub Security
Advisories, answered within 72 hours.

## Code and comments

The code comments and docstrings are written in French, one language per file; identifiers, error
codes, the documentation of the public layer, commit messages and issues are in English. A comment
says why, never what the line does, and tells nothing of the history of the edit. Match the file you
change.

## Pull requests

One subject per pull request. Say which test fails without it, which commands you ran, and the
result. The CI must pass.
