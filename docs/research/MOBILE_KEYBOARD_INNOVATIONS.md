# Mobile Keyboard Innovations (Gboard & SwiftKey)

Technical innovations from modern mobile keyboards and how they can be implemented in Alpha-OSK.

---

## Executive Summary

Mobile keyboards like **Gboard** (Google) and **SwiftKey** (Microsoft) have pioneered several innovations that desktop on-screen keyboards haven't widely adopted. The most impactful for Alpha-OSK are:

1. **Fuzzy/Spatial Recognition** - Corrects typos based on key proximity
2. **Gesture Typing** - Swipe across keys instead of tapping
3. **Next-Word Prediction** - Context-aware word suggestions
4. **Personalized Dictionary** - Learns your vocabulary
5. **Multi-Language Support** - Switch languages seamlessly
6. **Smart Autocorrect** - Context-aware corrections

Unlike Dasher (research-focused, novel paradigm), mobile keyboards focus on **familiar QWERTY + intelligent assistance** - exactly Alpha-OSK's approach.

---

## 1. Fuzzy/Spatial Recognition

### What Gboard Does

**The Innovation:** When you type a letter, Gboard considers **nearby keys** as possible alternatives. If you type "thr" but meant "the", it recognizes that 'r' and 'e' are adjacent and suggests "the".

**How it works:**
1. **Spatial model** - Each key has a probability distribution based on touch position
2. **Candidate generation** - For input "thr", generate candidates: "thr", "the", "thy", "tht"
3. **Language model ranking** - "the" is most probable in English
4. **Confidence scoring** - If "the" score >> "thr" score, auto-correct

**Key insight:** Physical keyboards have discrete keys. Touch keyboards have **spatial uncertainty** - your finger covers multiple keys. Gboard models this uncertainty.

### How Alpha-OSK Could Implement This

**Challenge:** Mouse/touchscreen clicks are more precise than finger touches. But users with motor control issues may have **spatial uncertainty** too.

**Proposed Implementation:**

#### 1. Spatial Key Model

```python
class SpatialKeyModel:
    """
    Model spatial uncertainty for key presses.
    Useful for users with motor control challenges.
    """
    
    def __init__(self):
        # Define keyboard layout with coordinates
        self.key_positions = {
            'q': (0, 0), 'w': (1, 0), 'e': (2, 0), # etc.
            'a': (0, 1), 's': (1, 1), 'd': (2, 1),
            'z': (0, 2), 'x': (1, 2), 'c': (2, 2),
        }
        
        # Spatial uncertainty radius (configurable)
        self.uncertainty_radius = 1.5  # Key widths
    
    def get_key_probabilities(self, clicked_key: str) -> Dict[str, float]:
        """
        Given a clicked key, return probability distribution
        over nearby keys based on spatial proximity.
        """
        clicked_pos = self.key_positions[clicked_key]
        probabilities = {}
        
        for key, pos in self.key_positions.items():
            # Calculate distance
            distance = self._euclidean_distance(clicked_pos, pos)
            
            # Probability decreases with distance (Gaussian)
            if distance <= self.uncertainty_radius:
                prob = math.exp(-distance**2 / (2 * 0.5**2))  # σ=0.5
                probabilities[key] = prob
        
        # Normalize
        total = sum(probabilities.values())
        return {k: v/total for k, v in probabilities.items()}
    
    def _euclidean_distance(self, pos1, pos2):
        return math.sqrt((pos1[0]-pos2[0])**2 + (pos1[1]-pos2[1])**2)
```

#### 2. Fuzzy Word Candidates

