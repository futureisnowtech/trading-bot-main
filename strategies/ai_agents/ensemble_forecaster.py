"""
strategies/ai_agents/ensemble_forecaster.py — Multi-LLM prediction market forecaster.

Queries multiple LLMs in parallel and aggregates their probability estimates.
Designed for prediction markets (Lane 3) where the question is binary: YES or NO.

Primary: Claude (Anthropic) — always available, uses our existing ANTHROPIC_API_KEY.
Optional: GPT-4o (OpenAI) — add OPENAI_API_KEY to .env to enable.
Optional: Gemini (Google) — add GOOGLE_API_KEY to .env to enable.

Gracefully degrades: if only Claude is configured, system works fine with 1 model.
Adaptive weights: updated by forecast_calibrator.py based on Brier scores per model.

Adapted from Fully-Autonomous-Polymarket-AI-Trading-Bot/src/forecast/ensemble.py
(sync instead of async; our config; our SQLite agent_stats; Claude-first).
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    OPENAI_API_KEY, GOOGLE_API_KEY,
    ENSEMBLE_CLAUDE_WEIGHT, ENSEMBLE_GPT_WEIGHT, ENSEMBLE_GEMINI_WEIGHT,
    PM_ENSEMBLE_MIN_MODELS, PM_LLM_TEMPERATURE, PM_LLM_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class ModelForecast:
    model_name: str
    probability: float          # 0.01–0.99 for YES outcome
    confidence: str = "LOW"     # LOW | MEDIUM | HIGH
    reasoning: str = ""
    invalidation_triggers: list[str] = field(default_factory=list)
    error: str = ""
    latency_ms: float = 0.0


@dataclass
class EnsembleForecast:
    """Aggregated forecast from all models."""
    probability: float          # final calibrated-ready probability
    confidence: str = "LOW"
    individual: list[ModelForecast] = field(default_factory=list)
    models_ok: int = 0
    models_failed: int = 0
    spread: float = 0.0         # disagreement: max - min probability
    agreement_score: float = 1.0
    reasoning: str = ""
    method: str = "weighted"
    error: str = ""


# ── Prompt ───────────────────────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
You are an expert probabilistic forecaster for binary prediction markets.

QUESTION: {question}
CATEGORY: {category}

MARKET DATA:
- Current market-implied probability (YES): {market_prob:.1%}
- 24h volume: ${volume_usd:,.0f}
- Days until resolution: {days_to_expiry:.1f}
- Spread: {spread:.1%}

CONTEXT:
{context}

TASK:
Produce an INDEPENDENT probability estimate for this question resolving YES.
Do NOT anchor to the current market price — form your own view.

Rules:
- Probability must be 0.01 to 0.99
- If you are uncertain, stay close to 0.50
- confidence HIGH = primary source data directly answers the question
- confidence MEDIUM = strong indirect evidence
- confidence LOW = limited or conflicting evidence

Return ONLY valid JSON (no markdown fences):
{{
  "probability": <0.01-0.99>,
  "confidence": "LOW" | "MEDIUM" | "HIGH",
  "reasoning": "2-3 sentence explanation",
  "invalidation_triggers": ["event that would flip this forecast"]
}}
"""


def _build_prompt(
    question: str,
    category: str,
    market_prob: float,
    volume_usd: float,
    days_to_expiry: float,
    spread: float,
    context: str = "",
) -> str:
    return _PROMPT_TEMPLATE.format(
        question=question,
        category=category,
        market_prob=market_prob,
        volume_usd=volume_usd,
        days_to_expiry=days_to_expiry,
        spread=spread,
        context=context or "No additional context available.",
    )


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n", 1)
        raw = lines[1] if len(lines) > 1 else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return json.loads(raw.strip())


# ── Individual model queries ──────────────────────────────────────────────────

