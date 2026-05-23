import asyncio
import json
import os
import time
import logging
from typing import Dict, Any

# Ensure project root is in path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notifications.ai_agent import ask_ai

logger = logging.getLogger(__name__)
CACHE_FILE = "logs/cached_macro_regime.json"

async def build_macro_context():
    """
    v18.34: Build macro context using Gemini + yfinance tools.
    """
    prompt = (
        "Query the current market regime using yfinance tools. "
        "I need: 1) SPY trend/price, 2) 10-year Treasury yield, 3) VIX level, 4) Top 3 financial headlines. "
        "Summarize into a JSON with keys: spy_trend, treasury_yield, vix_regime, headlines (list), "
        "and a risk_score (0-10, 10=high risk/panic). Respond ONLY with the JSON."
    )
    
    try:
        # v18.34: Implementation of Phase 3 Hardening
        # Standardize on a max 30s timeout for the Macro AI context builder.
        # Note: ask_ai currently lacks a timeout param, so we wrap it in asyncio loop 
        # executor or similar if needed, but since it's an external library call,
        # we'll use concurrent.futures if necessary. For now, we'll wrap the logic.
        
        loop = asyncio.get_event_loop()
        res_text = await asyncio.wait_for(
            loop.run_in_executor(None, ask_ai, prompt), 
            timeout=30.0
        )
        
        # Extract JSON (strip potential markdown blocks)
        res_text = res_text.replace("```json", "").replace("```", "").strip()
        
        # Robust parsing
        if "{" in res_text and "}" in res_text:
            start = res_text.find("{")
            end = res_text.rfind("}") + 1
            res_text = res_text[start:end]
            
        data = json.loads(res_text)
        data["updated_at"] = time.time()
        
        os.makedirs("logs", exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"[macro_cron] Successfully updated macro cache: risk_score={data.get('risk_score')}")
    except Exception as e:
        logger.error(f"[macro_cron] Macro context build failed: {e}")

async def main():
    logger.info("🚀 Starting Macro Context Cron (30-min cycle)")
    while True:
        await build_macro_context()
        await asyncio.sleep(1800) # 30 mins

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(main())
