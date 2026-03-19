# Repository Guidelines
Always respond in Chinese-simplified
Any changes at the design level must be synchronized to the design documentation.

## Project Structure & Module Organization
This repository now contains a working Python CLI prototype. Keep the layout simple and predictable:

- `src/youtubesummary/` for application code
- `tests/` for automated tests
- `assets/` for static files such as images or sample data
- `docs/` for design notes, architecture decisions, or usage guides when added
- `design.md` for the active design baseline
- `yt_asr_summary.py` as the top-level compatibility entry script
- `downloads/` for generated media, reports, and transcripts only

Prefer feature-oriented subdirectories inside `src/` (for example, `src/capture/` or `src/export/`) rather than a single flat folder.

## Build, Test, and Development Commands
No build system is configured yet. Current working commands:

- `python3 -m venv .venv` to create the local virtual environment
- `source .venv/bin/activate` to activate it on Linux/macOS
- `pip install -r requirements.txt` to install runtime dependencies
- `python3 yt_asr_summary.py --help` to inspect CLI options
- `python3 yt_asr_summary.py "<youtube-url>"` to analyze a YouTube video
- `python3 yt_asr_summary.py --media-file /path/to/file.mp4` to analyze a local media file

If you introduce a different toolchain, keep command names conventional and update this file in the same change.

## Coding Style & Naming Conventions
Use 2 spaces for YAML, JSON, and Markdown indentation. Follow the formatter standard for the chosen language rather than hand-formatting. Naming should stay consistent:

- `snake_case` for filenames in Python-heavy codebases
- `kebab-case` for Markdown and asset filenames
- `PascalCase` for classes and React-style components
- `camelCase` for variables and functions where idiomatic

Add linting and formatting early; prefer established tools such as `ruff`, `black`, `eslint`, or `prettier`.
Add descriptions to each code file.
Add as detailed comments as possible to critical code.
Comments using Chinese-simplified.

## Testing Guidelines
Place tests under `tests/` and mirror the source layout when practical, such as `tests/capture/test_parser.py` for `src/capture/parser.py`. Name tests clearly around behavior. Aim for fast, deterministic tests first, then add integration coverage for external APIs, file IO, or media processing.

## Commit & Pull Request Guidelines
Local Git history is not available in this workspace, so no existing commit convention could be inferred. Use short, imperative commit messages such as `Add capture pipeline scaffold` or `Define export test fixtures`.

This project now uses a local Git repository. Keep generated media, reports, transcripts, virtual environments, and cache files out of version control via `.gitignore`. Before submitting changes, verify `git status` only includes intentional source and documentation updates.

Pull requests should include:

- a brief description of the change
- linked issue or task reference when applicable
- test evidence (`npm test`, `pytest`, etc.)
- screenshots or sample output for UI or media-related changes

## Agent-Specific Instructions
Keep changes narrowly scoped. When adding tooling, tests, or structure, update this document so contributors can follow the repository’s actual workflow rather than assumptions.