def _query_claude(prompt: str) -> ModelForecast:
    start = time.monotonic()
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=PM_LLM_MAX_TOKENS,
            temperature=PM_LLM_TEMPERATURE,
            system="You are a calibrated probabilistic forecaster. Return only valid JSON.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text if resp.content else "{}"
        parsed = _parse_json(raw)
        return ModelForecast(
            model_name="claude",
            probability=max(0.01, min(0.99, float(parsed.get("probability", 0.5)))),
            confidence=parsed.get("confidence", "LOW"),
            reasoning=parsed.get("reasoning", ""),
            invalidation_triggers=parsed.get("invalidation_triggers", []),
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception as e:
        return ModelForecast(
            model_name="claude", probability=0.5, error=str(e),
            latency_ms=(time.monotonic() - start) * 1000,
        )


def _query_openai(prompt: str) -> ModelForecast:
    start = time.monotonic()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o",
            temperature=PM_LLM_TEMPERATURE,
            max_tokens=PM_LLM_MAX_TOKENS,
            messages=[
                {"role": "system", "content": "You are a calibrated probabilistic forecaster. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = _parse_json(raw)
        return ModelForecast(
            model_name="gpt-4o",
            probability=max(0.01, min(0.99, float(parsed.get("probability", 0.5)))),
            confidence=parsed.get("confidence", "LOW"),
            reasoning=parsed.get("reasoning", ""),
            invalidation_triggers=parsed.get("invalidation_triggers", []),
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except ImportError:
        return ModelForecast(model_name="gpt-4o", probability=0.5,
                             error="openai package not installed", latency_ms=0)
    except Exception as e:
        return ModelForecast(model_name="gpt-4o", probability=0.5, error=str(e),
                             latency_ms=(time.monotonic() - start) * 1000)


def _query_gemini(prompt: str) -> ModelForecast:
    start = time.monotonic()
    try:
        import google.generativeai as genai
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-pro")
        resp = model.generate_content(
            f"You are a calibrated probabilistic forecaster. Return only valid JSON.\n\n{prompt}"
        )
        raw = resp.text or "{}"
        parsed = _parse_json(raw)
        return ModelForecast(
            model_name="gemini",
            probability=max(0.01, min(0.99, float(parsed.get("probability", 0.5)))),
            confidence=parsed.get("confidence", "LOW"),
            reasoning=parsed.get("reasoning", ""),
            invalidation_triggers=parsed.get("invalidation_triggers", []),
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except ImportError:
        return ModelForecast(model_name="gemini", probability=0.5,
                             error="google-generativeai package not installed", latency_ms=0)
    except Exception as e:
        return ModelForecast(model_name="gemini", probability=0.5, error=str(e),
                             latency_ms=(time.monotonic() - start) * 1000)


# ── Aggregation ───────────────────────────────────────────────────────────────

def _weighted_average(forecasts: list[ModelForecast]) -> float:
    """Weighted average using configured per-model weights."""
    weights = {
        "claude":  ENSEMBLE_CLAUDE_WEIGHT,
        "gpt-4o":  ENSEMBLE_GPT_WEIGHT,
        "gemini":  ENSEMBLE_GEMINI_WEIGHT,
    }
    total_w = 0.0
    total_wp = 0.0
    for f in forecasts:
        w = weights.get(f.model_name, 1.0 / len(forecasts))
        total_w  += w
        total_wp += f.probability * w
    return total_wp / total_w if total_w > 0 else 0.5


# ── Main forecaster ──────────────────────────────────────────────────────────

def forecast(
    question: str,
    category: str = "UNKNOWN",
    market_prob: float = 0.5,
    volume_usd: float = 0.0,
    days_to_expiry: float = 7.0,
    spread: float = 0.02,
    context: str = "",
    adaptive_weights: Optional[dict[str, float]] = None,
) -> EnsembleForecast:
    """
    Query all configured LLMs in parallel and return an aggregated forecast.

    Always queries Claude. Queries GPT-4o if OPENAI_API_KEY is set.
    Queries Gemini if GOOGLE_API_KEY is set.

    Falls back gracefully — if all models fail, returns probability=0.5 (no edge).
    """
    prompt = _build_prompt(question, category, market_prob, volume_usd, days_to_expiry, spread, context)

    # Determine which models to query
    queries: dict[str, callable] = {}
    if ANTHROPIC_API_KEY:
        queries["claude"] = lambda: _query_claude(prompt)
    if OPENAI_API_KEY:
        queries["gpt-4o"] = lambda: _query_openai(prompt)
    if GOOGLE_API_KEY:
        queries["gemini"] = lambda: _query_gemini(prompt)

    if not queries:
        logger.error("[ensemble_forecaster] No API keys configured. Need at least ANTHROPIC_API_KEY.")
        return EnsembleForecast(probability=0.5, error="No API keys configured")

    # Run queries in parallel (thread pool — safe since we're sync)
    forecasts: list[ModelForecast] = []
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        futures = {pool.submit(fn): name for name, fn in queries.items()}
        for future in as_completed(futures, timeout=45):
            try:
                forecasts.append(future.result())
            except Exception as e:
                name = futures[future]
                logger.warning(f"[ensemble_forecaster] {name} query error: {e}")
                forecasts.append(ModelForecast(model_name=name, probability=0.5, error=str(e)))

    successes = [f for f in forecasts if not f.error]
    failures  = [f for f in forecasts if f.error]

    for f in failures:
        logger.warning(f"[ensemble_forecaster] {f.model_name} failed: {f.error}")

    if len(successes) < PM_ENSEMBLE_MIN_MODELS:
        logger.warning(f"[ensemble_forecaster] Only {len(successes)} models succeeded (need {PM_ENSEMBLE_MIN_MODELS})")
        if not successes:
            return EnsembleForecast(
                probability=0.5, confidence="LOW",
                individual=forecasts, models_ok=0, models_failed=len(failures),
                error="All models failed",
            )

    # Apply adaptive weights if provided (from forecast_calibrator)
    if adaptive_weights:
        total_w = sum(adaptive_weights.get(f.model_name, 1.0) for f in successes)
        prob = sum(f.probability * adaptive_weights.get(f.model_name, 1.0) for f in successes) / total_w
    else:
        prob = _weighted_average(successes)

    # Spread = disagreement
    probs = [f.probability for f in successes]
    spread_val = max(probs) - min(probs) if len(probs) > 1 else 0.0
    agreement  = max(0.0, 1.0 - spread_val * 2)

    # Aggregate confidence (downgrade if models disagree)
    conf_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    avg_conf = sum(conf_order.get(f.confidence, 0) for f in successes) / len(successes)
    if spread_val > 0.15 or avg_conf < 0.5:
        confidence = "LOW"
    elif avg_conf >= 1.5:
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"

    reasoning = " | ".join(f.reasoning for f in successes if f.reasoning)[:400]

    result = EnsembleForecast(
        probability=round(max(0.01, min(0.99, prob)), 4),
        confidence=confidence,
        individual=forecasts,
        models_ok=len(successes),
        models_failed=len(failures),
        spread=round(spread_val, 4),
        agreement_score=round(agreement, 3),
        reasoning=reasoning,
        method="weighted",
    )

    logger.info(
        f"[ensemble_forecaster] {question[:60]}… → "
        f"p={result.probability:.3f} conf={confidence} "
        f"models={len(successes)}/{len(forecasts)} spread={spread_val:.3f}"
    )
    return result


def get_edge_vs_market(ensemble: EnsembleForecast, market_prob: float) -> float:
    """
    Our edge = our probability estimate - market implied probability.
    Positive = we think YES is more likely than the market does.
    We only trade when abs(edge) > PM_MIN_EDGE_PCT (3%).
    """
    return ensemble.probability - market_prob
