# Gemini Code Workspace

This directory holds repo-local operating docs for Gemini Code.

Files:
- `GEMINI_CODE_BOOT_PROMPT.md` — the bootstrap prompt to paste into Gemini Code for this repo
- `skills/` — narrow workflow playbooks Gemini should follow for recurring tasks

How to use:
1. Start Gemini Code in this repo.
2. Paste the prompt from `GEMINI_CODE_BOOT_PROMPT.md`.
3. When a task matches one of the skills, tell Gemini to use it explicitly, or let the prompt route Gemini to the best match.

Design rule:
- Keep canonical project truth in `AGENTS.md`.
- Keep `GEMINI.md` as the concise Gemini-facing companion.
- Keep repeatable task workflows in `gemini/skills/*.md`.
- Keep skills narrow and operational, not generic.
