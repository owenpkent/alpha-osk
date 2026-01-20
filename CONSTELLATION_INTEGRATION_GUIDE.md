# Constellation Integration Guide

> **Copy this file to new repos to help AI assistants understand your setup.**

How to structure your repository for Constellation tracking.

---

## About the Owner

I'm Owen — a wheelchair user with muscular dystrophy.

- **Typing is hard** — Be proactive. Make decisions. Don't ask for confirmation on small things.
- **Offer A/B/C choices** — I can type one letter instead of explaining.
- **PowerShell on Windows** — Use PowerShell syntax. Prefer single-line commands.
- **Accessibility matters** — Many projects are tools I actually need.

---

## Overview

**Constellation** is a meta-repository that aggregates status, TODOs, and context from multiple independent projects. It enables:

- Cross-project visibility and coordination
- AI-assisted project management (Cursor, Windsurf, etc.)
- Automated status dashboards

Your repo remains fully autonomous — Constellation just reads from it.

---

## Quick Checklist

To integrate with Constellation, your repo should have:

- [ ] **README.md** — Project overview, purpose, current state
- [ ] **TODO.md** (or equivalent) — Pending tasks in checkbox format
- [ ] **Consistent structure** — See recommendations below

---

## Required: README.md

Your README is the primary source of context. Include:

### Essential Sections

```markdown
# Project Name

Brief description of what this project does.

## Status

Current state: **Active** | **In Development** | **Stable** | **Planning**

## Features

- Feature 1
- Feature 2

## Quick Start

How to run/use the project.
```

### Helpful Additions

- **Screenshots** — Visual context for AI tools and humans
- **Tech Stack** — Languages, frameworks, dependencies
- **Project Structure** — Directory overview

---

## Required: TODO.md (or equivalent)

Constellation scans for pending tasks using checkbox syntax. Use one of these files:

| File Name | Common Use |
|-----------|------------|
| `TODO.md` | General task list |
| `NEXT_STEPS.md` | Sequential phases/steps |
| `ROADMAP.md` | Feature roadmap |

### Checkbox Format

Use GitHub-flavored markdown checkboxes:

```markdown
## Current Sprint

- [ ] Uncompleted task
- [x] Completed task
- [ ] Another pending item

## Backlog

- [ ] Future feature
- [ ] Nice to have
```

### Best Practices

- **Group by category or phase** — Easier to scan
- **Keep items actionable** — "Add user auth" not "Think about auth"
- **Mark completed items** — `[x]` helps track progress
- **Bold key items** — `- [ ] **High priority task**`

---

## Recommended: LLM_NOTES.md or LLM_ONBOARDING.md

A quick-reference file for AI assistants working on your repo:

```markdown
# LLM Notes

Quick reference for AI assistants.

## Project Context
What this project is and why it exists.

## Key Files
| File | Purpose |
|------|---------|
| `src/main.py` | Entry point |
| `config.json` | Settings |

## Conventions
- Naming conventions
- Code style preferences
- Things to avoid

## Owner Context
Any personal context relevant to the project (e.g., accessibility requirements, target users).
```

---

## Optional: Status Section in README

For quick status visibility, add a status section:

```markdown
## Current Status

| Area | Status |
|------|--------|
| Core Features | ✅ Complete |
| UI | 🔄 In Progress |
| Documentation | ⏳ Planned |
| Tests | ❌ Not Started |

**Next Milestone:** v1.0 release
**Blockers:** None
```

---

## Directory Structure Recommendations

Consistent structure across repos makes cross-project work easier:

```
your-project/
├── README.md              # Required: Project overview
├── TODO.md                # Required: Task tracking
├── LICENSE                # Recommended: MIT or similar
├── requirements.txt       # Python: dependencies
├── package.json           # Node: dependencies
├── src/                   # Source code
├── docs/                  # Extended documentation
├── tests/                 # Test files
└── assets/                # Images, icons, etc.
```

---

## How Constellation Uses Your Repo