```python
class FuzzyWordGenerator:
    """Generate word candidates considering spatial uncertainty"""
    
    def __init__(self, spatial_model: SpatialKeyModel, dictionary: Set[str]):
        self.spatial_model = spatial_model
        self.dictionary = dictionary
    
    def generate_candidates(self, typed_sequence: str) -> List[Tuple[str, float]]:
        """
        Generate candidate words considering key proximity.
        
        Example:
        Input: "thr"
        Output: [("the", 0.85), ("thr", 0.10), ("thy", 0.05)]
        """
        candidates = []
        
        # Generate all possible interpretations
        possible_sequences = self._generate_fuzzy_sequences(typed_sequence)
        
        # Check which are real words
        for sequence, prob in possible_sequences:
            if sequence in self.dictionary:
                candidates.append((sequence, prob))
        
        # Sort by probability
        return sorted(candidates, key=lambda x: x[1], reverse=True)
    
    def _generate_fuzzy_sequences(self, typed: str) -> List[Tuple[str, float]]:
        """
        Generate all possible sequences considering spatial uncertainty.
        
        For "thr":
        - t could be t, r, y, g, f (nearby keys)
        - h could be h, g, j, y, n
        - r could be r, e, t, f, d
        
        Combine probabilities to get sequence likelihood.
        """
        if not typed:
            return [("", 1.0)]
        
        # Recursive generation
        first_char = typed[0]
        rest = typed[1:]
        
        # Get possible keys for first character
        first_probs = self.spatial_model.get_key_probabilities(first_char)
        
        # Get sequences for rest
        rest_sequences = self._generate_fuzzy_sequences(rest)
        
        # Combine
        result = []
        for char, char_prob in first_probs.items():
            for rest_seq, rest_prob in rest_sequences:
                combined_seq = char + rest_seq
                combined_prob = char_prob * rest_prob
                
                # Only keep reasonably probable sequences
                if combined_prob > 0.01:
                    result.append((combined_seq, combined_prob))
        
        return result
```

#### 3. Integration with Prediction System

```python
class FuzzyPredictor:
    """Combine fuzzy recognition with language model"""
    
    def __init__(self):
        self.spatial_model = SpatialKeyModel()
        self.fuzzy_generator = FuzzyWordGenerator(
            self.spatial_model, 
            self.load_dictionary()
        )
        self.language_model = LanguageModel()
    
    def predict(self, typed_text: str, context: str) -> List[Tuple[str, float]]:
        """
        Predict words considering both:
        1. Spatial uncertainty (fuzzy matching)
        2. Language model (context)
        """
        # Get last word being typed
        words = typed_text.split()
        current_word = words[-1] if words else ""
        
        # Generate fuzzy candidates
        fuzzy_candidates = self.fuzzy_generator.generate_candidates(current_word)
        
        # Re-rank using language model
        final_predictions = []
        for word, spatial_prob in fuzzy_candidates:
            # Get language model probability
            lm_prob = self.language_model.word_probability(word, context)
            
            # Combine probabilities (weighted)
            combined_prob = (spatial_prob * 0.3) + (lm_prob * 0.7)
            
            final_predictions.append((word, combined_prob))
        
        return sorted(final_predictions, key=lambda x: x[1], reverse=True)[:5]
```

#### 4. Configurable Uncertainty

```python
# Settings for different user needs
uncertainty_profiles = {
    'precise': {
        'radius': 0.5,      # Very precise clicks
        'weight': 0.1       # Low spatial uncertainty weight
    },
    'normal': {
        'radius': 1.0,
        'weight': 0.3
    },
    'motor_challenge': {
        'radius': 2.0,      # Higher uncertainty
        'weight': 0.5       # Trust spatial model more
    },
    'tremor': {
        'radius': 2.5,      # Very high uncertainty
        'weight': 0.6
    }
}

# User can select profile based on their motor control
```

**Benefits:**
- Corrects typos automatically
- Especially helpful for users with motor control challenges
- Reduces frustration from misclicks
- Configurable based on user ability

---

## 2. Gesture Typing (Swipe/Glide)

### What Gboard Does

**The Innovation:** Instead of tapping individual keys, swipe your finger across the keyboard in the shape of the word. Gboard decodes the gesture into the intended word.

**Example:** To type "hello", swipe h→e→l→l→o in one continuous motion.

