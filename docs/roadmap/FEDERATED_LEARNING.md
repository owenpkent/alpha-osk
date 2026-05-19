# Federated Learning Plan for Alpha-OSK

## Overview

Federated learning allows Alpha-OSK to improve prediction quality across all users without anyone's raw text ever leaving their device. Instead of sharing what you typed, your device shares only small, anonymized statistical updates (like "these two words often appear together") that get aggregated to improve the shared model.

## Why Federated Learning?

Alpha-OSK's prediction engine currently learns only from each individual user. This works well once a user has typed enough, but:

- **Cold start problem**: New users get generic predictions until they've typed enough to train the model.
- **Long-tail vocabulary**: Rare but important words (medical terms, names, technical jargon) may never be learned from one user's typing alone.
- **Accessibility community benefit**: Users with motor disabilities type less text per session, so individual learning is slower. Pooling anonymized insights across the community helps everyone faster.

## Privacy Principles

1. **Raw text never leaves the device.** No sentences, words, or phrases are transmitted.
2. **Only aggregated statistics are shared.** Specifically: n-gram frequency deltas (how much a word pair's weight changed during a session).
3. **Differential privacy.** Noise is added to updates before transmission so individual contributions cannot be reverse-engineered.
4. **Opt-in only.** Federated learning is disabled by default. Users must explicitly enable it in Settings.
5. **Transparent and auditable.** The exact data being sent is shown to the user before transmission.
6. **No account required.** Contributions are anonymous. No user ID is attached to updates.

## Architecture

```
User's Device                          Aggregation Server
+---------------------------+          +---------------------------+
|                           |          |                           |
|  Alpha-OSK types text     |          |  Receives anonymous       |
|  locally, learns n-grams  |          |  n-gram deltas from       |
|          |                |          |  many devices             |
|          v                |          |          |                |
|  Compute session delta:   |          |          v                |
|  new_freq - old_freq      |          |  Aggregate with weighted  |
|  for top-K changed pairs  |          |  averaging + outlier      |
|          |                |          |  rejection                |
|          v                |          |          |                |
|  Add differential privacy |  ---->>  |          v                |
|  noise (epsilon=1.0)      |  HTTPS   |  Publish updated          |
|          |                |          |  community model          |
|          v                |          |  (versioned JSON)         |
|  Transmit delta           |          |                           |
|  (no raw text)            |          +---------------------------+
|                           |                     |
|  Download community       |  <<----             |
|  model update             |  HTTPS              |
|          |                |                     |
|          v                |
|  Merge into local model   |
|  (local always wins ties) |
+---------------------------+
```

## What Gets Shared

### Transmitted (safe)
- **Bigram frequency deltas**: e.g., `("want", "to"): +3` means this pair was used 3 more times than the base model expected.
- **New unigram discoveries**: e.g., `"kubernetes": 5` means a word not in the base dictionary was used 5 times. Only words above a frequency threshold are shared.
- **Metadata**: model version, locale (e.g., "en-US"), session duration bucket (short/medium/long). No timestamps, no device ID.

### Never transmitted
- Raw typed text, sentences, or phrases
- User identity or device fingerprint
- Context (what was typed before/after a word)
- Blacklisted/dispreferred words
- Accessibility profile settings

## Differential Privacy

Before any delta leaves the device, Laplace noise is added:

```python
import numpy as np

def add_noise(delta: dict, epsilon: float = 1.0) -> dict:
    """Add Laplace noise to n-gram deltas for differential privacy."""
    sensitivity = 1.0  # max change one user can cause per n-gram
    scale = sensitivity / epsilon
    noisy = {}
    for key, value in delta.items():
        noisy_value = value + np.random.laplace(0, scale)
        # Only include if signal exceeds noise floor
        if abs(noisy_value) >= 2.0:
            noisy[key] = round(noisy_value, 1)
    return noisy
```

With `epsilon=1.0`, an individual user's contribution is mathematically indistinguishable from random noise. The aggregation server needs many contributions (hundreds) before meaningful patterns emerge.

## Update Cycle

1. **Session ends** (user closes keyboard or explicitly triggers save).
2. If federated learning is enabled, compute delta between current model state and the baseline.
3. Apply differential privacy noise.
4. Show user a summary: "Sharing 47 word-pair frequency updates (no raw text)."
5. User can review details or skip this upload.
6. Upload via HTTPS POST to aggregation endpoint.
7. Periodically (e.g., weekly), download the latest community model update.
8. Merge community model into local model. Local user-learned frequencies always take priority.

## Community Model Merging Strategy

```python
def merge_community_model(local: dict, community: dict) -> dict:
    """Merge community model into local, preserving user preferences."""
    merged = dict(local)
    for word, freq in community.items():
        if word in local:
            # User's own frequency wins; community provides a floor
            merged[word] = max(local[word], freq)
        else:
            # New word from community — add with reduced weight
            merged[word] = freq // 2
    return merged
```

## Implementation Phases

### Phase 1: Local Delta Computation (no network)
- Track baseline model state at session start
- Compute delta at session end
- Show "what would be shared" in analytics dashboard
- No actual transmission — just build the infrastructure

### Phase 2: Opt-In Upload
- Add "Contribute to community predictions" toggle in Settings
- Show preview of data before sending
- Implement HTTPS upload to a simple aggregation endpoint
- Endpoint stores deltas in append-only log (no processing yet)

### Phase 3: Aggregation Server
- Process collected deltas with weighted averaging
- Reject outliers (e.g., a delta with 10,000x normal frequency)
- Publish versioned community model JSON
- Host as a simple static file (e.g., GitHub Releases or S3)

### Phase 4: Community Model Download
- Periodic check for new community model version
- Download and merge into local model
- Show "Community model updated" notification

### Phase 5: Vocabulary Pack Crowdsourcing
- Allow users to opt-in to sharing vocabulary pack improvements
- If many medical users consistently add the same words, auto-suggest a pack update
- Pack maintainers review and publish updates

## Server Requirements

The aggregation server is intentionally simple:

- **Receives**: JSON POST of noisy deltas (~1-5 KB per session)
- **Stores**: Append-only log of deltas, grouped by model version and locale
- **Processes**: Batch job (daily/weekly) to compute weighted average
- **Publishes**: Static JSON file (community model update)
- **No database**: Flat files or S3 objects
- **No auth**: Anonymous submissions (rate-limited by IP)
- **No PII**: Nothing to breach — deltas are pre-noised and anonymous

Estimated hosting cost: ~$5/month for thousands of users.

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Poisoning attacks (adversary sends bad deltas) | Outlier rejection, frequency capping, require minimum session duration |
| Privacy leak via frequency analysis | Differential privacy noise, minimum frequency threshold for sharing |
| User distrust | Transparent preview of exact data shared, opt-in only, open-source server |
| Low adoption | Start with Phase 1 (local-only) to build trust, show value before asking for contribution |
| Model divergence | Community model is always additive — it can only add words or boost frequencies, never remove |

## UI Design

### Settings Toggle
```
[ ] Contribute to community predictions
    Help improve predictions for all users.
    Only anonymized word-pair statistics are
    shared — never your actual text.
    [View what would be shared]
```

### Analytics Integration
```
Community Contribution
  47 word-pair updates shared this session
  Community model v12 (updated 3 days ago)
  1,247 contributors this month
```

## Open Questions

1. **Locale handling**: Should English-US and English-UK have separate community models?
2. **Vocabulary pack contributions**: Should pack-specific deltas be separated from general model deltas?
3. **Minimum session threshold**: How many words must a user type before their delta is worth sharing? (Proposed: 50 words minimum)
4. **Epsilon tuning**: Should privacy budget be configurable, or fixed at epsilon=1.0?
5. **Consent UX**: Should we show a one-time explanation popup, or just a toggle in settings?
