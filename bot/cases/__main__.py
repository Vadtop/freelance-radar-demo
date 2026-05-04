from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bot.cases.seed import seed_cases


if __name__ == "__main__":
    asyncio.run(seed_cases())