**How it works:**
1. **Path tracking** - Record x,y coordinates of swipe
2. **Key sequence extraction** - Determine which keys the path crosses
3. **Fuzzy matching** - Path might not be perfect, consider nearby keys
4. **Word decoding** - Match key sequence to dictionary words
5. **Language model** - Rank candidates by probability

### How Alpha-OSK Could Implement This

**Adaptation for desktop:** Mouse drag or touchscreen swipe.

**Proposed Implementation:**

#### 1. Gesture Path Tracking

```python
class GestureTracker(QObject):
    """Track mouse/touch gestures across keyboard"""
    
    gestureCompleted = Signal(list)  # List of (x, y) points
    
    def __init__(self):
        self.is_tracking = False
        self.path_points = []
        self.start_time = None
    
    def on_mouse_press(self, x: int, y: int):
        """Start tracking gesture"""
        self.is_tracking = True
        self.path_points = [(x, y)]
        self.start_time = time.time()
    
    def on_mouse_move(self, x: int, y: int):
        """Record path points"""
        if self.is_tracking:
            self.path_points.append((x, y))
    
    def on_mouse_release(self, x: int, y: int):
        """Complete gesture and decode"""
        if self.is_tracking:
            self.path_points.append((x, y))
            self.is_tracking = False
            
            # Only process if gesture took reasonable time
            duration = time.time() - self.start_time
            if 0.2 < duration < 3.0:  # 200ms to 3s
                self.gestureCompleted.emit(self.path_points)
            
            self.path_points = []
```

#### 2. Path to Key Sequence

```python
class PathDecoder:
    """Convert gesture path to key sequence"""
    
    def __init__(self, key_positions: Dict[str, Tuple[int, int]]):
        self.key_positions = key_positions
        self.key_bounds = self._compute_key_bounds()
    
    def decode_path(self, path: List[Tuple[int, int]]) -> List[str]:
        """
        Convert path coordinates to sequence of keys.
        
        Returns list of keys the path crossed.
        """
        keys_crossed = []
        last_key = None
        
        for x, y in path:
            # Find which key this point is over
            current_key = self._point_to_key(x, y)
            
            if current_key and current_key != last_key:
                keys_crossed.append(current_key)
                last_key = current_key
        
        return keys_crossed
    
    def _point_to_key(self, x: int, y: int) -> Optional[str]:
        """Determine which key a point is over"""
        for key, bounds in self.key_bounds.items():
            if (bounds['x_min'] <= x <= bounds['x_max'] and
                bounds['y_min'] <= y <= bounds['y_max']):
                return key
        return None
```

#### 3. Gesture Word Matching

```python
class GestureWordMatcher:
    """Match gesture key sequence to words"""
    
    def __init__(self, dictionary: Set[str]):
        self.dictionary = dictionary
        # Build index: key_sequence -> words
        self.sequence_index = self._build_sequence_index()
    
    def match_gesture(self, key_sequence: List[str]) -> List[Tuple[str, float]]:
        """
        Find words matching the gesture key sequence.
        
        Example:
        Input: ['h', 'e', 'l', 'o']  (might have skipped second 'l')
        Output: [('hello', 0.95), ('helo', 0.05)]
        """
        # Convert to string
        seq_str = ''.join(key_sequence)
        
        # Find exact matches
        exact_matches = self.sequence_index.get(seq_str, [])
        
        # Find fuzzy matches (edit distance 1-2)
        fuzzy_matches = self._find_fuzzy_matches(seq_str)
        
        # Combine and score
        candidates = []
        for word in exact_matches:
            candidates.append((word, 1.0))  # Perfect match
        
        for word, distance in fuzzy_matches:
            score = 1.0 / (1 + distance)  # Decay by edit distance
            candidates.append((word, score))
        
        return sorted(candidates, key=lambda x: x[1], reverse=True)
    
    def _build_sequence_index(self) -> Dict[str, List[str]]:
        """Build index mapping key sequences to words"""
        index = {}
        for word in self.dictionary:
            # Deduplicate consecutive letters
            # "hello" -> "helo" (gesture might not hit 'l' twice)
            seq = self._word_to_sequence(word)
            if seq not in index:
                index[seq] = []
            index[seq].append(word)
        return index
    
    def _word_to_sequence(self, word: str) -> str:
        """Convert word to gesture sequence (dedupe consecutive)"""
        if not word:
            return ""
        result = [word[0]]
        for char in word[1:]:
            if char != result[-1]:
                result.append(char)
        return ''.join(result)
```

