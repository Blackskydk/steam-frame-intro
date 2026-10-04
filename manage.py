#!/usr/bin/python3
"""Run the project directly; no pip installation is needed."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from frame_intro.cli import main

if __name__ == '__main__':
    main()
