# LLM Onboarding

Quick reference for AI assistants working on Alpha-OSK.

---

## Project Overview

**Name:** Alpha-OSK  
**Purpose:** AI-powered on-screen keyboard for Windows accessibility  
**Status:** Planning / Early Development

---

## About the Owner

I'm Owen — a wheelchair user with muscular dystrophy.

- **Typing is hard** — Be proactive. Make decisions. Don't ask for confirmation on small things.
- **Offer A/B/C choices** — I can type one letter instead of explaining.
- **PowerShell on Windows** — Use PowerShell syntax. Prefer single-line commands.
- **Accessibility matters** — This is a tool I actually need.

---

## Key Files

| File | Purpose |
|------|---------|
| `README.md` | Project overview, status, quick start |
| `TODO.md` | Task tracking (use `- [ ]` checkbox format) |
| `DESIGN.md` | Layout specs and UX documentation |
| `run.py` | Dashboard launcher |
| `templates/dashboard.html` | Project dashboard UI |

---

## Tech Stack

- **Language:** Python 3.11+
- **UI Framework:** PySide6 (Qt) for keyboard
- **AI/ML:** Transformers, Whisper, Flower
- **Dashboard:** HTML served via Python http.server

---

## Core Concepts

### AI Features
1. **Prediction** — Context-aware word completion using transformers
2. **Voice** — Whisper-based speech-to-text with commands
3. **Federated Learning** — On-device learning that shares model updates, not data

### Accessibility Modes
- **Dwell click** — Hover to activate
- **Scanning** — Row/column navigation for switch users
- **Voice** — Hands-free dictation

### Inspiration
- GNOME On-Board (Linux) — Good accessibility, no AI
- Windows OSK — Built-in but dated
- Mobile keyboards — Great AI, poor accessibility

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
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Dashboard opens at `http://localhost:8080`

---

## Constellation

This repo is tracked by [Constellation](https://github.com/owenpkent/constellation).

For the dashboard to pick up this project:
1. Have a `README.md` with a `## Status` section
2. Have a `TODO.md` with checkbox items (`- [ ]` / `- [x]`)
3. Add the repo path to Constellation's `projects.yaml`
