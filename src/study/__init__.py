"""Research study harness.

Implements the protocol in ``docs/research/STUDY_PROTOCOL.md``: a copy-typing
task battery that measures what fraction of the offline benchmark's keystroke
savings a real participant actually realises.

The package is deliberately independent of Qt and of the keyboard bridge.
:mod:`metrics` is pure functions over recorded trials, :mod:`phrases` is data
plus a contamination check, :mod:`session` is a state machine over a condition
list, and :mod:`config` is the only module that touches disk.  The QML surface
lives behind ``src/study_bridge.py``, so the harness can be driven, tested and
re-analysed with no display attached.

An explicit ``__init__.py`` rather than an implicit namespace package, matching
``src/prediction`` and ``src/dictation``: PyInstaller resolves a regular package
reliably and can silently miss a namespace one, and this code has to survive
being frozen.
"""
