<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

# Project Notes

## Goal and Stack

- This repository is an AI short-drama episode tracker built with FastAPI, SQLite, asyncio, a server-rendered HTML dashboard, and the bundled Douyin Web crawler.
- Run locally with `uvicorn douyin_user_monitor.main:app --host 0.0.0.0 --port 8900`.
- Run tests with `python -m unittest discover -s tests -v` after installing `requirements.txt` and `pytest`.

## Core Rules

- Short-drama business services depend only on `DouyinProvider`; they must not import a crawler or issue crawler HTTP requests directly.
- `Video.aweme_id` is the final de-duplication key and has a database `UNIQUE` constraint. A duplicate aweme must not be parsed or notified again.
- `Episode(show_id, season_number, episode_number)` is unique. A matching post from another account creates an `EpisodeSource`, not another Episode or update notification.
- Parser has three outcomes: `matched`, `ignored`, and `review`. Only real short-drama or episode signals that cannot be resolved go to review; ordinary videos are ignored. Do not auto-create an Episode below `AUTO_ACCEPT_CONFIDENCE` or when title / episode number is incomplete.
- Never commit or log cookies, access tokens, bot tokens, or webhook URLs. Runtime files belong under ignored `data/` or `.env`.
- Any change to `RegexParser`, `EpisodeParser`, `ContextParser`, or title/number normalization must add or update an exact case in `tests/fixtures/parser_golden.json` and pass `python -m douyin_user_monitor parser-eval` offline.
- Global search responses must use explicit display-field projections and must never expose raw provider payloads, LLM raw output, cookies, tokens, or runtime secrets.
- FTS5 is optional in production. Search changes must preserve the LIKE fallback and keep application startup functional when the SQLite runtime lacks FTS5.
