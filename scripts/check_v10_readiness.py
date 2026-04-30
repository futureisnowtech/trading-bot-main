#!/usr/bin/env python3
"""
Compatibility wrapper.

The old v10 readiness model is no longer authoritative.
Use the current spot truth-lane readiness snapshot instead.
"""

from __future__ import annotations

from scripts.check_readiness import main as check_readiness_main


def main() -> int:
    print(
        "check_v10_readiness.py is deprecated. "
        "Routing to the current spot truth-lane readiness check.\n"
    )
    return check_readiness_main()


if __name__ == "__main__":
    raise SystemExit(main())
