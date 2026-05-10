#!/usr/bin/env python3
# This file is part of Xpra.
# Copyright (C) 2026 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# legacy shim
# for historical reasons, xpra.scripts.main has main function that takes two arguments,
# this does not fit the usual pattern, but this one does:


def main(argv: list[str]):
    from xpra.scripts.main import main as legacy_main
    return legacy_main(argv[0], argv[1:])


if __name__ == "__main__":  # pragma: no cover
    import sys
    code = main(["xpra.exe"] + sys.argv)
    if not code:
        code = 0
    sys.exit(code)
