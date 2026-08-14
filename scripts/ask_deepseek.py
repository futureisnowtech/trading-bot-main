#!/usr/bin/env python3
"""ask_deepseek.py — terminal-side DeepSeek access for ad-hoc data analysis.

Read-only by design: no trading tools are exposed, so this can never place,
cancel, or mutate anything. It only reasons over text you hand it.

Run it from the repo root so config.load_dotenv() picks up .env:

    python3 scripts/ask_deepseek.py "which of these settlement rows look wrong?"
    cat runtime_report.json | python3 scripts/ask_deepseek.py "summarize the blockers"
    python3 scripts/ask_deepseek.py -f logs/forecast.log "why did entries stop?"

The provider is forced to DeepSeek regardless of REASONING_PROVIDER, so an
operator analysing data here never disturbs whichever backend the live brain is
configured to use.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Set before importing the provider so every helper resolves the DeepSeek branch.
os.environ["REASONING_PROVIDER"] = "deepseek"

import config  # noqa: F401,E402  (import side effect: loads .env)
from runtime.reasoning_provider import (  # noqa: E402
    HAS_OPENAI_SDK,
    get_deepseek_base_url,
    get_deepseek_reasoning_effort,
    get_deepseek_thinking_mode,
    get_reasoning_model_id,
)

DEFAULT_SYSTEM = (
    "You are a data analyst for a Kalshi weather-derivatives trading system. "
    "Answer only from the data provided. State plainly when the data does not "
    "support a conclusion rather than guessing, and show the arithmetic behind "
    "any number you report."
)

MAX_ATTACHMENT_CHARS = 400_000


def _read_attachment(path: str) -> str:
    resolved = Path(path).expanduser()
    text = resolved.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_ATTACHMENT_CHARS:
        text = text[-MAX_ATTACHMENT_CHARS:]
        header = f"--- {resolved} (truncated to last {MAX_ATTACHMENT_CHARS} chars) ---"
    else:
        header = f"--- {resolved} ---"
    return f"{header}\n{text}"


def build_prompt(question: str, attachments: list[str], piped: str) -> str:
    blocks = []
    if piped.strip():
        blocks.append(f"--- piped stdin ---\n{piped.strip()}")
    blocks.extend(attachments)
    if blocks:
        return "\n\n".join(blocks) + f"\n\n--- question ---\n{question}"
    return question


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ask DeepSeek a question about local data. Read-only, no trading tools.",
    )
    parser.add_argument("prompt", nargs="*", help="The question. Data can also be piped in or attached with -f.")
    parser.add_argument("-f", "--file", action="append", default=[], help="Attach a file's contents. Repeatable.")
    parser.add_argument("--model", default="", help=f"Override the model (default: {config.DEEPSEEK_MODEL}).")
    parser.add_argument("--system", default=DEFAULT_SYSTEM, help="Override the system instruction.")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature (default: 0.2).")
    parser.add_argument("--effort", default="", choices=("", "low", "medium", "high"), help="Override reasoning effort.")
    parser.add_argument("--max-tokens", type=int, default=0, help="Cap the response length. 0 leaves it to the model.")
    parser.add_argument("--show-config", action="store_true", help="Print the resolved endpoint config and exit.")
    args = parser.parse_args()

    if args.model.strip():
        os.environ["DEEPSEEK_MODEL"] = args.model.strip()
    if args.effort:
        os.environ["DEEPSEEK_REASONING_EFFORT"] = args.effort

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    model_id = get_reasoning_model_id()

    if args.show_config:
        print(f"provider   : deepseek")
        print(f"model      : {model_id}")
        print(f"base_url   : {get_deepseek_base_url()}")
        print(f"effort     : {get_deepseek_reasoning_effort() or '(omitted)'}")
        print(f"thinking   : {get_deepseek_thinking_mode() or '(omitted)'}")
        print(f"api_key    : {'set' if api_key else 'MISSING'}")
        print(f"openai sdk : {'installed' if HAS_OPENAI_SDK else 'MISSING'}")
        return 0 if (api_key and HAS_OPENAI_SDK) else 1

    if not api_key:
        print("DEEPSEEK_API_KEY is not set. Add it to .env and run from the repo root.", file=sys.stderr)
        return 1
    if not HAS_OPENAI_SDK:
        print("openai SDK not installed. pip install -r requirements-runtime.txt", file=sys.stderr)
        return 1

    piped = "" if sys.stdin.isatty() else sys.stdin.read()
    question = " ".join(args.prompt).strip()
    if not question and not piped.strip() and not args.file:
        parser.error("nothing to analyse: pass a question, pipe data in, or attach a file with -f")
    if not question:
        question = "Analyse the data above and report what stands out."

    try:
        attachments = [_read_attachment(path) for path in args.file]
    except OSError as exc:
        print(f"Could not read attachment: {exc}", file=sys.stderr)
        return 1

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=get_deepseek_base_url())
    request = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": args.system},
            {"role": "user", "content": build_prompt(question, attachments, piped)},
        ],
        "temperature": args.temperature,
        "stream": False,
    }
    effort = get_deepseek_reasoning_effort()
    if effort:
        request["reasoning_effort"] = effort
    thinking = get_deepseek_thinking_mode()
    if thinking:
        request["extra_body"] = {"thinking": {"type": thinking}}
    if args.max_tokens > 0:
        request["max_tokens"] = args.max_tokens

    try:
        response = client.chat.completions.create(**request)
    except Exception as exc:
        print(f"DeepSeek request failed: {exc}", file=sys.stderr)
        return 1

    text = str(response.choices[0].message.content or "").strip()
    if not text:
        print("DeepSeek returned an empty response.", file=sys.stderr)
        return 1

    print(text)
    usage = getattr(response, "usage", None)
    if usage is not None:
        print(
            f"\n[{model_id} | prompt {usage.prompt_tokens} | completion {usage.completion_tokens}]",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