#### 4. QML Gesture Visualization

```qml
// Show gesture path as user swipes
Canvas {
    id: gestureCanvas
    anchors.fill: parent
    
    property var pathPoints: []
    
    onPaint: {
        var ctx = getContext("2d")
        ctx.clearRect(0, 0, width, height)
        
        if (pathPoints.length > 1) {
            ctx.strokeStyle = "#F4E47E"
            ctx.lineWidth = 3
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            
            ctx.beginPath()
            ctx.moveTo(pathPoints[0].x, pathPoints[0].y)
            
            for (var i = 1; i < pathPoints.length; i++) {
                ctx.lineTo(pathPoints[i].x, pathPoints[i].y)
            }
            
            ctx.stroke()
        }
    }
    
    Connections {
        target: gestureTracker
        function onPathUpdated(points) {
            gestureCanvas.pathPoints = points
            gestureCanvas.requestPaint()
        }
    }
}
```

**Benefits:**
- Faster typing for some users
- Reduces number of discrete clicks needed
- Fun, engaging interaction
- Alternative input method for variety

**Challenges:**
- Requires precise path tracking
- May be harder with mouse than touch
- Need good word matching algorithm

---

## 3. Next-Word Prediction (Context-Aware)

### What Gboard Does

**The Innovation:** After you type a word, Gboard predicts the **next word** based on context, not just the current word.

**Example:**
- Type "I am" → suggests "going", "a", "not", "very"
- Type "I am going" → suggests "to", "home", "out"
- Type "I am going to" → suggests "the", "be", "go"

**How it works:**
- **N-gram language model** - Trained on billions of sentences
- **Neural language model** - Transformer-based (BERT, GPT-style)
- **Personalization** - Learns your common phrases
- **Context window** - Looks at last 3-5 words

### How Alpha-OSK Could Implement This

**Current state:** Alpha-OSK has basic next-word prediction with transformer re-ranking.

**Enhancement:**

```python
class ContextualNextWordPredictor:
    """Enhanced next-word prediction with deeper context"""
    
    def __init__(self):
        self.ngram_model = NGramModel(n=5)  # 5-gram model
        self.transformer = DistilGPT2()
        self.user_phrases = {}  # Learn user's common phrases
    
    def predict_next_word(self, context: str, n_predictions: int = 5) -> List[Tuple[str, float]]:
        """
        Predict next word given context.
        
        Uses:
        1. User's personal phrase patterns (highest weight)
        2. Transformer model (medium weight)
        3. N-gram model (baseline)
        """
        # Extract recent context (last 5 words)
        words = context.split()
        recent_context = ' '.join(words[-5:]) if len(words) >= 5 else context
        
        # Check user's learned phrases first
        user_preds = self._check_user_phrases(recent_context)
        
        # Get transformer predictions
        transformer_preds = self._transformer_predict(recent_context)
        
        # Get n-gram predictions
        ngram_preds = self._ngram_predict(recent_context)
        
        # Blend predictions
        blended = self._blend_predictions(
            user_preds, transformer_preds, ngram_preds
        )
        
        return blended[:n_predictions]
    
    def learn_phrase(self, phrase: str):
        """Learn user's common phrases"""
        words = phrase.split()
        
        # Store n-grams from this phrase
        for i in range(len(words) - 1):
            context = ' '.join(words[max(0, i-4):i+1])
            next_word = words[i+1]
            
            if context not in self.user_phrases:
                self.user_phrases[context] = {}
            
            self.user_phrases[context][next_word] = \
                self.user_phrases[context].get(next_word, 0) + 1
```

