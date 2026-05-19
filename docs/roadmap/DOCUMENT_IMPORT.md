# Document Import — Learning from User Writing Style

## Goal

Let users import their own documents (emails, essays, chat logs, notes) so the prediction engine learns their vocabulary, phrasing, and word patterns. The keyboard should feel like it "knows" how they write.

---

## Do We Need AI?

**Short answer: No, not for the core feature.**

The existing prediction engine (n-gram + PPM) already learns writing style effectively without any AI/ML model:

| What It Learns | How | AI Needed? |
|----------------|-----|------------|
| Word frequency (you say "however" not "but") | Unigram counts | No |
| Word pairs ("looking forward", "on the other hand") | Bigram model | No |
| Three-word phrases ("I would like", "as soon as") | Trigram model | No |
| Typing patterns / corrections | PPM character model | No |
| Domain vocabulary (medical, tech, etc.) | Dictionary injection | No |
| Long-range context ("Given the previous paragraph...") | LLM re-ranking | Yes |
| Tone/formality adaptation | LLM or classifier | Yes |

The n-gram approach is:
- **Fast** — predictions in <10ms, no GPU needed
- **Private** — all processing is local, no data leaves the device
- **Incremental** — learns from each new document without retraining
- **Lightweight** — model is a JSON file, typically <5MB even after heavy use

### When AI Would Help

An LLM (like the existing optional DistilGPT-2 integration) could improve predictions *after* import by:
- Re-ranking suggestions using longer context (full sentence, not just last 2 words)
- Understanding that "Dear" at the start of a line should suggest "Sir/Madam" not "deer"
- Adapting tone (formal emails vs casual chat)

But this is an **enhancement**, not a requirement. The n-gram system alone will produce noticeably better predictions after importing even a few pages of the user's writing.

---

## What Already Exists

The bridge already has two import methods (not yet exposed in the UI):

```python
# keyboard_bridge.py
@Slot(str, result=bool)
def importTextFile(self, file_path: str) -> bool:
    """Import a text file to train the prediction model."""
    # Reads file, calls self._predictor._ngram.learn(text)

@Slot(str, result=int)
def importFolder(self, folder_path: str) -> int:
    """Import all text files from a folder."""
    # Walks folder, imports .txt, .md, .py, .js, .html, .css, .json
```

These feed text into `NgramPredictor.learn()`, which updates:
- **Unigrams** — individual word frequencies
- **Bigrams** — word pair frequencies (prev_word → next_word)
- **Trigrams** — three-word sequence frequencies
- **User vocab** — personal boost scores with recency decay

The PPM model (`PPMPredictor`) also learns character-level patterns but is not currently trained from imported files.

---

## Implementation Plan

### Phase 1: Basic File Import (UI + Plumbing)

**Effort: Small — mostly UI work, backend already exists.**

1. Add an "Import Documents" button to the Settings panel (under Data section)
2. Open a native file dialog (`FileDialog` in QML or `QFileDialog`)
3. Support formats: `.txt`, `.md`, `.doc/.docx`, `.pdf`, `.rtf`, `.eml`, `.html`
4. Show progress (word count, file name) during import
5. Train both n-gram AND PPM models from imported text
6. Auto-save model after import

**File format handling:**

| Format | Approach | Dependency |
|--------|----------|------------|
| `.txt`, `.md` | Read directly | None |
| `.html` | Strip tags, extract text | `html.parser` (stdlib) |
| `.docx` | Extract text from XML | `python-docx` (pip) |
| `.pdf` | Extract text | `PyMuPDF` or `pdfplumber` (pip) |
| `.rtf` | Strip RTF formatting | `striprtf` (pip) |
| `.eml` | Parse email body | `email` (stdlib) |
| `.csv` | Extract text columns | `csv` (stdlib) |

For Phase 1, start with `.txt` and `.md` only (zero dependencies). Add other formats later.

### Phase 2: Folder Import + Smart Filtering

