# Alpha-OSK Privacy

The short version: by default, **nothing leaves your computer**. Alpha-OSK predicts and learns entirely on your machine. Your typed text, vocabulary, keystroke history, and prediction model never touch a server.

## Diagnostic log

Separately from telemetry, Alpha-OSK writes an operational log to `alpha-osk.log` in your config directory (`%APPDATA%\alpha-osk\` on Windows, `~/.config/alpha-osk/` on Linux). It's local only: nothing in it is ever uploaded, and it is not included in Data Backup exports. It rotates automatically (capped at 2 MB, keeping the 3 most recent files), so it can't grow without bound.

**Until this was fixed, the log recorded more than it should have.** Several logging call sites in `keyboard_bridge.py` wrote your actual typed text (up to 200 characters of in-progress context) to this file on every prediction tap, and did so even while privacy mode was paused. That defeated the point of privacy mode for anything written to disk. This is now fixed: those sites log lengths and booleans only (for example "42 chars of context," never the 42 characters themselves). Nothing the app writes at its normal logging level may contain anything you typed.

If you're attaching the log to a bug report, it's safe: it now contains only operational details like platform info, model load paths, and prediction-pipeline timing, never content.

Logs written *before* this fix could contain fragments of what you typed, so they are deleted for you. The first time you launch the fixed version, Alpha-OSK removes any existing `alpha-osk.log` and its rotated `.1` / `.2` / `.3` files, then notes in the new log how many it removed. This runs exactly once, so logs written after the fix are kept normally and you are not fighting the app to retain them. If you had copied an old log somewhere else (into a bug report, a support email, or a backup) that copy is outside our reach and is worth deleting yourself.

## Snippet auto-detection

Settings → Data & Privacy → Privacy has a toggle: **"Offer to save emails, phones and addresses"**. It is **on** by default. With it on, Alpha-OSK watches the text you type for something shaped like an email address, a phone number, or a street address, and when it sees one it offers to save it into Snippets so you can insert it with one tap instead of retyping it.

This is worth being precise about, because it means the keyboard is pattern-matching your typing for personal details:

- **It all happens on your machine.** The matching is a handful of regular expressions in `src/text_patterns.py`. Nothing is uploaded, and this is entirely separate from the telemetry toggle below.
- **Nothing is stored until you say so.** Detection produces an on-screen offer and nothing else. If you dismiss it, or ignore it until it fades after 8 seconds, nothing is written to disk. Only tapping **Save** writes anything.
- **It never runs while learning is paused.** In privacy mode, whether you paused it yourself with the title-bar Learning switch or a password field triggered it automatically, no detection happens at all. A password field cannot produce an offer, let alone a saved value.
- **It won't nag.** A value you dismissed is not offered again for the rest of the session, and a value already in your snippets is never offered.
- **It doesn't overwrite.** If the matching slot already holds a different value, saving appends a new one ("Email 2") rather than replacing what you had.
- **What you save is ordinary snippet data.** It goes into `snippets.json` in your config directory, the same file the Snippets editor writes, and it is included in Data Backup exports along with the rest of your snippets (see below).
- **Nothing detected is written to the log.** The log records that an offer was raised and how long the value was, never the value.

Turn the toggle off and no detection runs at all. Your existing snippets are untouched either way.

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
- Anything from typing into a password field. Privacy mode (the "Learning" switch in the title bar) blocks all tracking when a password field is focused, so password-field activity never enters the totals in the first place.

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
- Your snippets (`snippets.json`), including anything you saved from a detection offer. These are quick-insert text you wrote or approved, so they are personal by nature: a name, an email address, a phone number, a mailing address. The export is a plain, unencrypted `.zip`, so treat the file you produce with the same care you would treat that information anywhere else, and think twice before putting it somewhere shared.
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