**Benefits:**
- More accurate predictions
- Learns your writing style
- Reduces keystrokes significantly

---

## 4. Smart Autocorrect

### What Gboard Does

**The Innovation:** Context-aware autocorrect that considers:
- Spatial proximity (fuzzy matching)
- Language model probability
- User's vocabulary
- Sentence context

**Example:**
- "I ate an apple" → "apple" not "Apple" (capitalization context)
- "Let's meat tomorrow" → suggests "meet" (context: time-related)
- "I love my dog Spot" → learns "Spot" is a name, doesn't correct

### How Alpha-OSK Could Implement This

```python
class SmartAutocorrect:
    """Context-aware autocorrection"""
    
    def __init__(self):
        self.fuzzy_matcher = FuzzyWordGenerator()
        self.language_model = LanguageModel()
        self.user_dictionary = set()  # Learned words
        self.autocorrect_enabled = True
        self.confidence_threshold = 0.8  # Only autocorrect if confident
    
    def should_autocorrect(self, typed_word: str, context: str) -> Optional[str]:
        """
        Determine if word should be autocorrected.
        
        Returns:
        - None if no correction needed
        - Corrected word if confident correction found
        """
        # Don't correct if in user dictionary
        if typed_word in self.user_dictionary:
            return None
        
        # Don't correct if it's a valid word
        if typed_word in self.standard_dictionary:
            return None
        
        # Get fuzzy candidates
        candidates = self.fuzzy_matcher.generate_candidates(typed_word)
        
        if not candidates:
            return None
        
        # Get best candidate with context
        best_word, confidence = self._rank_with_context(candidates, context)
        
        # Only autocorrect if very confident
        if confidence > self.confidence_threshold:
            return best_word
        
        return None
    
    def _rank_with_context(self, candidates, context):
        """Rank candidates using language model"""
        scored = []
        for word, spatial_prob in candidates:
            # Get probability in context
            lm_prob = self.language_model.word_probability(word, context)
            combined = spatial_prob * 0.3 + lm_prob * 0.7
            scored.append((word, combined))
        
        return max(scored, key=lambda x: x[1])
```

---

## 5. Personalized Dictionary

### What Gboard Does

**The Innovation:** Automatically learns new words you type frequently:
- Names (people, places)
- Technical terms
- Slang/informal words
- Domain-specific vocabulary

**How it works:**
- Track word frequency
- If word typed 3+ times, add to personal dictionary
- Sync across devices (cloud)
- Suggest learned words in predictions

### How Alpha-OSK Could Implement This

```python
class PersonalDictionary:
    """Learn and store user's personal vocabulary"""
    
    def __init__(self):
        self.word_counts = {}  # word -> count
        self.learned_words = set()
        self.learning_threshold = 3  # Add after 3 uses
        self.save_path = Path.home() / ".alpha-osk" / "personal_dict.json"
        self.load()
    
    def observe_word(self, word: str):
        """Track word usage"""
        # Skip common words (already in base dictionary)
        if word in self.base_dictionary:
            return
        
        # Increment count
        self.word_counts[word] = self.word_counts.get(word, 0) + 1
        
        # Learn if threshold reached
        if self.word_counts[word] >= self.learning_threshold:
            if word not in self.learned_words:
                self.learned_words.add(word)
                self.save()
                print(f"Learned new word: {word}")
    
    def is_valid_word(self, word: str) -> bool:
        """Check if word is in base or personal dictionary"""
        return (word in self.base_dictionary or 
                word in self.learned_words)
    
    def suggest_from_personal(self, prefix: str) -> List[str]:
        """Get suggestions from personal dictionary"""
        return [w for w in self.learned_words if w.startswith(prefix)]
```

---

## 6. Multi-Language Support

