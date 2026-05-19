# Dasher Project: Technical Innovations

This document explores technical approaches specifically from the **Dasher Project** (Cambridge University) and proposes creative implementations for Alpha-OSK.

> **Note:** For mobile keyboard innovations (Gboard, SwiftKey), see [`MOBILE_KEYBOARD_INNOVATIONS.md`](MOBILE_KEYBOARD_INNOVATIONS.md)

---

## Executive Summary

After analyzing Dasher's technical documentation, I've identified **8 major technical innovations** that could significantly enhance Alpha-OSK. These range from immediate wins (alphabet system, training text) to ambitious research directions (PPM language models, adaptive learning).

---

## 1. Alphabet System & Multi-Language Support

### What Dasher Does

Dasher uses XML-based alphabet files to support 60+ languages. Each alphabet file defines:
- Character sets and groupings
- Display vs. output characters (`d` vs `t` attributes)
- Color coding by group (`b` attribute for color)
- Training text associations
- Right-to-left support (`<orientation type="RL"/>`)
- Combining characters for complex scripts

**Example from Hebrew alphabet:**
```xml
<alphabet name="Ivrit / Hebrew">
  <orientation type="RL"/>
  <train>training_hebrew_IL.txt</train>
  <space d="□" t=" " b="9" />
  <group name="Hebrew letters" b="0">
    <s d="א" t="א" note="Alef"/>
    <s d="ב" t="ב" />
    <!-- more characters... -->
  </group>
</alphabet>
```

### How Alpha-OSK Could Implement This

**Immediate Implementation (Phase 1):**

1. **Create `data/alphabets/` directory structure:**
   ```
   data/
   ├── alphabets/
   │   ├── english.json
   │   ├── spanish.json
   │   ├── french.json
   │   └── hebrew.json
   └── training/
       ├── english_training.txt
       ├── spanish_training.txt
       └── ...
   ```

2. **JSON alphabet format (simpler than XML):**
   ```json
   {
     "name": "English (US)",
     "orientation": "LR",
     "training_file": "english_training.txt",
     "groups": [
       {
         "label": "Lowercase",
         "color": "#A8D0CB",
         "characters": [
           {"display": "a", "output": "a"},
           {"display": "b", "output": "b"}
         ]
       },
       {
         "label": "Uppercase",
         "color": "#D96A6A",
         "characters": [
           {"display": "A", "output": "A"}
         ]
       }
     ]
   }
   ```

3. **New Python module: `src/alphabet_manager.py`:**
   ```python
   class AlphabetManager(QObject):
       alphabetChanged = Signal(str)
       
       def __init__(self):
           self.current_alphabet = None
           self.available_alphabets = {}
           self.load_alphabets()
       
       def load_alphabets(self):
           """Load all alphabet files from data/alphabets/"""
           alphabet_dir = Path("data/alphabets")
           for file in alphabet_dir.glob("*.json"):
               with open(file) as f:
                   alphabet = json.load(f)
                   self.available_alphabets[alphabet['name']] = alphabet
       
       def switch_alphabet(self, name: str):
           """Switch to a different alphabet"""
           self.current_alphabet = self.available_alphabets[name]
           self.alphabetChanged.emit(name)
           # Reload prediction model with new training text
   ```

4. **QML dynamic keyboard generation:**
   - Instead of hardcoded QWERTY layout, generate keys from alphabet JSON
   - Color-code key groups based on alphabet definition
   - Support RTL layouts for Hebrew, Arabic, etc.

**Benefits:**
- Support multiple languages without code changes
- Users can create custom alphabets (programming symbols, emoji, etc.)
- Color coding improves visual navigation
- Training text per language improves predictions

---

## 2. PPM Language Model (Prediction by Partial Matching)

### What Dasher Does

Dasher uses a **PPM (Prediction by Partial Matching)** algorithm for character-level predictions. This is more sophisticated than simple n-grams:

**Key differences:**
- **Context length:** Looks back 5-10 characters, not just 2-3
- **Escape mechanism:** Handles unseen contexts gracefully
- **Hierarchical matching:** Falls back from long context to shorter if needed
- **Adaptive:** Learns from user's writing in real-time

**From Dasher's architecture:**
```cpp
class CPPMLanguageModel {
  void GetProbs(CLanguageModelContext* context, 
                std::vector<unsigned int>& probs) {
    // Look for longest matching context
    // If not found, escape to shorter context
    // Blend probabilities from multiple context lengths
  }
};
```

### How Alpha-OSK Could Implement This

