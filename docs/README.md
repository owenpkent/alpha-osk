# Alpha-OSK Documentation

Welcome to the Alpha-OSK documentation. This directory contains comprehensive guides on the philosophy, technical innovations, and implementation details of the project.

---

## 📚 Documentation Index

### Philosophy & Vision

**[`../PHILOSOPHY.md`](../PHILOSOPHY.md)** - Core design philosophy and principles

Learn about the information-theoretic foundations, accessibility-first design, and the principles borrowed from the Dasher Project. This document explains **why** we make the decisions we make.

**Key topics:**
- Information-efficient design
- Accessibility first, always
- Continuous, natural interaction
- Adaptive learning
- Transparency and openness

**Best quotes:**
> *"Writing can be described as zooming in on an alphabetical library, steering as you go."* — Dasher Project

> *"We alter the SIZE of the shelf space devoted to each book in proportion to the probability of the corresponding text."*

---

### Technical Implementation

**[`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md)** - Dasher Project innovations

Detailed technical analysis of 8 major innovations from the **Dasher Project** (Cambridge University) and how they can be implemented in Alpha-OSK.

**[`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md)** - Mobile keyboard innovations

Technical approaches from **Gboard** and **SwiftKey** including fuzzy/spatial recognition, gesture typing, smart autocorrect, and personalized dictionaries.

**Dasher innovations (8 total):**
1. Alphabet System, 2. PPM Language Model, 3. Adaptive Learning, 4. Visual Probability Encoding, 5. Training Text System, 6. Direct Input Integration, 7. Switch Scanning Mode, 8. Performance Optimization

**Mobile keyboard innovations (6 total):**
1. Fuzzy/Spatial Recognition, 2. Gesture Typing, 3. Next-Word Prediction, 4. Smart Autocorrect, 5. Personalized Dictionary, 6. Multi-Language Support

**Implementation roadmap:**
- Phase 1: Quick wins (1-2 weeks)
- Phase 2: Core enhancements (2-4 weeks)
- Phase 3: Advanced features (1-2 months)
- Phase 4: Accessibility (2-4 weeks)

---

### Prediction System

**[`PREDICTION_OPTIONS.md`](PREDICTION_OPTIONS.md)** - Comparison of prediction approaches

Analysis of different prediction strategies and the rationale for Alpha-OSK's hybrid approach.

---

### Security

**[`SECURITY_AUDIT.md`](SECURITY_AUDIT.md)** - Security audit and recommendations

Comprehensive audit covering secrets management, network exposure, subprocess safety, file I/O, deserialization, dependencies, logging, privilege handling, and input validation. Includes hardening recommendations for production deployment.

---

## 🎯 Quick Reference

### For New Contributors

1. **Start here:** [`../PHILOSOPHY.md`](../PHILOSOPHY.md) - Understand the "why"
2. **Then read:** [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md) - Learn the "how"
3. **Check:** [`../LLM_ONBOARDING.md`](../LLM_ONBOARDING.md) - Quick project overview

### For Developers

**Key principles when coding:**
1. Does this reduce effort?
2. Is this accessible?
3. Is this intelligent?
4. Is this documented?
5. Is this open?

### For Researchers

Alpha-OSK builds on decades of HCI research, particularly:
- **Dasher Project** (Cambridge University) - Information-theoretic text entry
- **PPM algorithms** - Language modeling
- **Accessibility research** - Assistive technology design

See [`../PHILOSOPHY.md`](../PHILOSOPHY.md) for full references.

---

## 🔗 External Resources

