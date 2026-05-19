# Word Prediction Engine Options

**Decision Point:** Choose the prediction approach for Alpha-OSK

This document presents 5 viable approaches for implementing word prediction, ranked by implementation complexity and performance characteristics.

---

## Quick Comparison

| Option | Latency | Accuracy | RAM | Disk | Learning | Offline | Complexity |
|--------|---------|----------|-----|------|----------|---------|------------|
| **A. N-gram (presage)** | <10ms | Good | ~50MB | ~20MB | ✅ Yes | ✅ Yes | ⭐ Low |
| **B. Small Transformer** | ~50ms | Better | ~200MB | ~100MB | ✅ Yes | ✅ Yes | ⭐⭐ Medium |
| **C. DistilGPT-2** | ~100ms | Best | ~500MB | ~300MB | ⚠️ Complex | ✅ Yes | ⭐⭐⭐ High |
| **D. Hybrid (n-gram + LLM)** | ~20ms | Best | ~300MB | ~150MB | ✅ Yes | ✅ Yes | ⭐⭐⭐ High |
| **E. Cloud API** | ~200ms | Best | ~10MB | ~5MB | ❌ No | ❌ No | ⭐ Low |

---

## Option A: N-gram Model (presage-based) ⭐ RECOMMENDED FOR MVP

### Overview
Statistical model using word frequency and context (2-3 previous words). Based on the proven `presage` library used by GNOME On-Board.

### Pros
- ✅ **Fast**: <10ms prediction latency
- ✅ **Lightweight**: ~50MB RAM, ~20MB disk
- ✅ **Proven**: Used in On-Board for years
- ✅ **Easy learning**: Simple frequency counting
- ✅ **100% offline**
- ✅ **Privacy-first**: All data stays local
- ✅ **Low complexity**: Can implement in 1-2 days

### Cons
- ❌ Limited context awareness (only 2-3 words back)
- ❌ No semantic understanding
- ❌ Struggles with rare words
- ❌ Needs large corpus for good accuracy

### Implementation Path
1. Use `python-presage` or port core algorithm
2. Train on standard corpus (e.g., Wikipedia subset, news articles)
3. Add user vocabulary learning (frequency tracking)
4. Expose predictions via `keyboard_bridge.py`

### Code Sketch
```python
# src/prediction/ngram_engine.py
class NgramPredictor:
    def __init__(self):
        self.model = presage.Presage(config_file="presage.xml")
    
    def predict(self, context: str, n: int = 5) -> List[str]:
        """Return top n predictions given context."""
        self.model.context(context)
        return [self.model.predict(i) for i in range(n)]
    
    def learn(self, text: str):
        """Update model with new text."""
        self.model.learn(text)
```

### Estimated Effort
- **Setup**: 4-6 hours
- **Integration**: 2-3 hours
- **Testing**: 2 hours
- **Total**: 1-2 days

---

## Option B: Small Transformer (DistilBERT-based)

### Overview
Lightweight transformer model fine-tuned for next-word prediction. Uses Hugging Face's `transformers` library with a small model like DistilBERT or TinyBERT.

### Pros
- ✅ Better context understanding than n-grams
- ✅ Handles longer context (512 tokens)
- ✅ Can fine-tune on user's writing style
- ✅ Still runs on CPU
- ✅ 100% offline

### Cons
- ⚠️ Slower: ~50-100ms per prediction
- ⚠️ Higher RAM: ~200-300MB
- ⚠️ Requires ML knowledge for fine-tuning
- ⚠️ Larger disk footprint (~100-200MB)

### Implementation Path
1. Load pre-trained DistilBERT or GPT-2 small
2. Fine-tune on keyboard-specific corpus
3. Run inference in background thread
4. Cache predictions for common contexts

### Code Sketch
```python
# src/prediction/transformer_engine.py
from transformers import pipeline

class TransformerPredictor:
    def __init__(self):
        self.model = pipeline(
            "text-generation",
            model="distilgpt2",
            device=-1  # CPU
        )
    
    def predict(self, context: str, n: int = 5) -> List[str]:
        results = self.model(
            context,
            max_new_tokens=1,
            num_return_sequences=n,
            return_full_text=False
        )
        return [r['generated_text'].strip() for r in results]
```

### Estimated Effort
- **Setup**: 1 day
- **Fine-tuning**: 2-3 days
- **Integration**: 1 day
- **Optimization**: 1-2 days
- **Total**: 5-7 days

---

## Option C: DistilGPT-2 (Full Context)

### Overview
Larger transformer model (DistilGPT-2 or GPT-2 small) for state-of-the-art predictions with full sentence context.

### Pros
- ✅ Best accuracy
- ✅ Understands full sentence context
- ✅ Can generate multi-word suggestions
- ✅ 100% offline

### Cons
- ❌ Slowest: ~100-200ms per prediction
- ❌ High RAM: ~500MB-1GB
- ❌ Requires GPU for acceptable speed
- ❌ Complex fine-tuning pipeline
- ❌ Large disk footprint (~300-500MB)

### Implementation Path
1. Load DistilGPT-2 or GPT-2 small
2. Implement async prediction queue
3. Add GPU acceleration (optional)
4. Fine-tune on accessibility-specific corpus

