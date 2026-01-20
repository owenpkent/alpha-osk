# Alpha-OSK

**AI-Powered On-Screen Keyboard for Windows**

An accessible on-screen keyboard designed for users with motor disabilities, featuring AI-enabled predictive text, voice dictation, and federated learning for personalized adaptation.

---

## Status

**Phase:** 🚧 Planning / Early Development

| Area | Status |
|------|--------|
| Core Keyboard | ⏳ Planned |
| AI Prediction | ⏳ Planned |
| Voice Dictation | ⏳ Planned |
| Federated Learning | ⏳ Planned |
| Collaboration Features | ⏳ Planned |
| Dashboard | ✅ Complete |

---

## Vision

Current Windows on-screen keyboards (including the built-in OSK) lack modern AI capabilities. Alpha-OSK aims to be what GNOME On-Board is for Linux—but better, with:

- **Intelligent word prediction** that learns your vocabulary
- **Voice dictation** with low-latency transcription
- **Federated learning** for privacy-preserving personalization
- **Collaborative dictionaries** shared across disability communities
- **Adaptive layouts** optimized for limited mobility

---

## Key Features (Planned)

### 🧠 AI-Powered Prediction
- Context-aware word and phrase completion
- Personal vocabulary learning (on-device)
- Specialized dictionaries (medical terms, assistive tech jargon)

### 🎤 Voice Dictation
- Real-time speech-to-text (Whisper-based)
- Voice commands for navigation and editing
- Hybrid input: switch between voice and touch seamlessly

### 🔒 Federated Learning
- Model improves without sending raw data to servers
- Privacy-first personalization
- Opt-in aggregated learning across users

### 🤝 Collaboration
- Shared word lists and abbreviation expansions
- Community-contributed accessibility profiles
- Sync settings across devices

### 🎨 Adaptive Layout
- Customizable key sizes and spacing
- Dwell-click and scanning support
- High-contrast and low-vision themes

---

## Inspiration

- **GNOME On-Board (Linux)** — Great customization, but limited AI
- **Windows OSK** — Functional but dated, no learning
- **iOS/Android keyboards** — Excellent prediction, not accessible enough

Alpha-OSK combines the best of accessibility-first design with modern AI.

---

## Tech Stack

- **Language:** Python 3.11+
- **UI Framework:** PySide6 (Qt)
- **AI/ML:** 
  - Transformers (Hugging Face) for prediction
  - Whisper (OpenAI) for voice
  - Flower for federated learning
- **Dashboard:** HTML/CSS (served via Python)

---

## Quick Start

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

The dashboard opens at `http://localhost:8080`

---

## Project Structure

```
alpha-osk/
├── README.md              # This file
├── TODO.md                # Task tracking
├── DESIGN.md              # Layout and UX specifications
├── run.py                 # Dashboard launcher
├── requirements.txt       # Python dependencies
├── templates/
│   └── dashboard.html     # Project dashboard
├── src/                   # Source code (coming soon)
│   ├── keyboard/          # Core keyboard logic
│   ├── prediction/        # AI prediction engine
│   ├── voice/             # Voice dictation
│   └── federation/        # Federated learning client
└── docs/                  # Extended documentation
```

---

## License

MIT License — Free for personal and commercial use.

---

## Constellation

This repo is tracked by [Constellation](https://github.com/owenpkent/constellation) for cross-project visibility.