**Current State:**
- Alpha-OSK uses simple n-gram (2-3 character lookback)
- No escape mechanism for unseen contexts
- No hierarchical blending

**Proposed Implementation:**

1. **New module: `src/prediction/ppm_predictor.py`:**
   ```python
   class PPMPredictor:
       """
       Prediction by Partial Matching language model.
       More sophisticated than n-grams.
       """
       def __init__(self, max_order=5):
           self.max_order = max_order  # Look back 5 chars
           self.context_tree = {}      # Hierarchical context storage
           self.escape_counts = {}     # For unseen contexts
       
       def predict(self, context: str) -> List[Tuple[str, float]]:
           """
           Get predictions using PPM algorithm.
           
           Algorithm:
           1. Try longest context (5 chars)
           2. If unseen, escape to 4 chars
           3. Blend probabilities from multiple levels
           4. Return weighted predictions
           """
           predictions = {}
           
           # Try contexts from longest to shortest
           for order in range(self.max_order, 0, -1):
               ctx = context[-order:] if len(context) >= order else context
               
               if ctx in self.context_tree:
                   # Found this context, get predictions
                   probs = self.context_tree[ctx]
                   weight = self._get_weight(order)
                   
                   for char, prob in probs.items():
                       predictions[char] = predictions.get(char, 0) + prob * weight
               else:
                   # Escape to shorter context
                   continue
           
           # Normalize and return top predictions
           return self._normalize_and_rank(predictions)
       
       def train_on_text(self, text: str):
           """Build context tree from training text"""
           for i in range(len(text)):
               for order in range(1, self.max_order + 1):
                   if i >= order:
                       context = text[i-order:i]
                       next_char = text[i]
                       
                       if context not in self.context_tree:
                           self.context_tree[context] = {}
                       
                       self.context_tree[context][next_char] = \
                           self.context_tree[context].get(next_char, 0) + 1
   ```

2. **Integration with hybrid predictor:**
   ```python
   # In hybrid_predictor.py
   self.ppm = PPMPredictor(max_order=5)
   self.ppm.train_on_text(training_text)
   
   # Use PPM for character predictions
   char_predictions = self.ppm.predict(current_context)
   
   # Use transformer for word-level re-ranking
   word_predictions = self.transformer.rerank(char_predictions)
   ```

**Benefits:**
- Better predictions with longer context
- Handles rare/unseen character combinations
- More accurate than simple n-grams
- Foundation for adaptive learning

---

## 3. Adaptive Learning System

### What Dasher Does

**From documentation:** *"Dasher can be trained on examples of any writing style, and it learns all the time, picking up your personal turns of phrase."*

Dasher continuously learns from user input:
- Builds personal language model
- Adapts to vocabulary and writing style
- Improves over time without manual retraining

### How Alpha-OSK Could Implement This

**Proposed Architecture:**

1. **Session logging:**
   ```python
   class AdaptiveLearner(QObject):
       """Learns from user's typing to improve predictions"""
       
       def __init__(self):
           self.session_text = []
           self.user_model_path = Path.home() / ".alpha-osk" / "user_model.json"
           self.load_user_model()
       
       def on_key_pressed(self, key: str, context: str):
           """Record user's actual choices"""
           self.session_text.append({
               'context': context,
               'chosen': key,
               'timestamp': time.time()
           })
       
       def on_session_end(self):
           """Update user model when session ends"""
           if len(self.session_text) > 50:  # Minimum data threshold
               self.update_user_model()
               self.save_user_model()
       
       def update_user_model(self):
           """Retrain on user's actual typing"""
           # Extract text from session
           typed_text = ''.join([t['chosen'] for t in self.session_text])
           
           # Update PPM model
           self.ppm.train_on_text(typed_text)
           
           # Update word frequencies
           words = typed_text.split()
           for word in words:
               self.word_freq[word] = self.word_freq.get(word, 0) + 1
   ```

2. **Personalized prediction blending:**
   ```python
   def get_predictions(self, context: str):
       # Blend base model + user model
       base_preds = self.base_model.predict(context)
       user_preds = self.user_model.predict(context)
       
       # Weight user model higher (they know their style)
       blended = {}
       for char, prob in base_preds:
           blended[char] = prob * 0.3  # 30% base model
       
       for char, prob in user_preds:
           blended[char] = blended.get(char, 0) + prob * 0.7  # 70% user model
       
       return sorted(blended.items(), key=lambda x: x[1], reverse=True)
   ```

**Benefits:**
- Keyboard gets better the more you use it
- Learns your vocabulary (technical terms, names, etc.)
- Adapts to your writing style
- No manual training required