### What Gboard Does

**The Innovation:** Seamlessly switch between languages or type in multiple languages simultaneously.

**Features:**
- Auto-detect language
- Bilingual predictions (English + Spanish)
- Language-specific autocorrect
- Easy language switching

### How Alpha-OSK Could Implement This

```python
class MultiLanguageManager:
    """Manage multiple languages simultaneously"""
    
    def __init__(self):
        self.active_languages = ['en']  # Default English
        self.language_models = {}
        self.load_language_models()
    
    def add_language(self, lang_code: str):
        """Add a language to active set"""
        if lang_code not in self.active_languages:
            self.active_languages.append(lang_code)
            self.load_language_model(lang_code)
    
    def predict_multilingual(self, context: str) -> List[Tuple[str, float, str]]:
        """
        Get predictions from all active languages.
        
        Returns: [(word, probability, language), ...]
        """
        all_predictions = []
        
        for lang in self.active_languages:
            model = self.language_models[lang]
            preds = model.predict(context)
            
            # Tag with language
            for word, prob in preds:
                all_predictions.append((word, prob, lang))
        
        # Sort by probability
        return sorted(all_predictions, key=lambda x: x[1], reverse=True)
```

---

## Implementation Priority for Alpha-OSK

### High Priority (Immediate Impact)
1. **Fuzzy/Spatial Recognition** - Critical for users with motor challenges
2. **Smart Autocorrect** - Reduces frustration
3. **Personal Dictionary** - Learns user's vocabulary

### Medium Priority (Nice to Have)
4. **Enhanced Next-Word Prediction** - Already partially implemented
5. **Multi-Language Support** - Useful for bilingual users

### Low Priority (Advanced)
6. **Gesture Typing** - Cool but complex, may not work well with mouse

---

## Comparison: Dasher vs Mobile Keyboards

| Feature | Dasher | Gboard/SwiftKey | Alpha-OSK Goal |
|---------|--------|-----------------|----------------|
| **Paradigm** | Novel (zooming) | Familiar (QWERTY) | Familiar + AI |
| **Input** | Continuous pointing | Discrete taps | Discrete clicks |
| **Prediction** | Built into spatial layout | Separate suggestion bar | Separate bar |
| **Fuzzy matching** | Implicit (spatial) | Explicit (proximity) | **Implement this** |
| **Learning** | Adaptive PPM | Neural + personal dict | **Implement both** |
| **Best for** | Eye-tracking, head-tracking | Touch typing | Mouse, accessibility |

---

## Technical Architecture

```python
# Proposed integration
class IntelligentKeyboard:
    def __init__(self):
        # From Dasher
        self.ppm_model = PPMPredictor()
        self.adaptive_learner = AdaptiveLearner()
        
        # From mobile keyboards
        self.fuzzy_recognizer = FuzzyPredictor()
        self.gesture_decoder = GestureWordMatcher()
        self.autocorrect = SmartAutocorrect()
        self.personal_dict = PersonalDictionary()
        
        # Hybrid approach
        self.hybrid_predictor = HybridPredictor(
            ppm=self.ppm_model,
            fuzzy=self.fuzzy_recognizer,
            transformer=self.transformer_model
        )
```

---

## Next Steps

1. **Implement fuzzy recognition first** - Biggest impact for accessibility
2. **Add personal dictionary** - Easy win, high value
3. **Enhance autocorrect** - Build on fuzzy recognition
4. **Consider gesture typing** - Experimental, test with users

---

## References

- [Gboard Technical Blog](https://ai.googleblog.com/search/label/Gboard)
- [SwiftKey Neural Network](https://www.microsoft.com/en-us/research/project/swiftkey/)
- Touch keyboard research papers on spatial uncertainty
- Mobile keyboard UX patterns

---

*This document focuses on **mobile keyboard innovations**. See [`TECHNICAL_INNOVATIONS.md`](TECHNICAL_INNOVATIONS.md) for **Dasher innovations**.*
