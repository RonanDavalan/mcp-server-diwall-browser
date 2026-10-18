# Acknowledgments and Contributors

This list is written as people and models work on the project, not at release time. Roles describe
the work actually done.

## Design and Development

- **Ronan Davalan**: architect and arbiter. Product vision, security requirements, project direction, validation and testing. Every architectural decision is his to validate.
- **Claude Code (Anthropic, Opus 5.5 and Sonnet 5.5)**: systems engineer and lead developer. Implementation of the MCP server, test suite, documentation, and the working framework.
- **Claude Cloud (Anthropic)**: code written on branches from prompts, without access to the framework, reviewed before every merge.
- **Gemini (Google)**: architectural advisor and synthesizer. Proposed the name **Diwall**.
- **Space Bunny Free (opencode)**: cleanup and pruning of the working framework at the start of the project.
- **Muse Spark 1.3 Contributor (Meta, through OpenCode)**: first use of the server by a model other than Claude (14 October 2026), on a local instance of a web application: thirteen driver scripts, a procedure record, and the field report that shaped the next fixes.
- **gemini-3.5-flash (Google, through OpenCode)**: tests of the dedicated-agent connection (15 October 2026): the server's tools reach the `navigateur` agent only, with the client recorded in the journal, and a shell command is not run without an answer.
- **Muse Spark 1.3 Contributor (Meta, through OpenCode)**: stylesheet and layout of the project's website (16 October 2026), chosen among several trials: the text and the behaviour of the pages were left untouched.
- **Muse Spark 1.3 Contributor (Meta, through OpenCode)**: reading of the sixteen French pages of the project's website and wording proposals in four languages (18 October 2026), kept as drafts.

## Heritage

The project descends from Diwall (diwall.davalan.fr) and its working method. Models that
contributed there shaped it: Muse Spark 1.3 Free (external session of 7-8 September 2026, a review of the
guide for the models), Grok (xAI, external reading of a delivered PDF that triggered a documentation
cleanup), and Gemini.

## Components

- The MCP protocol and its Python SDK.

## Code and Documentation Contributors

*(No outside contribution yet.)*