---

## 4. Visual Probability Encoding

### What Dasher Does

**Core innovation:** *"We alter the SIZE of the shelf space devoted to each book in proportion to the probability of the corresponding text."*

Dasher makes probable characters **physically larger** in the interface. This is information-theoretic optimal design.

### How Alpha-OSK Could Implement This

While we can't change key sizes dynamically (would be confusing), we can use **visual encoding**:

**Option A: Prediction Bar Enhancement**
```python
# Current: Just show top 5 predictions
# Enhanced: Show with visual probability indicators

predictions = [
    ("the", 0.45),   # 45% probability
    ("this", 0.25),  # 25% probability
    ("that", 0.15),  # 15% probability
    ("then", 0.10),  # 10% probability
    ("they", 0.05)   # 5% probability
]
```

**QML visualization:**
```qml
// Prediction button size proportional to probability
Rectangle {
    width: baseWidth * (prediction.probability * 2)  // 2x multiplier
    height: 40
    
    // Color intensity based on probability
    color: Qt.rgba(0.66, 0.82, 0.80, prediction.probability)
    
    Text {
        text: prediction.word
        font.pixelSize: 12 + (prediction.probability * 8)  // Larger for more probable
    }
}
```

**Option B: Key Highlighting**
```qml
// In KeyButton.qml
Rectangle {
    // Highlight keys that start probable words
    border.width: isProbableNextChar ? 3 : 1
    border.color: isProbableNextChar ? "#F4E47E" : "#A8D0CB"
    
    // Glow effect for high-probability characters
    layer.enabled: probability > 0.3
    layer.effect: Glow {
        radius: 8
        samples: 16
        color: "#F4E47E"
    }
}
```

**Benefits:**
- Users see probability visually
- Reduces cognitive load
- Faster decision making
- Information-theoretic optimal

---

## 5. Training Text System

### What Dasher Does

Dasher requires **300KB+ of training text** per language to build accurate probability models. They provide:
- Pre-built training texts for 60+ languages
- Instructions for creating custom training texts
- Automatic loading based on alphabet selection

### How Alpha-OSK Could Implement This

**Proposed System:**

1. **Training text collection:**
   ```
   data/training/
   ├── english_general.txt        # General English (Project Gutenberg)
   ├── english_technical.txt      # Programming/technical
   ├── english_medical.txt        # Medical terminology
   ├── spanish_general.txt
   └── user_custom.txt            # User's personal writing
   ```

2. **Training text manager:**
   ```python
   class TrainingTextManager:
       """Manages training corpora for prediction models"""
       
       def load_training_text(self, language: str, domain: str = "general"):
           """Load appropriate training text"""
           filename = f"{language}_{domain}.txt"
           path = Path("data/training") / filename
           
           if not path.exists():
               # Fall back to general
               path = Path("data/training") / f"{language}_general.txt"
           
           with open(path, encoding='utf-8') as f:
               return f.read()
       
       def add_user_text(self, text: str):
           """Add user's writing to training corpus"""
           user_path = Path.home() / ".alpha-osk" / "user_training.txt"
           
           with open(user_path, 'a', encoding='utf-8') as f:
               f.write(text + "\n")
           
           # Retrain model
           self.retrain_model()
   ```

3. **Domain-specific models:**
   ```python
   # User can select domain for better predictions
   domains = {
       'general': 'General writing',
       'technical': 'Programming & technical',
       'medical': 'Medical terminology',
       'legal': 'Legal documents',
       'creative': 'Creative writing'
   }
   
   # Load appropriate training text
   if user.domain == 'technical':
       training_text = load_training_text('english', 'technical')
       # Now predictions include: def, class, import, etc.
   ```

**Benefits:**
- Better predictions from larger corpus
- Domain-specific vocabulary support
- Easy to add new languages
- Users can contribute training texts

---

## 6. Direct Input Integration (AT-SPI / Accessibility API)

### What Dasher Does

Dasher can send text **directly to applications** using:
- **Linux:** AT-SPI (Assistive Technology Service Provider Interface)
- **Windows:** SendInput API
- **macOS:** Accessibility API

This is more reliable than xdotool/ydotool for some applications.

### How Alpha-OSK Could Implement This

**Current State:**
- Uses xdotool (X11) or ydotool (Wayland)
- Works but has limitations with some apps

**Proposed Enhancement:**