1. "Import Folder" button — imports all supported files recursively
2. Filter out code/config (skip files that are mostly non-English)
3. Skip binary files, very large files (>10MB), and duplicates
4. Show summary: "Imported 47 files, 23,400 words. Top new words: ..."
5. Let user preview what will be imported before committing

### Phase 3: Clipboard / Paste Import

Some users may not have documents saved as files. Let them:
1. Paste text directly into an import dialog
2. "Learn from clipboard" button — grabs current clipboard content
3. Good for: chat logs, social media posts, email threads

### Phase 4: Email Client Integration (Advanced)

For power users who want the keyboard to learn from their email style:
1. Connect to email via IMAP (Gmail, Outlook)
2. Import sent folder (only what the USER wrote, not received)
3. Strip signatures, quoted replies, headers
4. Privacy: all processing local, credentials stored in OS keychain

This would need AI to separate the user's writing from quoted text reliably.

### Phase 5: Continuous Learning (Background)

Instead of one-time import, watch a folder for changes:
1. User designates a "learning folder" (e.g., their Documents directory)
2. Background thread monitors for new/changed files
3. Incrementally learns from new content
4. Respects an ignore list (`.learnignore` file, similar to `.gitignore`)

---

## Technical Details

### Text Preprocessing Pipeline

Before feeding text to the prediction engine, we should clean it:

```
Raw text
  → Strip formatting (HTML tags, markdown syntax, RTF codes)
  → Normalize whitespace (collapse multiple spaces/newlines)
  → Split into sentences (period/question/exclamation boundaries)
  → Filter out: URLs, email addresses, file paths, code blocks
  → Filter out: numbers-only lines, very short lines (<3 words)
  → Lowercase for learning (prediction engine is case-insensitive)
  → Feed each sentence to ngram.learn() and ppm.learn_text()
```

### What to Learn vs. What to Skip

**Learn from:**
- Natural language sentences
- Common phrases and word pairs
- Domain-specific vocabulary

**Skip:**
- Code snippets (detected by: lots of brackets, semicolons, indentation)
- URLs and email addresses
- Numbers and dates
- Headers/footers that repeat across documents
- Very short fragments (<3 words)

### Model Size Concerns

The n-gram model stores word frequencies as a JSON dict. Growth is sublinear:
- 10 documents (~50K words): ~200KB model
- 100 documents (~500K words): ~1MB model  
- 1000 documents (~5M words): ~5MB model

The recency decay system (`_decay_factor = 0.95` every 50 learns) naturally prunes old, rarely-used words. No manual cleanup needed.

### Privacy Considerations

- All imported text is processed locally — nothing is sent to any server
- The model stores word frequencies, not original text (you can't reconstruct documents from the model)
- "Clear Learned Data" button in settings wipes all imported knowledge
- If LLM re-ranking is enabled, it also runs locally (DistilGPT-2 on-device)

---

## UI Mockup

In the Settings panel, under the existing "Data" section:

```
┌─ Data ──────────────────────────────────┐
│                                         │
│  [  Import File...  ]  [  Import Folder... ] │
│                                         │
│  Learned: 12,340 words from 8 files     │
│  Model size: 420 KB                     │
│                                         │
│  [ Save Prediction Model ]              │
│  [ Clear Learned Data    ]              │
└─────────────────────────────────────────┘
```

After import, show a brief toast/notification:
```
✓ Imported "meeting_notes.txt" — 2,340 words learned
```

---

## Summary

| Question | Answer |
|----------|--------|
| Do we need AI? | No — n-grams learn writing style effectively |
| Does AI help? | Yes — LLM re-ranking improves context-aware suggestions, but is optional |
| What's the minimum viable version? | File dialog → read .txt/.md → feed to existing `learn()` → done |
| What dependencies are needed? | None for .txt/.md; `python-docx` for Word; `PyMuPDF` for PDF |
| Is it private? | Yes — all local, model stores frequencies not original text |
| How much work? | Phase 1 is small (UI only, backend exists). Phase 2-5 are incremental. |
