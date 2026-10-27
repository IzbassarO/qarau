import os
import sys

# WeasyPrint (the PDF report) finds Pango and GObject by name through cffi, which falls back to
# ctypes.util.find_library; on macOS that reads DYLD_FALLBACK_LIBRARY_PATH at call time, and
# Homebrew on Apple silicon installs to /opt/homebrew/lib, outside dyld's default fallback. Set
# here, not in a shell profile or devteam's env: SIP drops DYLD_* when /bin/sh starts, so Kerege's
# `sh -c` checks and an agent's shell would lose it. Linux/Docker: apt packages, nothing to do.
if sys.platform == "darwin" and os.path.isdir("/opt/homebrew/lib"):
    _fallback = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if "/opt/homebrew/lib" not in _fallback.split(":"):
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
            p for p in (_fallback, "/opt/homebrew/lib") if p
        )
