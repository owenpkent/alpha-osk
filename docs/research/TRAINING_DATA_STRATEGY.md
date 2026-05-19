# Training Data Strategy for Alpha-OSK

## Current State

Our prediction system has a **tiny training corpus** (~5,859 characters). For comparison:
- **Gboard**: Trained on billions of words
- **SwiftKey**: Learns from millions of users
- **Alpha-OSK**: ~600 common phrases

This document outlines strategies to improve prediction quality while keeping storage minimal.

---

## 📊 Available Free Training Sources

### 1. Google 10,000 English Words (Recommended - Immediate)
**Source:** https://github.com/first20hours/google-10000-english

- **Size:** ~100KB
- **Quality:** Derived from Google's Trillion Word Corpus
- **Coverage:** 7,000 most common lemmas cover 90% of typical usage
- **Variants available:**
  - `google-10000-english-usa.txt` - American English
  - `google-10000-english-no-swears.txt` - Family-friendly
  - Short/Medium/Long word lists by character length

**Why use it:** Pre-sorted by frequency. Top words appear first. Perfect for n-gram unigram seeding.

### 2. Peter Norvig's 1/3 Million Words
**Source:** https://norvig.com/ngrams/count_1w.txt

- **Size:** ~5MB
- **Quality:** Google's n-gram analysis, includes frequency counts
- **Format:** `word\tcount` per line

**Why use it:** If we need more vocabulary, this is the extended version.

### 3. COCA Word Frequency (Free Tier)
**Source:** https://www.wordfrequency.info/free.asp

- **Size:** Top 5,000 words free
- **Quality:** Based on 1 billion word Corpus of Contemporary American English
- **Extra data:** Part-of-speech tags, genre distribution

**Why use it:** Academic-quality frequency data with context.

### 4. N-grams.info (Bigrams/Trigrams)
**Source:** https://www.ngrams.info

- **Quality:** COCA-derived, includes 2-gram and 3-gram frequencies
- **Cost:** Free samples, full data is paid

**Why use it:** Our bigram/trigram predictions need word-pair frequencies.

---

## 🎯 Recommended Actions (Prioritized)

### Phase 1: Quick Wins (< 1 day)

#### A. Download Google 10K wordlist
```bash
cd data/
curl -O https://raw.githubusercontent.com/first20hours/google-10000-english/master/google-10000-english-usa-no-swears.txt
```

#### B. Integrate into n-gram predictor
```python
# Load as unigram frequency baseline
# Word position = frequency rank (word 1 = most common)
with open("google-10000-english-usa-no-swears.txt") as f:
    for rank, word in enumerate(f, 1):
        frequency = 10000 - rank  # Higher = more common
        unigrams[word.strip()] = frequency
```

#### C. Create common bigram/trigram phrases
Manually curate 500-1000 common phrases:
```
i am
you are
how are you
what is
going to
want to
have to
...
```

### Phase 2: Smart Corpus (< 1 week)

#### A. Wikipedia Simple English
**Source:** https://simple.wikipedia.org dumps

- Uses simplified vocabulary (~2,000 word vocabulary)
- Good for general knowledge phrases
- ~150MB compressed, extract key articles

#### B. Movie Subtitles (OpenSubtitles)
**Source:** https://opus.nlpl.eu/OpenSubtitles-v2018.php

- Natural conversational language
- Common dialogue patterns
- Filter for English, remove timestamps

#### C. Email/Chat Corpora (Enron, Ubuntu IRC)
- Natural keyboard-style writing
- Common abbreviations and phrases

### Phase 3: Compression for Storage (Advanced)

#### Arithmetic Coding
- Compress pre-trained models using adaptive arithmetic coding
- Dasher uses this for efficient storage
- Can reduce model size by 50-70%
- See: https://en.wikipedia.org/wiki/Arithmetic_coding

#### Bloom Filter for Dictionary
- Probabilistic data structure
- ~1MB can check 1 million words
- Used for "is this a valid word?" checks

---

## 💾 Storage Budget Targets

| Data Type | Current | Target | Method |
|-----------|---------|--------|--------|
| Unigram frequencies | 8,893 words | 10,000 words | Google 10K list |
| Bigram frequencies | ~50 | 5,000+ | COCA samples |
| Trigram frequencies | ~20 | 1,000+ | Curated phrases |
| PPM character model | 5,859 chars | 50,000+ chars | Expanded corpus |
| **Total disk footprint** | ~500KB | **< 2MB** | Compressed |

---

## 🔄 User Learning (Already Implemented)

The system learns from user behavior:
1. **Word selection** - Clicked predictions boost that word
2. **Typed words** - New words added to personal dictionary
3. **Bigram learning** - Word pairs become associated

This happens automatically. Power users can import their own text files via Prediction Settings.

---

## 📝 Implementation Checklist

- [ ] Download Google 10K wordlist to `data/`
- [ ] Update `ngram_predictor.py` to load frequency-ranked words
- [ ] Create `data/common_bigrams.txt` with 500+ pairs
- [ ] Create `data/common_trigrams.txt` with 200+ triplets
- [ ] Expand `training_corpus.txt` with conversational text
- [ ] Document how users can import custom corpora
- [ ] (Future) Implement arithmetic coding for model compression

---

## References

- **Google Trillion Word Corpus:** https://books.google.com/ngrams/info
- **Peter Norvig's Natural Language Corpus Data:** https://norvig.com/ngrams/
- **COCA (Corpus of Contemporary American English):** https://www.wordfrequency.info
- **Arithmetic Coding (Wikipedia):** https://en.wikipedia.org/wiki/Arithmetic_coding
- **Dasher Project (PPM + Arithmetic Coding):** http://www.inference.org.uk/dasher/
