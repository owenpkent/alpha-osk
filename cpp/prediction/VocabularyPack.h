#pragma once

#include <QHash>
#include <QSet>
#include <QString>
#include <QStringList>
#include <QVariantList>

#include <memory>

class NgramPredictor;

// Import-only domain vocabulary packs. Port of
// src/prediction/vocabulary_pack.py. A pack is a folder with dictionary.txt
// (required) + optional bigrams.txt / trigrams.txt / pack.json. Enabled packs
// inject their vocabulary into the predictor's n-gram tables.
class VocabularyPack
{
public:
    static std::unique_ptr<VocabularyPack> fromDirectory(const QString &packDir);

    bool load();   // read dictionary/bigrams/trigrams into memory
    void unload();
    QVariantMap getInfo() const; // {id, name, description, version, enabled, words, bigrams, trigrams}

    QString id() const;
    bool enabled() const { return m_enabled; }
    void setEnabled(bool e) { m_enabled = e; }

    const QSet<QString> &words() const { return m_words; }
    const QHash<QString, QHash<QString, int>> &bigrams() const { return m_bigrams; }
    const QHash<QString, QHash<QString, int>> &trigrams() const { return m_trigrams; }

    // Pack vocabulary weights (lower than user-learned so personal typing wins).
    static constexpr int kUnigramWeight = 3;
    static constexpr int kBigramWeight = 30;
    static constexpr int kTrigramWeight = 30;

private:
    QString m_name;
    QString m_description;
    QString m_path;
    int m_version = 1;
    bool m_enabled = false;
    QSet<QString> m_words;
    QHash<QString, QHash<QString, int>> m_bigrams;
    QHash<QString, QHash<QString, int>> m_trigrams;
};

class PackManager
{
public:
    // builtinPacksDir defaults to <project>/data/packs (usually absent);
    // userPacksDir defaults to <config>/packs (created on first use).
    explicit PackManager(const QString &builtinPacksDir = QString(),
                         const QString &userPacksDir = QString());

    QStringList getAvailablePacks() const;     // pack ids
    QVariantList getAllPackInfo() const;        // [{id, name, ...}]
    QStringList getEnabledPacks() const;
    bool enablePack(const QString &id);
    bool disablePack(const QString &id);
    void applyToPredictor(NgramPredictor *predictor) const;
    QString importPack(const QString &sourceDir); // returns pack id, or "" on failure
    QString userPacksDir() const { return m_userPacksDir; }

private:
    void discoverPacks();
    static bool copyTreeSkipSymlinks(const QString &src, const QString &dst);

    QString m_builtinPacksDir;
    QString m_userPacksDir;
    QHash<QString, std::shared_ptr<VocabularyPack>> m_packs;
    QStringList m_order; // discovery order, for stable listing
};
