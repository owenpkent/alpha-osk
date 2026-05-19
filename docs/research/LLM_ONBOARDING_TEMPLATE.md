# LLM Onboarding

> **Copy this file to your repo as `LLM_ONBOARDING.md` and fill in the bracketed sections.**

Quick reference for AI assistants working on this repository.

---

## Project Overview

**Name:** [Project Name]  
**Purpose:** [One sentence — what does this do?]  
**Status:** [Active | In Development | Functional | Planning]

## About the Owner

I'm Owen — a wheelchair user with muscular dystrophy.

- **Typing is hard** — Be proactive. Make decisions. Don't ask for confirmation on small things.
- **Offer A/B/C choices** — I can type one letter instead of explaining.
- **PowerShell on Windows** — Use PowerShell syntax. Prefer single-line commands.
- **Accessibility matters** — Many of my projects are tools I actually need.

---

## Key Files

| File | Purpose |
|------|---------|
| `README.md` | Project overview, status, quick start |
| `TODO.md` | Task tracking (use `- [ ]` checkbox format) |
| [Add your key files here] | |

---

## Tech Stack

- **Language:** [Python / TypeScript / etc.]
- **Framework:** [PySide6 / FastAPI / React / etc.]
- **Dependencies:** See `requirements.txt` or `package.json`

---

## AI Collaboration (Sequential Handoffs)

I frequently switch between AI assistants (like Antigravity and Claude) on the **same exact local repository** when I run out of credits on one tool. When picking up a task:
- **Assume a Dirty State:** The previous AI may have been cut off mid-task. Immediately run `git status` and `git diff` to understand where it left off before making any new changes.
- **Handle Stale Git Locks:** If you encounter a `.git/index.lock` error, the previous AI might have ended abruptly during a Git operation. If the lock is stale, ask me for permission to delete it so you can proceed.
- **Troubleshooting Agent Unresponsiveness:** If Antigravity becomes completely unresponsive or fails to read the Git repository, it is likely crashing internally due to an unsupported Git extension left behind. Run `git config --local --unset extensions.worktreeConfig` to fix it.
- **Prepare for Handoffs:** If I tell you we are switching tools, or if you complete a meaningful chunk of work, use `git add -A` and `git commit -m "wip: [description]"` so the next agent has a clean checkpoint.

---

## Git Commits

Use conventional commits:
```
feat: add new feature
fix: correct bug
docs: update documentation
refactor: restructure code
chore: maintenance tasks
```

PowerShell:
```powershell
git add -A; git commit -m "feat: description"; git push
```

---

## Quick Start

```powershell
# [Update these commands for your project]
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

---

## Constellation

This repo is tracked by [Constellation](https://github.com/owenpkent/constellation).

For the dashboard to pick up this project:
1. Have a `README.md` with a `## Status` section
2. Have a `TODO.md` with checkbox items (`- [ ]` / `- [x]`)
3. Add the repo path to Constellation's `projects.yaml`
