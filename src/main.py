#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""源码目录的入口转发，`python src/main.py` 可直接运行。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ireader_welfare.cli import main

if __name__ == "__main__":
    sys.exit(main())