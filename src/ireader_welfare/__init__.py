#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ireader_welfare —— 掌阅福利中心自动签到。"""

from .auth import Credential, login
from .welfare import WelfareClient

__version__ = "1.3.0"
__all__ = ["login", "Credential", "WelfareClient", "__version__"]