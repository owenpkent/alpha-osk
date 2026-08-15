"""Regenerate the README screenshots from the real QML UI.

    python scripts/capture_screenshots.py

Loads the real ``qml/Main.qml`` against a real ``KeyboardBridge`` under the
offscreen platform plugin, drives it into each state worth showing, and
writes PNGs to ``assets/screenshots/``.  Nothing is clicked by hand, so a
refresh after a UI change costs one command instead of an afternoon, and
two screenshots taken a year apart still frame the same thing.

Three things about the environment are load-bearing:

**The synthesizer is mocked.**  A real ``KeyboardBridge`` types into
whatever app currently has focus, and this script types to populate the
prediction bar.  Without the mock, running it would spray text into the
user's editor.

**``APPDATA`` points at a throwaway directory.**  The bridge loads and
saves the model, analytics and snippets from the config dir, so pointing
it elsewhere keeps a screenshot run from touching the real ones.  It also
keeps the *user's own vocabulary* out of the shots: the word cloud and the
dashboard render learned words, and these images go in a public README.
The demo model below is seeded from neutral prose for exactly that reason.

**Fonts are found explicitly** (``QT_QPA_FONTDIR``).  The offscreen plugin
builds no font database on Windows, and the layout renders perfectly with
every glyph as a tofu box, which is easy to miss in a thumbnail.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHOTS = REPO / "assets" / "screenshots"


def _sandbox_dir() -> Path:
    """A throwaway config dir whose *path* is safe to photograph.

    Settings -> Data & Privacy prints the export folder on screen, so the
    sandbox location ends up in a public README.  A `tempfile.mkdtemp`
    path contains the developer's Windows username; `C:\\Users\\Public` is
    world-writable, belongs to nobody, and reads as a plausible install.
    """
    if sys.platform == "win32":
        public = Path(r"C:\Users\Public\alpha-osk-demo")
        try:
            public.mkdir(parents=True, exist_ok=True)
            return public
        except OSError:
            pass
    return Path(tempfile.mkdtemp(prefix="alpha-osk-shots-"))


# Every one of these must be set before QGuiApplication is constructed.
_SANDBOX = _sandbox_dir()
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
# The offscreen plugin has no native control style, so Controls fall back
# to a style that paints nothing on a dark panel (the Check Now button
# photographs as a black rectangle).  Fusion draws itself completely.
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Fusion")
os.environ["APPDATA"] = str(_SANDBOX)
os.environ["XDG_CONFIG_HOME"] = str(_SANDBOX)
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

sys.path.insert(0, str(REPO))

from contextlib import ExitStack  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

from PySide6.QtCore import QCoreApplication, QMetaObject, QSettings, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

# Imported for the side effect, exactly as the headless QML tests do: without
# QQuickWindow in the module, engine.rootObjects()[0] comes back as a plain
# QWindow and has no grabWindow().
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402

# A distinct org/app name keeps the QML `Settings` element out of the real
# user's registry section -- otherwise a screenshot run would overwrite their
# saved theme, layout and window width.
QCoreApplication.setOrganizationName("alpha-osk-screenshots")
QCoreApplication.setApplicationName("Alpha-OSK-Screenshots")

# Start from defaults every time.  Turning the side panels on widens the
# window, and QML `Settings` persists that width, so a second run started
# wider and widened again: the shots grew a few pixels per run and every
# refresh churned all eight files for no visible reason.
QSettings("alpha-osk-screenshots", "Alpha-OSK-Screenshots").clear()

from src import keyboard_bridge  # noqa: E402

# Demo vocabulary for the model-facing shots.  Deliberately bland: it is
# rendered into a public README, so it must not be anybody's real typing.
# Repetition is what gives the word cloud and the top-words chart their
# spread, so the counts below are shaped rather than uniform.
_DEMO_CORPUS = [
    ("thank you so much for your help today", 9),
    ("I will send the document over this afternoon", 7),
    ("could you please let me know when you are free", 7),
    ("the meeting has been moved to tomorrow morning", 6),
    ("I think that would work really well for us", 6),
    ("please let me know if you need anything else", 5),
    ("I am looking forward to hearing back from you", 5),
    ("that sounds good to me thank you again", 5),
    ("we should probably talk about this in person", 4),
    ("I have attached the notes from our last call", 4),
    ("the weather is beautiful this morning", 3),
    ("hope you are having a good week so far", 3),
    ("I will follow up with them later today", 3),
    ("let me check my calendar and get back to you", 3),
    ("thanks again for putting this together", 2),
]

_SETTLE_MS = 400


def _settle(ms: int = _SETTLE_MS) -> None:
    """Let bindings, layout polish and the software renderer catch up."""
    QTest.qWait(ms)


def _grab(window, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    _settle()
    image = window.grabWindow()
    if image.isNull():
        raise RuntimeError(f"grab failed for {name}")
    path = SHOTS / f"{name}.png"
    if not image.save(str(path)):
        raise RuntimeError(f"could not write {path}")
    print(f"  {path.relative_to(REPO)}  ({image.width()}x{image.height()})")


def _window_titled(title: str):
    for window in QGuiApplication.allWindows():
        if window.title() == title:
            return window
    raise LookupError(f"no window titled {title!r}")


def _window_named(object_name: str):
    for window in QGuiApplication.allWindows():
        if window.objectName() == object_name:
            return window
    raise LookupError(f"no window named {object_name!r}")


def _child_with_property(window, name: str):
    """The first item in *window* that declares a property called *name*.

    Found by shape rather than by objectName so the QML does not have to
    carry an id that exists only for this script.  ``currentView`` and
    ``currentTab`` are each declared once in the tree they are searched in.
    """
    for item in window.findChildren(QQuickItem):
        if item.property(name) is not None:
            return item
    raise LookupError(f"no item exposing {name!r}")


def _seed_demo_model(bridge) -> None:
    """Give the engine and the dashboard something plausible to render.

    An empty model produces an empty word cloud and a dashboard of zeroes,
    which shows the reader nothing about what the feature is for.
    """
    for sentence, repeats in _DEMO_CORPUS:
        for _ in range(repeats):
            bridge._predictor.learn(sentence)

    # Lifetime counters for the dashboard.  Set directly rather than
    # simulated, because the shot needs a *year* of usage, and the ratios
    # between them are what the four tiles actually display.
    analytics = bridge._analytics
    analytics._alltime_keystrokes = 214_806
    analytics._alltime_keystrokes_saved = 86_142
    analytics._alltime_words = 43_517
    analytics._alltime_predictions = 21_884
    analytics._alltime_prediction_offers = 34_205
    analytics._alltime_prediction_rank_count = 21_884
    analytics._alltime_top_pick_count = 13_402
    analytics._alltime_backspaces = 9_733
    analytics._alltime_minutes = 4_912.0
    analytics._alltime_sessions = 312
    for sentence, repeats in _DEMO_CORPUS:
        for word in sentence.split():
            analytics._alltime_word_freq[word] += repeats * 3


def _make_bridge():
    from src.keyboard_bridge import KeyboardBridge

    with patch.object(keyboard_bridge, "create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()
    bridge._synth = synth
    return bridge


def _type(bridge, text: str) -> None:
    for char in text:
        if char == " ":
            bridge.pressSpecialKey("space")
        else:
            bridge.pressKey(char)


def main() -> int:
    # Held only for its lifetime: Qt requires an application object to exist
    # for the whole run, and dropping the reference tears down the scene.
    _app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841

    # The bridge probes the live desktop on every keystroke (password-field
    # detection) and on a timer (outside-click detection).  Both would read
    # whatever the developer left focused, and a password field on screen
    # would flip the shot into "Learning paused".  Patched by name only where
    # the name exists, so this script keeps working across releases that add
    # or drop a probe rather than dying on an AttributeError.
    with ExitStack() as stack:
        for probe in ("is_password_field", "external_click_detected"):
            if hasattr(keyboard_bridge, probe):
                stack.enter_context(patch.object(keyboard_bridge, probe, return_value=False))
        bridge = _make_bridge()
        _seed_demo_model(bridge)

        warnings: list[str] = []
        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
        engine.rootContext().setContextProperty("keyboard", bridge)
        engine.load(QUrl.fromLocalFile(str(REPO / "qml" / "Main.qml")))
        if not engine.rootObjects():
            print("Main.qml failed to load:")
            for warning in warnings:
                print(" ", warning)
            return 1
        root = engine.rootObjects()[0]
        _settle(800)

        # Every panel on, so the hero shot shows what the keyboard can be
        # rather than what it is on a fresh install.
        root.setProperty("showFunctionRow", True)
        root.setProperty("showNavigation", True)
        root.setProperty("showNumpad", True)
        _settle()

        # Prediction pills, typed rather than injected, so what the bar shows
        # is genuinely what the engine returns for this prefix.
        _type(bridge, "thank you so much for your he")
        _settle()

        print("capturing:")
        root.setProperty("currentTheme", "dark")
        _settle()
        _grab(root, "dark-theme-keyboard")

        # "purple" is the id; "Amethyst" is only its display name, and an
        # unknown id falls back to dark *silently* (themeData[id] || dark),
        # so getting this wrong photographs as a second identical shot.
        root.setProperty("currentTheme", "purple")
        _settle()
        assert root.property("currentTheme") == "purple"
        _grab(root, "amethyst-theme-keyboard")

        root.setProperty("currentTheme", "dark")
        _settle()

        # Compact view: its own layout, its own Number Row panel.
        root.setProperty("compactView", True)
        _settle(600)
        _type(bridge, "llo the")
        _settle()
        _grab(root, "compact-view-keyboard")
        root.setProperty("compactView", False)
        _settle(600)

        # Settings, one shot per drilled-in category.
        root.setProperty("showSettings", True)
        _settle(600)
        settings_window = _window_titled("Alpha-OSK Settings")
        panel = _child_with_property(settings_window, "currentView")
        settings_views = (
            ("appearance", "settings-appearance"),
            ("data", "settings-data-privacy"),
        )
        for view, name in settings_views:
            panel.setProperty("currentView", view)
            _settle(500)
            _grab(settings_window, name)
        root.setProperty("showSettings", False)
        _settle()

        # Your Language Model: word cloud, then the dashboard tab.
        root.setProperty("showVisualization", True)
        _settle(900)
        viz_window = _window_titled("Your Language Model")
        viz = _child_with_property(viz_window, "currentTab")
        _settle(600)
        _grab(viz_window, "language-model-word-cloud")
        viz.setProperty("currentTab", 2)
        _settle(900)
        _grab(viz_window, "analytics-dashboard")
        root.setProperty("showVisualization", False)
        _settle()

        # Snippets: a separate top-level window, opened from the title bar.
        # Six fills the grid exactly (2 columns x 3 rows), which is also
        # what the pager keys off, so this shot shows a full page without
        # advertising a second empty one.
        bridge.setSnippet(0, "Name", "Alex Rivera")
        bridge.setSnippet(1, "Email", "alex.rivera@example.com")
        bridge.setSnippet(2, "Phone", "555-123-4567")
        bridge.setSnippet(3, "Address", "128 Juniper Lane, Springfield")
        for _ in range(2):
            bridge.addSnippet()
        bridge.setSnippet(4, "Work email", "a.rivera@example.org")
        bridge.setSnippet(5, "Sign-off", "Thanks, Alex")
        # Tagged so the shot shows what the tags are for: telling two
        # similar snippets apart without reading either one.
        for index, tag in ((1, "blue"), (2, "green"), (3, "amber"), (4, "blue")):
            bridge.setSnippetColor(index, tag)
        snippets_window = _window_named("snippetsWindow")
        # openList(), not visible = true: the rows are built by its refresh(),
        # so simply showing the window renders an empty list.
        QMetaObject.invokeMethod(snippets_window, "openList")
        _settle(700)
        _grab(snippets_window, "snippets-window")
        snippets_window.setProperty("visible", False)

        real = [w for w in warnings if "does not support customization" not in w]
        if real:
            print(f"\n{len(real)} QML warning(s) during capture:")
            for warning in real[:10]:
                print(" ", warning)

    shutil.rmtree(_SANDBOX, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
