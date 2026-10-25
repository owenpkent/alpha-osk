"""Measure native memory growth while typing into the real bridge and Main.qml.

Issue #204: typing with the suggestion bar live grows private bytes by about
0.5 MB per word, against a flat QObject count. This drives the real
KeyboardBridge and qml/Main.qml offscreen, types seeded random words (a pill
tap every Nth word) and samples private bytes every --sample-every words.
Growth is reported after the first --warmup words so one-off caches are
excluded.

Everything runs headless against a throwaway copy of the config directory;
the real %APPDATA%/alpha-osk is only read (models, snippets, analytics) and
never written. Secrets (dictation.json, telemetry.json) are not copied.

Examples:
    python scripts/bench/qml_memory.py
    python scripts/bench/qml_memory.py --hide-bar
    python scripts/bench/qml_memory.py --recycle 20 --words 300
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parents[2]
_COPIED = ("models", "snippets.json", "analytics.json", "key_actions.json")


def _real_config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "alpha-osk"


def _mb(n: int) -> float:
    return n / (1024 * 1024)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--words", type=int, default=300)
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--sample-every", type=int, default=50)
    ap.add_argument(
        "--pill-every", type=int, default=3, help="tap a pill every N words (0 = never)"
    )
    ap.add_argument("--seed", type=int, default=204)
    ap.add_argument("--hide-bar", action="store_true", help="set suggestionsEnabled false on root")
    ap.add_argument("--recycle", type=int, default=0, help="draw from only N distinct words")
    ap.add_argument("--no-qml", action="store_true", help="bridge only, no Main.qml")
    ap.add_argument("--grab-every", type=int, default=10, help="grabWindow every N words (0=never)")
    ap.add_argument("--gc-end", action="store_true", help="collectGarbage + trim at the end")
    args = ap.parse_args()

    import psutil

    real = _real_config_dir()
    with tempfile.TemporaryDirectory(prefix="aosk-qmlmem-") as tmp:
        tmp_path = Path(tmp)
        cfg = tmp_path / "appdata" / "alpha-osk"
        cfg.mkdir(parents=True)
        for name in _COPIED:
            src = real / name
            if src.is_dir():
                shutil.copytree(src, cfg / name)
            elif src.is_file():
                shutil.copy2(src, cfg / name)
        os.environ["APPDATA"] = str(tmp_path / "appdata")

        sys.path.insert(0, str(ROOT))
        from PySide6.QtCore import QCoreApplication, QSettings, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuick import QQuickItem  # noqa: F401

        from tests.qt_settings_scope import TEST_APP, TEST_ORG

        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
        assert QCoreApplication.organizationName() == TEST_ORG

        def fake_cfg() -> Path:
            cfg.mkdir(parents=True, exist_ok=True)
            return cfg

        import src.keyboard_app  # noqa: F401

        patches = [
            patch("src.platform.get_config_dir", fake_cfg),
            patch("src.snippets.get_config_dir", fake_cfg),
            patch("src.key_actions.get_config_dir", fake_cfg),
            patch("src.keyboard_app.get_config_dir", fake_cfg),
            patch("src.keyboard_bridge.is_password_field", lambda: False),
            patch("src.keyboard_bridge.external_click_detected", lambda: False),
            patch("src.keyboard_bridge.caret_position_token", lambda: None),
        ]
        for p in patches:
            p.start()
        try:
            QSettings(TEST_ORG, TEST_APP).clear()
            s = QSettings(TEST_ORG, TEST_APP)
            s.setValue("ui/savedAutoCheckUpdates", False)
            s.sync()

            from src.keyboard_bridge import KeyboardBridge

            with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
                synth = MagicMock()
                synth.is_available.return_value = True
                synth.backend_name.return_value = "MockSynth"
                factory.return_value = synth
                bridge = KeyboardBridge()

            engine = None
            root = None
            if not args.no_qml:
                from tests.qml_context import install_context_properties

                engine = QQmlApplicationEngine()
                install_context_properties(engine, bridge)
                engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))
                if not engine.rootObjects():
                    print("Main.qml failed to load", file=sys.stderr)
                    return 1
                root = engine.rootObjects()[0]
                if args.hide_bar:
                    root.setProperty("suggestionsEnabled", False)
            for _ in range(2000):
                if bridge.predictionStatus == "ready":
                    break
                QCoreApplication.processEvents()
                time.sleep(0.01)
            assert bridge.predictionStatus == "ready", bridge.predictionStatus
            if root is not None and args.hide_bar:
                print("suggestionsEnabled =", root.property("suggestionsEnabled"))

            words = [
                w.strip()
                for w in (ROOT / "data" / "google-10000-english-usa-no-swears.txt")
                .read_text(encoding="utf-8")
                .splitlines()
                if w.strip().isalpha() and 3 <= len(w.strip()) <= 12
            ]
            rng = random.Random(args.seed)
            pool = rng.sample(words, args.recycle) if args.recycle else words

            proc = psutil.Process()

            def private() -> int:
                info = proc.memory_full_info()
                return getattr(info, "private", getattr(info, "uss", info.rss))

            samples: list[tuple[int, int]] = []
            taps = 0
            for i in range(1, args.words + 1):
                w = rng.choice(pool)
                for ch in w:
                    bridge.pressKey(ch)
                    QCoreApplication.processEvents()
                if args.pill_every and i % args.pill_every == 0 and bridge.predictions:
                    bridge.pressPrediction(bridge.predictions[0])
                    taps += 1
                else:
                    bridge.pressSpecialKey("space")
                QCoreApplication.processEvents()
                if root is not None and args.grab_every and i % args.grab_every == 0:
                    root.grabWindow()
                if i % args.sample_every == 0 or i == args.warmup:
                    samples.append((i, private()))
            if args.gc_end and engine is not None:
                engine.collectGarbage()
                engine.trimComponentCache()
                QCoreApplication.processEvents()
                samples.append((args.words + 1, private()))

            print(
                f"hide_bar={args.hide_bar} recycle={args.recycle} no_qml={args.no_qml} "
                f"pill_every={args.pill_every} taps={taps}"
            )
            for n, b in samples:
                print(f"  word {n:4d}: {_mb(b):8.1f} MB")
            base = dict(samples).get(args.warmup)
            last = samples[-1][1]
            if base is not None:
                grow = _mb(last - base)
                per = grow / max(1, args.words - args.warmup)
                print(f"growth after word {args.warmup}: {grow:.1f} MB ({per:.3f} MB/word)")
            del engine
            app.processEvents()
        finally:
            for p in reversed(patches):
                p.stop()
            QSettings(TEST_ORG, TEST_APP).clear()
    return 0


if __name__ == "__main__":
    sys.exit(main())