1. **AT-SPI integration (Linux):**
   ```python
   import pyatspi
   
   class DirectInputManager:
       """Send text directly to focused application via AT-SPI"""
       
       def __init__(self):
           self.use_atspi = self.check_atspi_available()
       
       def send_text(self, text: str):
           """Send text using best available method"""
           if self.use_atspi:
               self.send_via_atspi(text)
           else:
               # Fall back to xdotool/ydotool
               self.send_via_xdotool(text)
       
       def send_via_atspi(self, text: str):
           """Use AT-SPI for direct text insertion"""
           desktop = pyatspi.Registry.getDesktop(0)
           focused = self.get_focused_accessible(desktop)
           
           if focused and focused.queryEditableText():
               # Direct text insertion
               editable = focused.queryEditableText()
               pos = editable.caretOffset
               editable.insertText(pos, text, len(text))
           else:
               # Fall back
               self.send_via_xdotool(text)
   ```

2. **Smart method selection:**
   ```python
   # Test which method works best for current app
   app_name = get_focused_app_name()
   
   if app_name in ['firefox', 'chrome', 'code']:
       # These work better with AT-SPI
       use_method = 'atspi'
   elif app_name in ['terminal', 'konsole']:
       # Terminals need xdotool
       use_method = 'xdotool'
   else:
       # Try AT-SPI first, fall back if fails
       use_method = 'auto'
   ```

**Benefits:**
- More reliable text entry
- Works with more applications
- Better integration with accessibility stack
- Faster (no subprocess overhead)

---

## 7. Button/Switch Mode for Single-Switch Users

### What Dasher Does

**From documentation:** *"For users who can only use a single switch, Dasher offers button/switch modes. The interface scans through options and the user selects by pressing the button."*

This is crucial for users with very limited motor control.

### How Alpha-OSK Could Implement This

**Proposed Feature:**

1. **Scanning mode:**
   ```python
   class ScanningMode(QObject):
       """Single-switch scanning for users with limited motor control"""
       
       scanPositionChanged = Signal(int, int)  # row, col
       
       def __init__(self, scan_speed_ms=1000):
           self.scan_speed = scan_speed_ms
           self.current_row = 0
           self.current_col = 0
           self.scanning_rows = True  # First scan rows, then columns
           self.timer = QTimer()
           self.timer.timeout.connect(self.advance_scan)
       
       def start_scanning(self):
           """Start automatic scanning"""
           self.timer.start(self.scan_speed)
       
       def advance_scan(self):
           """Move highlight to next position"""
           if self.scanning_rows:
               self.current_row = (self.current_row + 1) % num_rows
           else:
               self.current_col = (self.current_col + 1) % num_cols
           
           self.scanPositionChanged.emit(self.current_row, self.current_col)
       
       def on_switch_press(self):
           """User pressed their single switch"""
           if self.scanning_rows:
               # Selected a row, now scan columns
               self.scanning_rows = False
               self.current_col = 0
           else:
               # Selected a key!
               key = self.get_key_at(self.current_row, self.current_col)
               self.type_key(key)
               
               # Reset to row scanning
               self.scanning_rows = True
               self.current_row = 0
   ```

2. **QML visual feedback:**
   ```qml
   // Highlight scanned row/column
   Rectangle {
       id: scanHighlight
       color: "transparent"
       border.color: "#F4E47E"
       border.width: 4
       
       // Animate to current scan position
       Behavior on x { NumberAnimation { duration: 200 } }
       Behavior on y { NumberAnimation { duration: 200 } }
   }
   ```

3. **Configurable scanning:**
   ```python
   # Settings
   scan_modes = {
       'row_column': 'Scan rows, then columns',
       'linear': 'Scan all keys in order',
       'group': 'Scan groups, then keys',
       'prediction_first': 'Scan predictions before keyboard'
   }
   
   scan_speeds = {
       'slow': 2000,      # 2 seconds per position
       'medium': 1000,    # 1 second
       'fast': 500,       # 0.5 seconds
       'custom': user_value
   }
   ```

**Benefits:**
- Accessible to single-switch users
- Configurable for different abilities
- Prediction-first mode for efficiency
- Critical accessibility feature

---

## 8. Performance Optimization: Frame-Based Prediction

### What Dasher Does

Dasher runs at **30-60 FPS** with continuous prediction updates. Key optimizations:
- Frame-based rendering
- Prediction caching
- Incremental updates
- Background computation

**From architecture:**
```cpp
void CDasherCore::NewFrame() {
    // Called 30-60 times per second
    // Update predictions incrementally
    // Cache unchanged results
}
```

### How Alpha-OSK Could Implement This

**Current State:**
- Predictions computed on every keystroke
- No caching
- Blocking UI during prediction

**Proposed Optimization:**