### Dasher Project
- [Official Website](https://dasher.at)
- [How Dasher Works](https://dasher.at/docs/concepts/how-dasher-works/)
- [Research Publications](https://dasher.at/docs/research/publications/)
- [Development Guide](https://dasher.at/docs/development/)

### Related Projects
- [GNOME On-Board](https://launchpad.net/onboard) - Linux on-screen keyboard
- [Dasher GitHub](https://github.com/dasher-project/dasher) - Source code

---

## 📖 Document Relationships

```
PHILOSOPHY.md
    ↓ (inspires)
TECHNICAL_INNOVATIONS.md + MOBILE_KEYBOARD_INNOVATIONS.md
    ↓ (implements)
src/prediction/
    ├── ngram_predictor.py      # Word-level frequency prediction
    ├── ppm_predictor.py        # Character-level PPM (Dasher algorithm)
    ├── fuzzy_recognizer.py     # Spatial error correction + accessibility profiles
    ├── transformer_predictor.py # Optional LLM re-ranking (disabled by default)
    └── hybrid_predictor.py     # Orchestrates all predictors
```

**Philosophy → Technical Design → Implementation**

---

## 🚀 Implementation Status

### Completed ✅

**UI & Window:**
- **Title bar** with drag handle, minimize, close buttons
- **Resizable window** - Drag left or right edges; closed-form key sizing distributes width proportionally across all visible panels (main, nav, numpad) with sub-pixel rounding protection and dynamic minimum-width enforcement to prevent key clipping
- **Multi-monitor DPI** — Window size stays correct when dragged between monitors with different scaling; fixed via Qt `PassThrough` DPI rounding policy + `onScreenChanged` clamp in QML
- **Settings popup window** - All settings in a separate floating window (⚙ button), positioned to the right of the keyboard
- **Data-driven theme engine** — 5 built-in themes (Dark, Light, Blue, Green, Purple), defined as a single JS object map. Adding a theme requires one data entry, no code changes.
- **Window transparency** — Adjustable opacity slider (30%–100%) in settings. Background becomes transparent while keys remain fully opaque and readable.
- **Visual polish** — Ripple effect on key press (expands from touch point), smooth bounce animation, enhanced gradient depth, improved typography (DemiBold, Inter font stack).
- **Audio feedback** — Optional key click sounds via QSoundEffect. Toggle in settings. Gracefully degrades if QtMultimedia is unavailable.
- Modern prediction bar with improved readability

**Keyboard Layout:**
- **Data-driven layouts** — QWERTY, Dvorak, and Colemak defined as JSON files in `data/layouts/`. Switchable in settings, persisted across sessions.
- **Keyboard shortcuts** - Ctrl+C, Ctrl+V, Ctrl+Z, etc. work correctly
- Sticky modifiers (Shift, Ctrl, Alt, Win)
- Toggleable panels (Function keys, Navigation, Numpad)
- **Key Repeat** - Hold backspace/delete to repeat

**Prediction:**
- Hybrid prediction engine (n-gram + PPM + fuzzy)
- **PPM Language Model** - Character-level prediction (Dasher algorithm)
- **Fuzzy/Spatial Recognition** - Motor challenge support
- **Next-word Prediction** - Suggests words after clicking a prediction
- **Training Corpus** - Pre-loaded with common phrases
- **Smart Punctuation** - Auto-removes space before ? ! . , ; :

**Analytics:**
- **Session analytics dashboard** — Live stats in settings: words per minute, prediction hit rate, correction rate, top words, session duration. Polls every 2 seconds.

**Accessibility Profiles:**
Six profiles that adjust how aggressively the keyboard compensates for motor challenges:

| Profile | Key Target Size | Hold Delay | Autocorrect | Best For |
|---------|----------------|-----------|-------------|----------|
| Precise | Strict (0.5) | None | Off | Users with full motor control who want exact targeting |
| Normal | Standard (1.0) | None | On | Most users — balanced accuracy and assistance |
| Mild Tremor | Wider (1.5) | 100ms | On | Slight hand tremor or reduced finger precision |
| Moderate Tremor | Wide (2.0) | 200ms | On | Noticeable tremor, needs more forgiveness |
| Severe Tremor | Widest (2.5) | 300ms | On | Significant motor challenges, maximum assistance |
| Limited Mobility | Wide (2.0) | 150ms | On | Reduced range of motion, difficulty reaching distant keys |

Settings adjust `spatial_uncertainty` (how far from center a press is still counted), `confidence_threshold` (how sure the system must be before autocorrecting), `prediction_weight` (how much the predictor compensates), and `key_hold_delay` (minimum press duration to prevent accidental triggers).

**Settings Panel (popup window):**
- Keyboard layout selector (QWERTY / Dvorak / Colemak)
- Layout toggles: Function keys, Navigation keys, Numpad
- Suggestions: toggle, count (3–10)
- Accessibility profiles with descriptive labels
- Vocabulary packs (Medical, Programming, Academic, Gaming, Business)
- Theme selector (5 themes)
- Appearance: key click sound toggle, window opacity slider
- Session analytics dashboard
- Data management: save model, clear learned data
- Developer: Debug mode toggle
- Draggable, frameless, always-on-top; does not overlay the keyboard

### Architecture Decision: No LLM/AI Toggle in Settings ✂️
The transformer model (DistilGPT-2) toggle has been removed from the settings UI:
- **Reason:** Model not available by default; exposing the toggle causes confusion
- **Current prediction stack:** N-gram + PPM + Fuzzy — no AI dependency
- **Future:** AI toggle may return when a lightweight model ships with the app

### Current State: Prediction Quality 🔧
The prediction system is functional but still learning:
- **Next-word prediction works** - Suggests words after clicking a prediction
- **Training corpus is small** - Only 5,859 characters (needs expansion)
- **N-gram dominates** - Word-level predictions weighted 3x over character-level
- **PPM is learning** - Character-level model adapts as you type
- **Improving with use** - System learns from your selections

**To improve predictions:**
1. Use the keyboard regularly - it learns from your typing
2. Import text files via Prediction Settings (⚡ button)
3. Expand `data/training_corpus.txt` with domain-specific text

### Planned 📋
- Switch scanning mode
- AT-SPI direct input (bypass xdotool)
- Multi-language alphabet support
- Eye-tracking integration
- Gesture typing (swipe)

See [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md) for detailed roadmap.

---

## 💡 Contributing

When adding new features or documentation:

1. **Check philosophy alignment** - Does it fit our principles?
2. **Document the "why"** - Not just the "what"
3. **Include examples** - Code snippets, use cases
4. **Reference research** - Stand on shoulders of giants
5. **Think accessibility** - Will this work for all users?

---

## 📝 Documentation Standards

### For Technical Docs
- Include code examples
- Explain algorithms, not just APIs
- Show before/after comparisons
- Provide implementation roadmaps

### For Philosophy Docs
- Use quotes from research
- Explain principles with examples
- Connect to real user needs
- Reference academic sources

### For All Docs
- Write for future contributors
- Assume reader is intelligent but unfamiliar
- Use clear headings and structure
- Include visual aids when helpful

---

## 🎓 Learning Path

**New to assistive technology?**
1. Read Dasher's ["How It Works"](https://dasher.at/docs/concepts/how-dasher-works/)
2. Try Dasher yourself to understand the paradigm
3. Read our [`PHILOSOPHY.md`](../PHILOSOPHY.md)
4. Explore the codebase with context

**Want to contribute code?**
1. Understand the philosophy first
2. Read [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md)
3. Pick a Phase 1 task (quick wins)
4. Submit PR with clear documentation

**Interested in research?**
1. Review Dasher's [publications](https://dasher.at/docs/research/publications/)
2. Identify gaps or improvements
3. Propose experiments
4. Document findings

---

## 📊 Metrics & Goals

### Prediction Accuracy
- **Current:** ~70% top-3 accuracy
- **Target:** 85% top-3 accuracy (with PPM)
- **Stretch:** 90% with adaptive learning

### Performance
- **Current:** <10ms n-gram prediction
- **Target:** <50ms PPM prediction
- **Requirement:** <100ms total latency

### Accessibility
- **Current:** Mouse/touchscreen support
- **Target:** Switch scanning mode
- **Stretch:** Eye-tracking integration

---

## 🔍 Finding Information

**Looking for...**
- **Why we do things this way?** → [`../PHILOSOPHY.md`](../PHILOSOPHY.md)
- **How to implement feature X?** → [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md)
- **Quick project overview?** → [`../LLM_ONBOARDING.md`](../LLM_ONBOARDING.md)
- **Prediction comparison?** → [`PREDICTION_OPTIONS.md`](PREDICTION_OPTIONS.md)
- **Code architecture?** → [`../LLM_ONBOARDING.md`](../LLM_ONBOARDING.md#architecture)
- **Security posture?** → [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md)

---

## 🙏 Acknowledgments

This documentation builds on:
- **Dasher Project** - 25+ years of research and development
- **GNOME On-Board** - Pioneering Linux accessibility
- **The accessibility community** - Real users with real needs

Special thanks to:
- David MacKay and the Cambridge Inference Group
- All Dasher contributors and researchers
- The open-source accessibility community

---

## 📄 License

All documentation is licensed under the same terms as Alpha-OSK (see main LICENSE file).

When referencing Dasher research, please cite:
> Ward, D. J., Blackwell, A. F., & MacKay, D. J. (2002). *Dasher—a data entry interface using continuous gestures and language models.* UIST '00.

---

*Last updated: February 2026*

**Questions?** Open an issue or discussion on GitHub.
