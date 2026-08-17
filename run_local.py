"""Quick local test — one tick only."""
import asyncio
import sys
import os
import logging

sys.path.insert(0, "src")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from aegis.worker import AutonomousWorker

async def main():
    w = AutonomousWorker()
    print(f"Symbols: {w.symbols}")
    print(f"LLM: {w.llm_model} @ {w.llm_base_url}")
    print(f"Capital: R$ {w.capital}")
    print("--- Running single tick ---")
    await w._tick()
    print("--- State ---")
    import json
    print(json.dumps(w.state, indent=2, default=str))

asyncio.run(main())
