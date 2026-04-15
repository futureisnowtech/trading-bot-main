# Claude Code Workspace

This directory holds repo-local operating docs for Claude Code.

Files:
- `CLAUDE_CODE_BOOT_PROMPT.md` — the bootstrap prompt to paste into Claude Code for this repo
- `skills/` — narrow workflow playbooks Claude should follow for recurring tasks

How to use:
1. Start Claude Code in this repo.
2. Paste the prompt from `CLAUDE_CODE_BOOT_PROMPT.md`.
3. When a task matches one of the skills, tell Claude to use it explicitly, or let the prompt route Claude to the best match.

Design rule:
- Keep long-term project truth in `CLAUDE.md` and `AGENTS.md`.
- Keep repeatable task workflows in `claude/skills/*.md`.
- Keep skills narrow and operational, not generic.
