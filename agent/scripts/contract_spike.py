"""Live contract spike for the OpenRouter decisions-modality endpoint (issue #9).

POSTs the PRD §10 batch wire format — two canonical questions (the 6-option
catalog choice per PRD §7 plus one noul) and a state snapshot — to the
decisions endpoint for DECISION_MODEL and records the raw response body to
`agent/tests/contract_snapshot.json` as {"meta", "response"} so
DecisionsModalityTransport.parse can be pinned against the real response
shape in tests (issue acceptance clause 1).

Modes (--transport):
  decisions (default) — the primary decisions-modality call only; writes the
      snapshot on success.
  chat        — probe the ChatStructuredTransport fallback chain instead
      (no snapshot; the snapshot records the decisions wire body).
  both        — decisions first; when it fails, engage the fallback chain
      exactly like decide() would and report whether chat saved the run.

Exit code is 0 when the primary succeeded (or, in `both` mode, when the
fallback chain rescued a primary failure), nonzero when the probed
transport(s) failed — so CI/CI-like callers can gate on spike health.

Usage (from the repo root or agent/):
  python agent/scripts/contract_spike.py [--transport both] [--output PATH]
      [--state-json '{"arr_trend": "up"}']

Reads OPENROUTER_API_KEY / DECISION_MODEL / OPENROUTER_BASE_URL /
DECISION_TIMEOUT_S from the environment after loading agent/.env. Without a
key the transports fail at construction with the agent's standard message —
nothing is written.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parent
for _path in (str(REPO_ROOT), str(AGENT_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Load agent/.env BEFORE importing the decision package — anything reading
# OPENROUTER_API_KEY must see the file's values (mirrors server.py's order).
from dotenv import load_dotenv

for _env_path in (AGENT_DIR / ".env", Path(".env")):
    if _env_path.is_file():
        load_dotenv(_env_path)
        break
else:
    load_dotenv()

try:  # package-style entry (import agent.decision from the repo root)
    from agent.decision import (
        ChatStructuredTransport,
        ChoiceQuestion,
        DecisionsModalityTransport,
        NoulQuestion,
    )
    from agent.decision.client import OPENROUTER_BASE_URL as DEFAULT_BASE_URL
except ModuleNotFoundError:  # script-style entry (import decision from agent/)
    from decision import (
        ChatStructuredTransport,
        ChoiceQuestion,
        DecisionsModalityTransport,
        NoulQuestion,
    )
    from decision.client import OPENROUTER_BASE_URL as DEFAULT_BASE_URL

# PRD §7 catalog — the choice question's options are exactly these 6 types.
CATALOG_OPTIONS = [
    "kpi_row",
    "line_chart",
    "variance_callout",
    "data_table",
    "data_quality_card",
    "insight_text",
]
DEFAULT_OUTPUT = AGENT_DIR / "tests" / "contract_snapshot.json"


def canonical_questions():
    """The spike's two-question set: the PRD §7 'which components' choice
    over the full 6-type catalog, plus one noul (yes/no) question."""
    return [
        ChoiceQuestion(
            id="which-components",
            prompt="Which catalog components does this query need?",
            options=CATALOG_OPTIONS,
        ),
        NoulQuestion(
            id="is-data-problem",
            prompt=(
                "Does the evidence point to a data problem (nulls, gaps, "
                "duplicates) rather than a business trend?"
            ),
        ),
    ]


def _load_state(state_json):
    """Parse --state-json as a JSON object literal, or read it as a file path
    when it is not valid JSON (so both '{"k": 1}' and state.json work)."""
    if not state_json:
        return {}
    try:
        state = json.loads(state_json)
    except ValueError:
        with open(state_json, encoding="utf-8") as handle:
            state = json.load(handle)
    if not isinstance(state, dict):
        raise SystemExit(f"--state-json must be a JSON object, got: {state!r}")
    return state


def _print_table(label, questions, mapping=None, elapsed_s=None, error=None):
    """One row per question: id, type, and the parsed probabilities (or the
    failure reason) — the spike is a human-readable contract probe first."""
    status = f"OK in {elapsed_s:.3f}s" if elapsed_s is not None else "FAILED"
    print(f"\n{label}: {status}")
    if error is not None:
        print(f"  error: {error}")
        return
    for question in questions:
        payload = mapping.get(question.id)
        if payload is None:
            print(f"  {question.id:<20} {question.type:<8} <no answer>")
            continue
        probabilities = payload.get("probabilities")
        if isinstance(probabilities, dict):
            rendered = ", ".join(
                f"{option}={value}" for option, value in probabilities.items()
            )
        else:
            rendered = repr(probabilities)
        print(f"  {question.id:<20} {question.type:<8} {rendered}")


async def _probe(transport, questions):
    """Run one transport's ask_batch, returning (mapping, elapsed_s) on
    success or (None, error-string) on failure."""
    start = time.monotonic()
    try:
        mapping = await transport.ask_batch(questions)
    except Exception as exc:  # the spike reports failures, never crashes
        return None, f"{type(exc).__name__}: {exc}"
    return mapping, time.monotonic() - start


def _write_snapshot(path, transport, questions, elapsed_s, note=""):
    """Record the raw decisions response body with provenance metadata — the
    {'meta', 'response'} wrapper _answers_mapping knows how to unwrap."""
    meta = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": transport.model,
        "url": f"{transport.base_url}/decisions",
        "source": (
            "live-openrouter"
            if transport.base_url == DEFAULT_BASE_URL
            else "custom-endpoint"
        ),
        "questions": [q.id for q in questions],
        "elapsed_s": round(elapsed_s, 3),
    }
    if note:
        meta["note"] = note
    payload = {"meta": meta, "response": transport.last_raw_response}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nsnapshot written: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Live contract spike: POST the PRD §10 batch wire format to the "
            "OpenRouter decisions-modality endpoint and record the raw "
            "response shape to agent/tests/contract_snapshot.json."
        )
    )
    parser.add_argument(
        "--transport",
        choices=("decisions", "chat", "both"),
        default="decisions",
        help="decisions = primary only (default); chat = fallback chain only; "
        "both = primary, falling back to chat when it fails",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"snapshot path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--state-json",
        default="{}",
        help="state snapshot as a JSON object literal or a path to a .json file",
    )
    parser.add_argument(
        "--note",
        default="",
        help="free-form provenance note recorded verbatim in the snapshot meta",
    )
    args = parser.parse_args(argv)
    state = _load_state(args.state_json)
    questions = canonical_questions()

    # Acceptance clause 4: without a key the transports fail at construction
    # with the standard RuntimeError — report it cleanly instead of a traceback.
    try:
        transport = DecisionsModalityTransport(state=state)
        chat = ChatStructuredTransport(state=state)
    except RuntimeError as exc:
        print(f"spike aborted: {exc}", file=sys.stderr)
        return 1

    async def run():
        decisions_mapping, decisions_elapsed = await _probe(transport, questions)
        _print_table(
            "decisions-modality transport "
            f"({transport.model} @ {transport.base_url}/decisions)",
            questions,
            decisions_mapping,
            decisions_elapsed,
            error=None if decisions_mapping else decisions_elapsed,
        )
        outcome = {
            "decisions_ok": decisions_mapping is not None,
            "decisions_elapsed": decisions_elapsed if decisions_mapping else None,
            "chat_ok": None,
        }
        if args.transport in ("chat", "both"):
            chat_mapping, chat_elapsed = await _probe(chat, questions)
            _print_table(
                f"chat fallback chain ({', '.join(chat.models)})",
                questions,
                chat_mapping,
                chat_elapsed if chat_mapping else None,
                error=None if chat_mapping else chat_elapsed,
            )
            outcome["chat_ok"] = chat_mapping is not None
        return outcome

    try:
        outcome = asyncio.run(run())
    finally:
        asyncio.run(transport.aclose())
        asyncio.run(chat.aclose())

    exit_code = 0
    if outcome["decisions_ok"]:
        _write_snapshot(
            Path(args.output),
            transport,
            questions,
            outcome["decisions_elapsed"],
            note=args.note,
        )
    elif args.transport == "both" and outcome["chat_ok"]:
        # Primary failed but the fallback chain rescued the run — that is the
        # resilience the chain exists for, so the spike still exits 0.
        print("\nprimary failed; the chat fallback chain engaged successfully")
    else:
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
