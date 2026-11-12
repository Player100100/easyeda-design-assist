"""Run from any directory: python -B path/to/src/design_assist.py --help."""
import sys

sys.dont_write_bytecode = True

from easyeda_assist.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