1. **Prediction caching:**
   ```python
   class CachedPredictor:
       """Cache predictions to avoid redundant computation"""
       
       def __init__(self):
           self.cache = {}
           self.cache_hits = 0
           self.cache_misses = 0
       
       def get_predictions(self, context: str):
           """Get predictions with caching"""
           # Use last N characters as cache key
           cache_key = context[-10:] if len(context) > 10 else context
           
           if cache_key in self.cache:
               self.cache_hits += 1
               return self.cache[cache_key]
           
           # Cache miss - compute predictions
           self.cache_misses += 1
           predictions = self._compute_predictions(context)
           
           # Cache result
           self.cache[cache_key] = predictions
           
           # Limit cache size
           if len(self.cache) > 1000:
               # Remove oldest entries
               self.cache.pop(next(iter(self.cache)))
           
           return predictions
   ```

2. **Background prediction:**
   ```python
   class BackgroundPredictor(QThread):
       """Compute predictions in background thread"""
       
       predictionsReady = Signal(list)
       
       def __init__(self):
           super().__init__()
           self.context_queue = Queue()
       
       def request_predictions(self, context: str):
           """Queue prediction request"""
           self.context_queue.put(context)
       
       def run(self):
           """Background thread loop"""
           while True:
               context = self.context_queue.get()
               
               # Compute predictions (doesn't block UI)
               predictions = self.predictor.predict(context)
               
               # Emit signal to UI thread
               self.predictionsReady.emit(predictions)
   ```

3. **Incremental updates:**
   ```python
   def on_text_changed(self, new_text: str):
       """Only update if context actually changed"""
       # Extract prediction context (last 20 chars)
       new_context = new_text[-20:]
       
       if new_context == self.last_context:
           # No change, skip prediction
           return
       
       self.last_context = new_context
       
       # Request new predictions in background
       self.background_predictor.request_predictions(new_context)
   ```

**Benefits:**
- Faster UI responsiveness
- No blocking during prediction
- Reduced CPU usage
- Better battery life on laptops

---

## Implementation Roadmap

### Phase 1: Quick Wins (1-2 weeks)
1. ✅ **Alphabet system** - JSON-based multi-language support
2. ✅ **Training text** - Load corpus for better predictions
3. ✅ **Prediction caching** - Simple cache for performance

### Phase 2: Core Enhancements (2-4 weeks)
4. ✅ **PPM language model** - Replace simple n-grams
5. ✅ **Visual probability encoding** - Better prediction UI
6. ✅ **Background prediction** - Non-blocking computation

### Phase 3: Advanced Features (1-2 months)
7. ✅ **Adaptive learning** - Learn from user's typing
8. ✅ **AT-SPI integration** - Direct input method
9. ✅ **Domain-specific models** - Technical, medical, etc.

### Phase 4: Accessibility (2-4 weeks)
10. ✅ **Switch scanning mode** - Single-switch support
11. ✅ **Configurable scanning** - Multiple scan patterns
12. ✅ **Eye-tracking preparation** - Architecture for future eye-tracking

---

## Technical Debt to Address

Based on Dasher's architecture, Alpha-OSK should consider:

1. **Separation of concerns:**
   - Core prediction engine (platform-independent)
   - UI layer (QML)
   - Input synthesis (xdotool/atspi)
   
2. **Testing infrastructure:**
   - Unit tests for prediction accuracy
   - Performance benchmarks
   - Accessibility testing

3. **Configuration system:**
   - User preferences persistence
   - Per-language settings
   - Accessibility profiles

---

## Research Opportunities

Inspired by Dasher's research directions:

1. **Hybrid Speech-Keyboard:**
   - Use speech recognition for word-level input
   - Use keyboard for corrections
   - Combine probabilities from both

2. **Context-Aware Prediction:**
   - Detect current application (code editor, email, etc.)
   - Load appropriate domain model
   - Switch automatically

3. **Collaborative Learning:**
   - Anonymized usage data
   - Community-trained models
   - Privacy-preserving federated learning

---

## Conclusion

Dasher's 25+ years of research provides a wealth of proven techniques. The most impactful for Alpha-OSK are:

**Immediate value:**
1. Alphabet system (multi-language)
2. Training text (better predictions)
3. PPM language model (smarter predictions)

**High impact:**
4. Adaptive learning (personalization)
5. Visual probability encoding (UX)
6. Switch scanning (accessibility)

**Long-term:**
7. AT-SPI integration (reliability)
8. Domain-specific models (specialized use)

These innovations align with Alpha-OSK's philosophy of **information-efficient, accessible, adaptive design**.
