# Alpha-OSK Privacy

The short version: by default, **nothing leaves your computer**. Alpha-OSK predicts and learns entirely on your machine. Your typed text, vocabulary, keystroke history, and prediction model never touch a server.

## Optional usage telemetry

Settings → Data & Privacy → Privacy has one toggle: **"Share anonymous usage stats"**. It is off by default. If you turn it on, Alpha-OSK sends a small weekly report so we can track total impact across the community (e.g. "X million keystrokes saved across Y users").

### What's in the report

Nine numbers, sent once a week:

| Field | Example | What it is |
|-------|---------|------------|
| `anon_id` | `8f2a-4c1b-...` | A random ID generated when you first turn the toggle on. Not your username, email, or any system identifier. |
| `app_version` | `1.0.16` | Which version you're running. |
| `os` | `windows` | Your operating system (Windows or Linux). |
| `keystrokes` | `18523` | How many keys you've pressed total. |
| `words` | `3421` | How many words you've typed total. |
| `predictions` | `2671` | How many times you've clicked a suggestion. |
| `keystrokes_saved` | `10342` | How many keys the suggestions saved you. |
| `minutes` | `6366` | Total time spent typing. |
| `sessions` | `43` | How many times you've launched Alpha-OSK. |
| `prediction_offers` | `9210` | How many times we showed you suggestions. |

These are exactly the lifetime numbers shown on your Analytics dashboard. Nothing more.

### What's NOT in the report

- The text you typed. Ever. No words, no sentences, no fragments.
- Your vocabulary. The list of words you've taught Alpha-OSK never leaves your machine.
- Per-key data. We don't send which letters you press most, common typos, or anything per-keystroke.
- Per-session data. Only your running totals.
- Your IP address. Not logged on the server.
- Your machine. No hostname, MAC address, hardware ID, or operating-system install ID.
- Anything from typing into a password field. Privacy mode (the "Learning" / "Paused" toggle in the title bar) blocks all tracking when a password field is focused, so password-field activity never enters the totals in the first place.

### Where the data goes

To a Cloudflare Worker that we control. The worker stores one row per `anon_id` (the latest report, replaced each week — no history kept) and exposes a public aggregate endpoint that returns the total across everyone. Individual rows are never exposed publicly.

### Opting out

Turn the toggle off in Settings → Data & Privacy → Privacy. Future weekly reports stop. Already-submitted data is **not** automatically deleted — your row in the database stays until either (a) you click "Delete my contributed data" in the same Settings section, or (b) you don't open Alpha-OSK for 365 days, after which the row is automatically removed.

If you opt back in later, you get a **new** `anon_id`. Your prior contribution and your new contribution cannot be linked. This is intentional.

### Reinstalling / clearing config

If you reinstall Alpha-OSK or delete `%APPDATA%\alpha-osk\` (Windows) or `~/.config/alpha-osk/` (Linux), you start fresh. New `anon_id`, lifetime counters back to zero. Your old row in the database becomes orphaned and gets cleaned up by the 365-day rule.

## Data export (backup / move to a new computer)

Settings → Data & Privacy → Data Backup lets you export your prediction model, lifetime analytics, and imported vocabulary packs to a single `.zip` file you can copy to a USB stick, cloud drive, or another machine. Importing that file on the new machine restores your state without a reinstall.

What the export contains:

- Your prediction model (`ngram_model.json`, `ppm_model.json`).
- Lifetime analytics (`analytics.json` — the counters shown on the dashboard).
- Imported vocabulary packs (the folders under `packs/`).
- A manifest with the schema version, the Alpha-OSK version that wrote the file, an ISO-8601 UTC timestamp, and the list of files.

What the export does **not** contain:

- **Your telemetry contributor ID.** `telemetry.json` is intentionally excluded. Carrying the `anon_id` to a new machine would link your contributions across machines, which the "Opting out" section above promises won't happen. When you turn telemetry back on after import, the new machine generates a fresh `anon_id`.
- Settings (theme, layout, toggles, window size). Those live in the OS settings layer (Windows registry / Linux config) and are quick to reconfigure on the new machine.

Importing replaces your current data on that machine. Before any overwrite, Alpha-OSK saves your existing state as a timestamped **rescue export** in `<config>/exports/rescue-<timestamp>.zip` so you can roll back by importing that file.

## Federated learning

A separate planned feature (`roadmap/FEDERATED_LEARNING.md`) that would share *learning updates* across users to improve prediction quality for everyone. **Not yet implemented.** When it ships, it will be a separate opt-in toggle with its own clear explanation, distinct from this telemetry toggle. Federated learning never sends raw text either, but it sends more than telemetry does (n-gram statistical updates, with differential-privacy noise added). Worth understanding the trade-off separately before opting in.

## Auto-update

Alpha-OSK checks GitHub Releases on startup if "Check for updates on startup" is enabled in Settings → Data & Privacy → Updates. This sends an HTTPS request to GitHub for the latest release metadata. GitHub sees the request the same way it would see any unauthenticated HTTPS visit (your IP, your User-Agent, the URL). No Alpha-OSK identity is attached. This is unrelated to the telemetry toggle.

## Questions or concerns

Open an issue on the project repo. We'll respond.
