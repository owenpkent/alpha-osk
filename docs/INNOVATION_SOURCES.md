# Innovation Sources Overview

Alpha-OSK draws technical inspiration from multiple sources. This document provides an overview and guides you to the right detailed documentation.

---

## 📚 Documentation by Source

### 1. Dasher Project (Cambridge University)
**Focus:** Research-backed, information-theoretic text entry

**Document:** [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md)

**Key Innovations:**
- **PPM Language Model** - Prediction by Partial Matching (5-10 char context)
- **Alphabet System** - Multi-language support via XML/JSON configs
- **Adaptive Learning** - Learns from user's typing in real-time
- **Visual Probability Encoding** - Size/color based on probability
- **Training Text System** - 300KB+ corpora for accurate predictions
- **AT-SPI Integration** - Direct input via accessibility APIs
- **Switch Scanning Mode** - Single-switch accessibility
- **Performance Optimization** - Caching, background threads

**Best for:** Accessibility features, language modeling, research-backed approaches

---

### 2. Mobile Keyboards (Gboard, SwiftKey)
**Focus:** Familiar QWERTY + intelligent assistance

**Document:** [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md)

**Key Innovations:**
- **Fuzzy/Spatial Recognition** - Corrects typos based on key proximity
- **Gesture Typing** - Swipe across keys instead of tapping
- **Next-Word Prediction** - Context-aware word suggestions
- **Smart Autocorrect** - Context-aware corrections
- **Personalized Dictionary** - Learns your vocabulary automatically
- **Multi-Language Support** - Seamless language switching

**Best for:** User experience, error correction, mobile-inspired features

---

### 3. GNOME On-Board
**Focus:** Linux desktop on-screen keyboard

**Document:** *(To be created if needed)*

**Key Features:**
- Desktop integration
- Layout customization
- Accessibility settings
- Auto-show/hide

**Best for:** Linux-specific integration, desktop UX patterns

---

## 🎯 Which Document Should I Read?

### If you want to implement...

**Better predictions:**
- → [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md) - PPM model, training text
- → [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md) - Next-word prediction

**Error correction:**
- → [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md) - Fuzzy recognition, autocorrect

**Multi-language support:**
- → [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md) - Alphabet system
- → [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md) - Multi-language manager

**Accessibility features:**
- → [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md) - Switch scanning, AT-SPI

**Learning/Personalization:**
- → [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md) - Adaptive learning
- → [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md) - Personal dictionary

**Alternative input methods:**
- → [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md) - Eye-tracking prep, switch mode
- → [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md) - Gesture typing

---

## 🔄 How These Sources Complement Each Other

### Dasher: Research Foundation
- **Strength:** Proven algorithms, accessibility focus, information theory
- **Approach:** Novel paradigm (zooming interface)
- **Best practices:** Language modeling, adaptive learning, accessibility

### Mobile Keyboards: UX Patterns
- **Strength:** Familiar interface, user-tested features, mass adoption
- **Approach:** Traditional QWERTY + AI assistance
- **Best practices:** Error correction, personalization, smooth UX