### Estimated Effort
- **Setup**: 2 days
- **Fine-tuning**: 3-5 days
- **GPU optimization**: 2-3 days
- **Integration**: 1-2 days
- **Total**: 8-12 days

---

## Option D: Hybrid (N-gram + Small LLM) ⭐ BEST LONG-TERM

### Overview
Combine fast n-gram model for instant predictions with a small transformer for context-aware re-ranking. Best of both worlds.

### Pros
- ✅ Fast initial response (<20ms from n-gram)
- ✅ High accuracy (LLM re-ranks top candidates)
- ✅ Graceful degradation (n-gram works if LLM slow)
- ✅ Moderate resource usage
- ✅ 100% offline

### Cons
- ⚠️ More complex architecture
- ⚠️ Two models to maintain
- ⚠️ Higher total disk usage

### Implementation Path
1. Implement n-gram engine (Option A)
2. Add small transformer for re-ranking
3. Use n-gram for instant feedback
4. LLM refines predictions in background

### Code Sketch
```python
# src/prediction/hybrid_engine.py
class HybridPredictor:
    def __init__(self):
        self.ngram = NgramPredictor()
        self.llm = TransformerPredictor()
    
    def predict_fast(self, context: str, n: int = 5) -> List[str]:
        """Instant n-gram predictions."""
        return self.ngram.predict(context, n)
    
    def predict_refined(self, context: str, n: int = 5) -> List[str]:
        """LLM-refined predictions (async)."""
        candidates = self.ngram.predict(context, n * 2)
        return self.llm.rerank(context, candidates)[:n]
```

### Estimated Effort
- **N-gram base**: 2 days
- **LLM integration**: 3 days
- **Async pipeline**: 2 days
- **Testing**: 2 days
- **Total**: 9-11 days

---

## Option E: Cloud API (OpenAI/Anthropic)

### Overview
Use cloud-based LLM API for predictions. Simplest implementation but requires internet.

### Pros
- ✅ Easiest to implement
- ✅ Best accuracy (GPT-4 level)
- ✅ No local resources needed
- ✅ Always up-to-date

### Cons
- ❌ **Privacy concern**: Text sent to cloud
- ❌ **Requires internet**: Not accessible offline
- ❌ **Latency**: ~200-500ms per request
- ❌ **Cost**: API fees per request
- ❌ **Not suitable for accessibility tool**

### Implementation Path
1. Integrate OpenAI or Anthropic API
2. Add caching layer
3. Implement fallback for offline mode

### Estimated Effort
- **Setup**: 4 hours
- **Caching**: 2 hours
- **Total**: 1 day

**⚠️ NOT RECOMMENDED** for accessibility software due to privacy and offline requirements.

---

## Recommendation Matrix

### For MVP (Next 1-2 Weeks)
**→ Option A: N-gram (presage-based)**
- Fast to implement
- Proven technology
- Good enough for initial users
- Can upgrade later

### For Production (1-3 Months)
**→ Option D: Hybrid (N-gram + Small LLM)**
- Best user experience
- Balances speed and accuracy
- Scalable architecture
- Supports federated learning

### If Resource-Constrained
**→ Option A: N-gram only**
- Minimal RAM/CPU
- Works on older hardware
- Still provides value

### If Performance is Critical
**→ Option B or C: Pure Transformer**
- Best accuracy
- Modern approach
- Requires more resources

---

## Integration Architecture

Regardless of chosen option, the integration follows this pattern:

```python
# src/prediction/prediction_engine.py
class PredictionEngine(QObject):
    """Base class for all prediction engines."""
    
    predictionsReady = Signal(list)  # Emit to QML
    
    @abstractmethod
    def predict(self, context: str, n: int = 5) -> List[str]:
        """Return top n predictions."""
        pass
    
    @abstractmethod
    def learn(self, text: str):
        """Update model with user's text."""
        pass

# In keyboard_bridge.py
class KeyboardBridge(QObject):
    def __init__(self):
        self.predictor = NgramPredictor()  # Or other engine
        self._context_buffer = ""
    
    @Slot(str)
    def pressKey(self, key: str):
        self._context_buffer += key
        predictions = self.predictor.predict(self._context_buffer)
        self.predictionsReady.emit(predictions)
```

---

## Decision Framework

**Choose Option A if:**
- You want to ship in 1-2 weeks
- Resource usage is critical
- You need proven, stable technology

**Choose Option B if:**
- You can wait 1-2 months
- You want better accuracy than n-grams
- You have 200-300MB RAM to spare

**Choose Option D if:**
- You want the best long-term solution
- You can invest 2-3 weeks
- You plan to add federated learning later

**Choose Option C if:**
- Accuracy is paramount
- You have GPU available
- You can accept 100ms+ latency

---

## Next Steps

1. **Pick an option** (A, B, C, or D)
2. **Create implementation plan** in TODO.md
3. **Set up prediction module**: `src/prediction/`
4. **Integrate with keyboard_bridge.py**
5. **Test with real typing scenarios**
6. **Iterate based on user feedback**

---

## Questions to Consider

- **How important is offline support?** (Rules out E)
- **What's the target hardware?** (Affects B/C viability)
- **How soon do you need this?** (Affects A vs others)
- **Will you add federated learning?** (Favors D)
- **Is privacy critical?** (Rules out E, favors A/D)

---

**Recommendation: Start with Option A (n-gram), plan migration to Option D (hybrid) once stable.**
