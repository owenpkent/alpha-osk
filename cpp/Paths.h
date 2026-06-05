#pragma once

#include <QString>

// Filesystem locations, matching the Python app so the C++ rewrite reads the
// user's existing learned model and data files in place.
//
// On Windows the Python app uses %APPDATA%/alpha-osk (NOT the Qt
// AppDataLocation, which would append the org/app names). We mirror that
// exactly so ngram_model.json / ppm_model.json carry over.
namespace paths {

// %APPDATA%/alpha-osk  (created if missing on first access of modelDir()).
QString configDir();

// configDir()/models
QString modelDir();

QString ngramModelPath();   // modelDir()/ngram_model.json
QString ppmModelPath();     // modelDir()/ppm_model.json

// Project root: the directory containing qml/ and data/. Resolved by walking
// up from the executable until qml/Main.qml is found, then falling back to the
// source dir baked in at build time.
QString projectRoot();

QString dataDir();          // projectRoot()/data
QString qmlMainPath();      // projectRoot()/qml/Main.qml

} // namespace paths
