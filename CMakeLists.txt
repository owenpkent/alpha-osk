cmake_minimum_required(VERSION 3.21)

# Alpha-OSK -- C++ / Qt6 Windows rewrite (cpp-rewrite branch).
# The QML UI under qml/ and the data files under data/ are reused unchanged
# from the original Python app; only the backend is rewritten in C++.
project(AlphaOSK VERSION 1.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

# Allow the build to point at the Qt install without editing this file:
#   cmake -B build -G Ninja -DCMAKE_PREFIX_PATH=C:/Qt/6.5.3/msvc2019_64
if(NOT DEFINED CMAKE_PREFIX_PATH AND DEFINED ENV{QT_ROOT})
    set(CMAKE_PREFIX_PATH "$ENV{QT_ROOT}")
endif()

find_package(Qt6 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2 Widgets Network)

qt_standard_project_setup()

add_executable(alpha-osk
    cpp/main.cpp
    cpp/WinUtil.cpp
    cpp/Paths.cpp
    cpp/SnippetStore.cpp
    cpp/Analytics.cpp
    cpp/TelemetryClient.cpp
    cpp/DataExport.cpp
    cpp/KeyboardBridge.cpp
    cpp/platform/KeySynthesizer.cpp
    cpp/platform/WindowsKeySynthesizer.cpp
    cpp/platform/PasswordDetect.cpp
    cpp/prediction/NgramPredictor.cpp
    cpp/prediction/HybridPredictor.cpp
    cpp/prediction/PPMPredictor.cpp
    cpp/prediction/FuzzyRecognizer.cpp
    cpp/prediction/SwipeRecognizer.cpp
    cpp/prediction/VocabularyPack.cpp
)

target_include_directories(alpha-osk PRIVATE cpp)

target_link_libraries(alpha-osk PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Qml
    Qt6::Quick
    Qt6::QuickControls2
    Qt6::Widgets
    Qt6::Network
    Qt6::GuiPrivate
)

if(WIN32)
    target_link_libraries(alpha-osk PRIVATE user32 shell32 ole32 oleaut32 winmm uiautomationcore)
endif()

# In a dev build the exe loads qml/Main.qml and data/ from the source tree.
# Bake the source dir in as a fallback so running from build/ "just works".
target_compile_definitions(alpha-osk PRIVATE
    APP_PROJECT_ROOT="${CMAKE_CURRENT_SOURCE_DIR}"
    APP_VERSION_STR="${PROJECT_VERSION}"
)