### Alpha-OSK: Hybrid Approach
**We take the best of both:**
- **Interface:** Familiar QWERTY (like mobile keyboards)
- **Prediction:** PPM + transformers (like Dasher's research)
- **Accessibility:** Switch scanning, AT-SPI (like Dasher)
- **UX:** Fuzzy recognition, autocorrect (like mobile keyboards)

---

## 📊 Feature Comparison

| Feature | Dasher | Gboard/SwiftKey | Alpha-OSK |
|---------|--------|-----------------|-----------|
| **Interface** | Zooming library | QWERTY grid | QWERTY grid |
| **Prediction Model** | PPM | Neural + n-gram | Hybrid (PPM + transformer) |
| **Error Correction** | Implicit (spatial) | Fuzzy + autocorrect | **Implementing fuzzy** |
| **Learning** | Adaptive PPM | Personal dict + neural | **Both approaches** |
| **Accessibility** | Eye-tracking, switches | Touch optimization | Mouse, switches, future eye-tracking |
| **Multi-language** | 60+ alphabets | 100+ languages | **Implementing alphabet system** |
| **Platform** | Cross-platform | Mobile-first | Linux desktop |

---

## 🚀 Implementation Priority

### Phase 1: Foundation (Dasher-inspired)
1. Alphabet system for multi-language
2. Training text loading
3. PPM language model

### Phase 2: UX (Mobile-inspired)
4. Fuzzy/spatial recognition
5. Smart autocorrect
6. Personal dictionary

### Phase 3: Advanced (Both sources)
7. Adaptive learning (Dasher)
8. Enhanced next-word prediction (Mobile)
9. Gesture typing (Mobile, experimental)

### Phase 4: Accessibility (Dasher-focused)
10. Switch scanning mode
11. AT-SPI integration
12. Eye-tracking preparation

---

## 📖 Reading Order for New Contributors

**Recommended path:**

1. **Start:** [`PHILOSOPHY.md`](PHILOSOPHY.md)
   - Understand the "why" behind design decisions
   - Learn core principles from Dasher research

2. **Then:** [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md)
   - Familiar features you've likely used
   - Easier to understand and implement
   - Quick wins for UX improvement

3. **Next:** [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md)
   - More advanced algorithms
   - Research-backed approaches
   - Accessibility-focused features

4. **Finally:** [`README.md`](README.md)
   - Navigation and quick reference
   - Implementation status
   - Contributing guidelines

---

## 🔍 Quick Reference

### Fuzzy Recognition / Error Correction
- **Source:** Mobile keyboards (Gboard)
- **Document:** [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md#1-fuzzyspatial-recognition)
- **Priority:** High (critical for motor challenges)

### PPM Language Model
- **Source:** Dasher Project
- **Document:** [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md#2-ppm-language-model-prediction-by-partial-matching)
- **Priority:** High (better predictions)

### Adaptive Learning
- **Source:** Dasher Project
- **Document:** [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md#3-adaptive-learning-system)
- **Priority:** Medium (personalization)

### Gesture Typing
- **Source:** Mobile keyboards (Gboard)
- **Document:** [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md#2-gesture-typing-swipeglide)
- **Priority:** Low (experimental)

### Switch Scanning
- **Source:** Dasher Project
- **Document:** [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md#7-buttonswitch-mode-for-single-switch-users)
- **Priority:** High (accessibility)

---

## 💡 Key Insights

### From Dasher Research
> *"We alter the SIZE of the shelf space devoted to each book in proportion to the probability of the corresponding text."*

**Lesson:** Use probability to guide interface design. Make probable choices easier.

### From Mobile Keyboards
> *"When you type a letter, consider nearby keys as possible alternatives."*

**Lesson:** Account for spatial uncertainty, especially for users with motor challenges.

### For Alpha-OSK
> *"Familiar interface + intelligent assistance = accessible power"*

**Approach:** QWERTY layout everyone knows + AI that helps everyone succeed.

---

## 🎓 Research References

### Dasher
- Ward, D. J., Blackwell, A. F., & MacKay, D. J. (2002). *Dasher—a data entry interface using continuous gestures and language models.* UIST '00.
- [Dasher Official Website](https://dasher.at)
- [Dasher Research Publications](https://dasher.at/docs/research/publications/)

### Mobile Keyboards
- [Gboard AI Blog](https://ai.googleblog.com/search/label/Gboard)
- [SwiftKey Neural Networks](https://www.microsoft.com/en-us/research/project/swiftkey/)
- Touch keyboard spatial uncertainty research

### General HCI
- Information theory in interface design
- Assistive technology best practices
- Predictive text entry systems

---

## 🤝 Contributing

When proposing new features, consider:

1. **Which source inspired it?** (Dasher, mobile, or original idea)
2. **Does it fit our philosophy?** (See [`PHILOSOPHY.md`](PHILOSOPHY.md))
3. **Which document should it go in?** (Dasher vs mobile innovations)
4. **What's the implementation complexity?** (Quick win vs long-term)
5. **Who benefits most?** (All users vs specific accessibility needs)

---

## 📝 Document Maintenance

### When to update which document:

**[`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md):**
- Dasher-specific algorithms
- Research-backed approaches
- Accessibility features from Dasher
- PPM, adaptive learning, switch scanning

**[`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md):**
- Gboard/SwiftKey features
- UX patterns from mobile
- Error correction, fuzzy matching
- Gesture typing, autocorrect

**[`INNOVATION_SOURCES.md`](INNOVATION_SOURCES.md)** (this file):
- Overview and navigation
- Cross-references
- Comparison tables
- Reading guides

---

*Last updated: February 2026*

**Questions?** Check the specific innovation documents or open a GitHub discussion.
