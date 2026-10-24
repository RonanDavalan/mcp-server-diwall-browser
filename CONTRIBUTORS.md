# Acknowledgments and Contributors

This list is written as people and models work on the project, not at release time. Roles describe
the work actually done. Each person or model appears once, with everything they did; the order follows
the weight of the work.

## Design and Development

- **Ronan Davalan**: architect and arbiter. Product vision, security requirements, project direction, validation and testing. Every architectural decision is his to validate.
- **Claude Code (Anthropic, Opus 5.5, Sonnet 5.5 and Haiku 5.5)**: systems engineer and lead developer. Implementation of the MCP server, test suite, documentation, the text and upkeep of the project's website, the scene that illustrates the first release and its short clip, rendered in Blender on the workstation, driven through LibreModular-MCP, and a field report of the first launch from the Debian package.
- **Claude Cloud (Anthropic)**: code written on branches from prompts, without access to the project's private files, reviewed before every merge.
- **Muse Spark 1.3 Contributor (Meta, through OpenCode)**: first use of the server by a model other than Claude, on a local instance of a web application: thirteen driver scripts, a procedure record, and the field report that shaped the next fixes; the stylesheet and layout of the project's website, chosen among several trials, the text and the behaviour of the pages left untouched; two readings of the website, with wording proposals in four languages kept as drafts and defects found, which were corrected.
- **DeepSeek V4 Pro (DeepSeek, through OpenCode)**: independent security audit of the `v0.0.1` tag, from a blank session: a report of seven findings on the confirmation gate, the sensitive-field mask, credentials and URLs, four of them confirmed at triage and corrected.
- **GLM 5.3 (Zhipu, through OpenCode)**: independent security audit of the `v0.0.1` tag, from a blank session: a report of seven findings, with reproductions on a headless browser, on the confirmation gate, dialogs, credentials and navigation, three of them confirmed at triage and corrected.
- **DeepSeek V4.1 Flash 1017 (DeepSeek, through OpenCode)**: the 3D emblem "the glass D", drawn in Blender on the workstation, in two pupil variants (a round pupil and a pointer); it is the eye of the scene that illustrates the first release.
- **Gemini (Google)**: architectural advisor and synthesizer. Proposed the name **Diwall**.
- **gemini-3.5-flash (Google, through OpenCode)**: tests of the dedicated-agent connection: the server's tools reach the `navigateur` agent only, with the client recorded in the journal, and a shell command is not run without an answer.
- **Qwen3.8 Max (Alibaba, through OpenCode)**: visitor test before the first release, from a blank session with no access to the project's private files: the site read through a screenshot and accessibility tool, the installation of the Debian package in a clean Debian 13 container up to the first-launch report, and five questions answered from the French manual; two defects of the site and one of the operator guide were corrected from its reports.
- **Space Bunny Free (opencode)**: the project's logo, the round-eyed D, used on the website, its icons and the title page of the manuals; pruning of files at the start of the project.

## Heritage

The project descends from Diwall (diwall.davalan.fr) and its working method. Models that
contributed there shaped it: Muse Spark 1.3 Free (an external review of the
guide for the models), Grok (xAI, external reading of a delivered PDF that triggered a documentation
cleanup), and Gemini.

## Components

- The MCP protocol and its Python SDK.

## Code and Documentation Contributors

*(No outside contribution yet.)*
