"""Process entry point of the pointer-clip guard: ``<parent_pid> <heartbeat_name>``."""

from __future__ import annotations

import sys

from src.platform.pointer_clip import guard_main

if __name__ == "__main__":
    sys.exit(guard_main(sys.argv[1:]))