### Automated Scanning

Constellation's `sync_status.py` script reads:

1. **TODO.md** (or NEXT_STEPS.md, ROADMAP.md) — Extracts unchecked items
2. **README.md** — Used for project context

### What Gets Aggregated

| From Your Repo | Into Constellation |
|----------------|-------------------|
| Unchecked TODOs | PROJECT_STATUS.md next steps |
| Completed/total counts | Progress percentages |
| README context | Project descriptions |

### Git Status

Constellation's `dashboard.py` checks:
- Branch name
- Uncommitted changes
- Ahead/behind remote

---

## PowerShell Usage

When providing commands or examples, use PowerShell syntax. Owen prefers single-line commands for efficiency:

### Common PowerShell Patterns

**Running Python scripts:**
```powershell
python scripts/sync_status.py
```

**Navigating and running commands:**
```powershell
cd C:\Users\Owen\dev\my-project; python src/main.py
```

**Checking file contents:**
```powershell
Get-Content README.md
```

**Running with arguments:**
```powershell
python scripts/dashboard.py --fetch
```

**Listing files:**
```powershell
Get-ChildItem -Path src -Filter *.py
```

### Tips for PowerShell Commands

- Use semicolons (`;`) to chain commands on one line
- Prefer `Get-Content` over `cat` for file reading
- Use `Get-ChildItem` or `ls` for directory listing
- PowerShell is case-insensitive for cmdlets but case-sensitive for file paths on some systems

---

## Example: Minimal Compliant Repo

```
my-project/
├── README.md
├── TODO.md
└── src/
    └── main.py
```

**README.md:**
```markdown
# My Project

A tool that does something useful.

## Status

**In Development** — Core features working, UI in progress.

## Quick Start

python src/main.py
```

**TODO.md:**
```markdown
# TODO

## Current

- [ ] Finish UI layout
- [ ] Add settings dialog
- [x] Core functionality

## Backlog

- [ ] Add tests
- [ ] Write documentation
```

---

## Git Commit Best Practices

When committing code (especially via AI assistants), follow these conventions:

### Commit Message Format

```
<type>: <short summary>

<optional body with details>
```

**Types:**
- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation only
- `refactor:` — Code change that neither fixes a bug nor adds a feature
- `style:` — Formatting, whitespace, etc.
- `chore:` — Maintenance tasks, dependencies, build changes

**Examples:**
```
feat: add voice control for lateral tilt
fix: prevent actuator overshoot at limits
docs: update README with safety warnings
refactor: extract motor control into separate module
```

### PowerShell Git Commands

```powershell
# Stage all changes
git add -A

# Commit with message
git commit -m "feat: add new feature"

# Push to origin
git push

# All in one line
git add -A; git commit -m "feat: add new feature"; git push
```

### When AI Commits

- **Be specific** — "Update files" is bad; "fix: correct axis mapping for rudder" is good
- **One logical change per commit** — Don't bundle unrelated changes
- **Use present tense** — "add feature" not "added feature"

---

## Working with Temporary Files

AI assistants often need scratch space for drafts, experiments, or intermediate work.

### Recommended Patterns

| Pattern | Use Case |
|---------|----------|
| `_scratch/` | Temporary working directory (gitignored) |
| `_draft_*.md` | Draft documents |
| `*.tmp` | Temporary files |

### .gitignore Additions

Add these to your `.gitignore`:

```
# AI/LLM scratch space
_scratch/
_draft_*
*.tmp
.cascade/
.cursor/
```

### When to Use Temp Files

- **Exploring options** — Write multiple versions before choosing
- **Complex refactors** — Stage changes before applying
- **Documentation drafts** — Iterate before finalizing
- **Test data** — Generate sample data for testing

### Cleanup

AI assistants should clean up temp files when done. If you see orphaned temp files, it's safe to delete them.

---

## Questions?

Reach out to Owen or refer to the [Constellation README](./README.md) for workflow details.
