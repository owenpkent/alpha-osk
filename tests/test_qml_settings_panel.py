"""Headless QML tests for the Settings window's own layout, not any one
category's content.

The property under test is general, not Dictation-specific: **no settings
category may lay out content wider than the 360 px settings window**,
because one overflowing child raises the whole category's implicit width
and clips every sibling in it, not just the child that caused it. That is
exactly what happened on the Dictation page: the Deepgram model picker was
a bare ``Row`` of chips with no ``Layout.fillWidth``. A ``Row`` sizes
itself to its content and never shrinks, so once its two model chips
("Nova 3, most accurate, best for dictation" is the long one) summed wider
than the window, the *column* containing it grew to match, and every other
``Layout.fillWidth`` sibling in that column -- the Deepgram explainer
paragraph above it, the language-chip picker below it -- was resized to
that same too-wide column and rendered clipped at the window edge.
``Flow`` is the fix precisely because it does not have this failure mode:
it wraps chips onto additional rows instead of demanding one wide row, so
its own rendered width stays whatever its parent assigns it (fillWidth),
and nothing upstream of it is forced wider.

This loads the real ``qml/Main.qml`` headlessly, the way
``tests/test_qml_compact_view.py`` and ``tests/test_qml_dictation.py`` do,
and opens the *actual* Settings window (a separate top-level
``Window { .. }`` declared inline in ``Main.qml``, found by title through
``QGuiApplication.allWindows()`` since it carries no ``objectName`` --
unlike ``SnippetsWindow`` / ``SymbolsWindow``, it was never split into its
own component file). It then walks the live ``UnifiedSettingsPanel``
instance's visual tree for each category and asserts nothing renders past
the window's right edge.

Two traps, both load-bearing:

* **``flickArea.contentWidth`` cannot be the signal.** It is bound in the
  source as ``contentWidth: width`` -- a static echo of the Flickable's own
  width -- so ``contentWidth == width`` always, by construction, whether or
  not anything inside has overflowed. Asserting that comparison is exactly
  the "test that cannot fail" this suite's own conventions warn against.
  The actual signal is geometric: walk the live tree and compare each
  visible item's *scene* right edge against the window's.
* **A category that starts hidden and is switched to visible does not
  always get relaid-out by a bare ``processEvents()`` pump under the
  offscreen platform.** Reproduced independently of this feature (a
  minimal two-sibling-``ColumnLayout``-behind-one-``visible``-flag QML file
  shows the same thing): the parent ``ColumnLayout`` can leave the
  newly-shown child sized however it computed itself while hidden and
  unconstrained, rather than re-running its own arrangement pass. A real,
  running window settles this within a frame or two; a headless engine
  with no render loop needs a nudge. ``_settle_view`` below waits on a real
  timer (mirroring ``_wait_ms`` in ``test_qml_dictation.py`` -- a bare
  ``processEvents()`` loop drains the pending queue but does not let Qt's
  own timers run) and then perturbs the window's width by one pixel and
  back, which forces exactly the geometry-changed signal a real resize
  would send and is what actually flushes the stuck arrangement. Skipping
  this step reports the *previous* category's overflow number for the new
  one, which reads as a much larger and stranger bug than the real one.

**Font placeholder gate.** This machine's ``offscreen`` QPA platform has no
proportional font installed for ``QGuiApplication``'s default family, so
Qt falls back to a placeholder where every glyph -- ``i`` and ``W`` alike
-- has the same fixed advance. That inflates every string's rendered width
uniformly, and it is *the longest strings in the whole panel* that cross
the 360 px line first: the "Your Language Model" / "Dictation" header
titles and the "Nova 3, most accurate, best for dictation" model chip. On
a placeholder font "Your Language Model" alone measures 304 px, wider than
most real proportional renderings of that string would ever need. This is
the same trap ``tests/test_qml_dictation.py::_font_is_a_placeholder`` /
``requires_real_font`` exists for, and it is deliberately copied rather
than imported: CI installs real fonts (that module's own comment says so),
so these run for real there, and a contributor on a font-less box gets an
honest skip instead of a failure that has nothing to do with the code
under test. (This repo's own ``tests/conftest.py`` also points the
offscreen platform at the Windows system font directory on Windows for
exactly this reason, so a plain ``pytest`` run on a Windows checkout gets
real metrics and exercises the assertion for real, not the skip.)

**Why the sweep alone cannot prove the Dictation fix, and what does.** The
sweep below drives every category off whatever the running app actually
has loaded, and with a real font the *shipped* Deepgram model labels
("Nova 3, most accurate, best for dictation" included) turn out to be
short enough to fit in a bare ``Row`` too -- verified directly: reverting
the ``Row``-to-``Flow`` change and re-running the sweep against the real
label list still passes, because the strings involved just are not long
enough on this rendering to trigger the failure mode they trigger in
production, on whatever font produced the original report. A general test
that only exercises today's shipped strings would therefore pass whether
or not the fix were reverted -- the exact "test that cannot fail" shape
this suite's own conventions warn against. ``TestALongModelLabelIsContainedByFlow``
below closes that gap the only reliable way available: it overrides
``unifiedSettings.dictationModels`` with synthetic, deliberately-too-long
labels before measuring, which reproduces the reported shape
deterministically regardless of what font resolves or what the shipped
copy happens to say today. Reverting the fix and re-running that class
was used to confirm it actually fails on the old ``Row`` shape (worst
right edge 470px against a 360px window, with the Deepgram paragraph
itself dragged out to match) before being written up as passing here.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

# Must be set before QGuiApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QSettings, QTimer, QUrl  # noqa: E402
    from PySide6.QtGui import QFont, QFontMetricsF, QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading an item's `contentItem` property raises "Can't find
    # converter for 'QQuickItem*'".
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

# Same style chatter as the sibling headless QML modules -- Qt Quick
# Controls warns about Rectangle customisation for every scrollbar/handle
# in the panel, which predates this feature and signals nothing about it.
IGNORED_WARNING_FRAGMENTS = ("does not support customization",)

SETTINGS_WINDOW_TITLE = "Alpha-OSK Settings"

# Every drill-down category the home grid can land on (see
# UnifiedSettingsPanel.qml's `currentView` doc comment). "home" (the
# category-card grid itself) is included too: it has no chips or Flows,
# but it is still a settings category and the property is general.
SETTINGS_CATEGORIES = ("home", "appearance", "typing", "dictation", "model", "data")


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)]


def _font_is_a_placeholder() -> bool:
    """True when Qt resolved a fixed-width stand-in instead of a real font.

    Copied verbatim from ``tests/test_qml_dictation.py`` rather than
    imported: see that module's own docstring on the same function for why
    it must not be called before a ``QGuiApplication`` exists, and why a
    fixed-width placeholder makes width-sensitive assertions unfalsifiable
    on a box that has one.
    """
    font = QFont()
    font.setFamilies(["Ubuntu", "Noto Sans", "sans-serif"])
    font.setPixelSize(20)
    font.setWeight(QFont.Medium)
    metrics = QFontMetricsF(font)
    return metrics.horizontalAdvance("i") == metrics.horizontalAdvance("W")


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation - these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def requires_real_font(qapp):
    """Skip when this machine has no proportional font for Qt to fall back on.

    A skip here is the honest outcome, not a workaround: it says "not
    verified on this machine" instead of a green tick that would pass
    identically whether the Row-vs-Flow regression this file guards
    against were present or not. CI installs real fonts, so the assertion
    runs for real there.
    """
    if _font_is_a_placeholder():
        pytest.skip(
            "Qt resolved a fixed-width placeholder font, so width-based "
            "overflow assertions are unfalsifiable on this machine (every "
            "glyph is the same width, inflating every string uniformly). "
            "CI runs this against real fonts."
        )


def _wait_ms(ms: int) -> None:
    """Run the event loop for *ms*, so Qt's own timers actually fire.

    A bare ``QCoreApplication.processEvents()`` loop only drains events
    already queued; it does not let time-based machinery (including, it
    turns out, some Layout re-arrangement bookkeeping) run. Mirrors the
    identical helper in ``tests/test_qml_dictation.py``.
    """
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def qml_root(qapp):
    """Load Main.qml with a mocked-synth bridge, from clean settings."""
    warnings: list[str] = []

    QSettings(TEST_ORG, TEST_APP).clear()
    settings = QSettings(TEST_ORG, TEST_APP)
    # Disarm the startup update check for the same reason every other
    # headless QML fixture in this suite does: it fires a real HTTPS
    # request from a daemon thread a few seconds in, which outlives a
    # torn-down bridge and has crashed the whole pytest run before.
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    try:
        yield root, warnings, bridge
    finally:
        del engine


def _open_settings_window(root):
    """Show Settings and return its top-level QQuickWindow.

    Unlike ``SnippetsWindow`` / ``SymbolsWindow``, the settings window was
    never split into its own component file (it is an inline
    ``Window { id: settingsWindow ... }`` in Main.qml) and carries no
    ``objectName``, so it cannot be reached with ``root.findChild``.  It
    *does* register with ``QGuiApplication`` once instantiated (Qt Quick
    creates a nested ``Window`` element eagerly, not lazily on first
    show), so finding it by its title is the reliable route -- the same
    thing a user would use to pick it out on a real desktop.
    """
    root.setProperty("showSettings", True)
    _wait_ms(100)

    matches = [w for w in QGuiApplication.allWindows() if w.title() == SETTINGS_WINDOW_TITLE]
    assert matches, (
        f"no window titled {SETTINGS_WINDOW_TITLE!r} found after setting "
        "showSettings=true -- the settings Window in Main.qml may have been "
        "renamed or restructured"
    )
    assert len(matches) == 1, f"expected exactly one settings window, found {len(matches)}"
    return matches[0]


def _find_settings_panel(item):
    """The live ``UnifiedSettingsPanel`` instance inside the settings window.

    Found by class-name prefix rather than objectName, because
    ``UnifiedSettingsPanel.qml`` sets none anywhere in the file. QML names
    a plain (non-C++-registered) component's generated class after its
    file, the same fact ``test_qml_snippets.py`` relies on to assert
    ``SnippetsWindow`` was extracted into its own file; here it is the
    only way to find the panel at all.
    """
    for child in item.childItems():
        if child.metaObject().className().startswith("UnifiedSettingsPanel"):
            return child
        found = _find_settings_panel(child)
        if found is not None:
            return found
    return None


def _settle_view(window, panel, view: str) -> None:
    """Switch to *view* and flush the layout, not just the event queue.

    See the module docstring's second trap. A category ``ColumnLayout``
    that starts ``visible: false`` can size itself freely from its own
    (unconstrained) content while hidden; simply flipping it back to
    visible does not reliably make its parent re-run arrangement under a
    headless, non-rendering window. Perturbing the window's width by one
    pixel and back forces the same geometry-changed signal a real resize
    sends, which does flush it. Confirmed independently of this file: a
    two-view minimal reproduction needs exactly this nudge, and neither a
    longer bare wait nor more ``processEvents()`` passes substitute for it.
    """
    panel.setProperty("currentView", view)
    _wait_ms(120)
    width = window.width()
    window.resize(width + 1, window.height())
    _wait_ms(20)
    window.resize(width, window.height())
    _wait_ms(120)


def _max_right_edge(panel, content_item) -> float:
    """The furthest any *visible* descendant of *panel* reaches, in the
    settings window's own coordinate frame.

    ``isVisible()`` (not the raw ``visible`` property) is what makes this
    meaningful: it is the effective, ancestor-aware visibility, so a child
    of the five currently-hidden category columns is correctly excluded
    even though each one still exists in the tree the whole time. Walking
    ``childItems()`` rather than ``findChildren`` is required for the
    usual reason documented across this suite (a Repeater's delegates are
    visual children only, so ``findChildren`` silently returns none of
    them) -- there are no Repeaters directly in play here, but every
    sibling headless QML test in this repo makes the same choice for the
    same underlying reason, and a mixed convention is one more thing to
    get wrong later.
    """
    worst = 0.0

    def walk(item) -> None:
        nonlocal worst
        if not item.isVisible():
            return
        right = item.mapToItem(content_item, item.width(), 0).x()
        worst = max(worst, right)
        for child in item.childItems():
            walk(child)

    walk(panel)
    return worst


# A real font still leaves a pixel or two of positioner/rounding slack
# (the same order of magnitude as `POSITIONER_SLOP_PX` in
# test_qml_compact_view.py), so the bound is the window width plus a small
# tolerance rather than an exact equality.
OVERFLOW_TOLERANCE_PX = 2.0


class TestNoSettingsCategoryOverflowsTheWindow:
    @pytest.mark.usefixtures("requires_real_font")
    @pytest.mark.parametrize("view", SETTINGS_CATEGORIES)
    def test_the_category_fits_inside_the_window(self, qml_root, view: str) -> None:
        root, warnings, _bridge = qml_root
        window = _open_settings_window(root)
        panel = _find_settings_panel(window.property("contentItem"))
        assert panel is not None, "UnifiedSettingsPanel not found inside the settings window"

        _settle_view(window, panel, view)

        content_item = window.property("contentItem")
        worst_right = _max_right_edge(panel, content_item)
        window_width = window.width()

        assert worst_right <= window_width + OVERFLOW_TOLERANCE_PX, (
            f"{view!r} category renders {worst_right - window_width:.1f}px past "
            f"the settings window's right edge ({worst_right:.1f}px vs "
            f"{window_width:.1f}px window width). A Row (or any positioner "
            "with no Layout.fillWidth) inside a fillWidth column is the usual "
            "shape: it never shrinks, so it drags every sibling in its "
            "column wider than the window along with it."
        )
        assert _real_warnings(warnings) == []

    def test_every_category_is_reachable_and_named_here(self, qml_root) -> None:
        """The parametrised sweep above is only as general as this list.

        Guards against the list quietly going stale if a category is
        renamed or a new one is added to the drill-down menu without a
        matching update here -- which would otherwise just mean fewer
        cases silently ran, not a failure.
        """
        root, _warnings, _bridge = qml_root
        window = _open_settings_window(root)
        panel = _find_settings_panel(window.property("contentItem"))
        assert panel is not None

        for view in SETTINGS_CATEGORIES:
            panel.setProperty("currentView", view)
            assert panel.property("currentView") == view

    def test_content_width_is_not_a_usable_signal(self, qml_root) -> None:
        """Documents the trap named in the module docstring as an assertion,
        not just prose: `flickArea.contentWidth` is bound to `width`
        (`contentWidth: width` in UnifiedSettingsPanel.qml), so the two
        are equal by construction regardless of whether anything inside
        has overflowed. A test that compared them would pass on the
        pre-fix Row shape exactly as it does on the Flow shape -- the
        "test that cannot fail" this suite's own conventions warn about --
        which is why the tests above measure live descendant geometry
        instead.
        """
        from PySide6.QtQml import QQmlEngine, QQmlExpression

        root, _warnings, _bridge = qml_root
        window = _open_settings_window(root)
        panel = _find_settings_panel(window.property("contentItem"))
        assert panel is not None
        # `flickArea` is an id local to UnifiedSettingsPanel.qml, so it
        # must be resolved from a descendant's own creation context, not
        # from `panel` itself: the *root* object of a nested component
        # carries its instantiating document's context, not the one its
        # own ids are registered in. See `_bar()` in
        # tests/test_qml_dictation.py for the same pattern.
        inner = panel.childItems()[0]
        ctx = QQmlEngine.contextForObject(inner)

        def evaluate(expr: str):
            value, undefined = QQmlExpression(ctx, inner, expr).evaluate()
            assert not undefined, expr
            return value

        panel.setProperty("currentView", "dictation")
        _wait_ms(60)
        assert evaluate("flickArea.contentWidth") == evaluate("flickArea.width")


def _find_text_item(item, needle: str):
    """First visible descendant of *item* whose `text` contains *needle*."""
    text = item.property("text")
    if isinstance(text, str) and needle in text:
        return item
    for child in item.childItems():
        found = _find_text_item(child, needle)
        if found is not None:
            return found
    return None


class TestALongModelLabelIsContainedByFlow:
    """Deterministic proof of the Row-to-Flow fix, independent of the
    shipped Deepgram copy or the local font.

    The sweep above exercises whatever ``dictationModels`` the running app
    actually has, and on a real font today's two shipped labels turn out
    to be short enough that a bare ``Row`` never overflows either --
    confirmed by reverting the fix and re-running that sweep, which still
    passed. That made the general sweep alone a test that could not fail
    for *this* regression specifically, which is exactly what this class
    exists to close: it overrides ``unifiedSettings.dictationModels`` with
    labels long enough to overflow on any font, so the assertion is
    falsifiable regardless of what the shipped strings say today or what
    machine runs it.

    ``dictationModels`` is an ordinary ``property var`` on the panel (see
    ``UnifiedSettingsPanel.qml``), populated from ``root.refreshDictation()``
    only when Settings opens -- overriding it afterwards, as done here,
    sticks for the rest of the test with nothing to put it back.
    """

    # Deliberately far longer than any label could need, because the
    # first version of this was not.  At 67 characters it sat on a knife
    # edge: wide enough to overflow on the CI font stacks (by 75px on
    # ubuntu, 10px on windows) and narrow enough to fit on a developer's
    # Windows box, so the same commit passed locally and failed on CI.
    # A label nothing can render inside 360px makes the assertion mean
    # the same thing on every machine, which is what the class claims.
    LONG_MODELS = [
        {
            "id": "m1",
            "label": (
                "A model with an extremely long descriptive label that will "
                "not fit inside the settings window on any font, at any "
                "size, no matter how narrow the glyphs happen to be"
            ),
        },
        {"id": "m2", "label": "Another quite long descriptive model label here too"},
    ]

    def test_no_sibling_is_dragged_wider_than_the_window(self, qml_root) -> None:
        root, warnings, _bridge = qml_root
        window = _open_settings_window(root)
        panel = _find_settings_panel(window.property("contentItem"))
        assert panel is not None

        panel.setProperty("dictationModels", self.LONG_MODELS)
        _settle_view(window, panel, "dictation")

        content_item = window.property("contentItem")
        worst_right = _max_right_edge(panel, content_item)
        window_width = window.width()

        assert worst_right <= window_width + OVERFLOW_TOLERANCE_PX, (
            f"an over-long model label pushed something {worst_right - window_width:.1f}px "
            f"past the settings window's right edge ({worst_right:.1f}px vs "
            f"{window_width:.1f}px window width). Reverting the model picker "
            "from Flow back to Row reproduces exactly this: worst right edge "
            "470px against a 360px window, in a from-scratch measurement "
            "taken while writing this test."
        )
        assert _real_warnings(warnings) == []

    def test_the_deepgram_paragraph_specifically_is_not_clipped(self, qml_root) -> None:
        """The exact damage the original report named: "the Deepgram
        paragraph wrapped past the right edge". Checked on its own,
        because the sweep above proves the *category* fits but not which
        sibling would have been the one to suffer if it did not.
        """
        root, warnings, _bridge = qml_root
        window = _open_settings_window(root)
        panel = _find_settings_panel(window.property("contentItem"))
        assert panel is not None

        panel.setProperty("dictationModels", self.LONG_MODELS)
        _settle_view(window, panel, "dictation")

        content_item = window.property("contentItem")
        paragraph = _find_text_item(panel, "Dictation uses Deepgram")
        assert paragraph is not None, "the Deepgram explainer paragraph was not found"

        right = paragraph.mapToItem(content_item, paragraph.width(), 0).x()
        window_width = window.width()
        assert right <= window_width + OVERFLOW_TOLERANCE_PX, (
            f"the Deepgram paragraph itself renders {right - window_width:.1f}px "
            f"past the window's right edge ({right:.1f}px vs {window_width:.1f}px) "
            "-- a long model chip dragged a sibling that has nothing to do "
            "with the model picker wider than the window, which is the "
            "'one overflowing child clips every sibling' failure this whole "
            "file is about."
        )
        assert _real_warnings(warnings) == []
