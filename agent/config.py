"""The agent's environment-variable contract (issue #19).

One registry of every variable the agent reads from the environment
(agent/.env, docker-compose's env_file). The owner-developer swaps the
decision/text models and the endorsement threshold by editing agent/.env
only — no code edits, no image rebuild (`docker compose up -d agent`
recreates the container) — and this module is the machine-checkable half of
that contract: tests/test_env_contract.py pins it against BOTH the literal
os.getenv / os.environ reads across the agent's modules AND the keys listed
in agent/.env.example; the three sets must be exactly equal, so a variable
can ship neither unregistered, nor undocumented, nor uncommented.

Deliberately NOT a settings module: the owning modules keep reading env at
their use sites because their failure policies differ by design —
DecisionClient fails loud on a bad DECISION_THRESHOLD at construction, the
layout node's cut degrades to the default with a warning, server.py's bind
address falls through `or` chains. Centralizing the reads would force one
policy on all of them (and rewrite four modules on a verify-and-document
issue); config.py only documents name, default, requiredness and purpose.
Stdlib only — no imports from the agent's own modules — so any test or
operator script can import it without the model/db stack.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvVar:
    """One env variable row: name + effective default + operator-facing
    documentation. `default` is the exact STRING the variable reads as when
    unset (an empty string for pure opt-ins), so tests can compare it
    against agent/.env.example verbatim and float()/int() it the way the
    owning module would."""

    name: str
    default: str
    required: bool
    purpose: str


# Defaults mirrored from the owning modules; tests/test_env_contract.py pins
# each against its owner's constant so the two cannot drift.
_OPENROUTER_BASE_URL_DEFAULT = "https://openrouter.ai/api/v1"
_DECISION_ENDPOINT_DEFAULT = "https://openrouter.ai/api/alpha/decisions"
_TEXT_MODEL_DEFAULT = "openai/gpt-6-luna"
_DECISION_MODEL_DEFAULT = "~perplexity/pplx-decider-flash-latest"
_DECISION_FALLBACK_MODELS_DEFAULT = (
    "openai/gpt-6-luna,anthropic/claude-haiku-5.5"
)
_DECISION_THRESHOLD_DEFAULT = "0.6"
_DECISION_TIMEOUT_S_DEFAULT = "8.0"
_SPA_ORIGIN_DEFAULT = "http://localhost:5173"
_AGENT_HOST_DEFAULT = "0.0.0.0"
_AGENT_PORT_DEFAULT = "8123"

ENV_VARS: tuple[EnvVar, ...] = (
    EnvVar(
        name="OPENROUTER_API_KEY",
        default="",
        required=True,
        purpose=(
            "OpenRouter API key. Without it the agent still boots and serves "
            "200 on /health: the decider falls back to its deterministic "
            "heuristic transport and fill writes KPI-grounded copy."
        ),
    ),
    EnvVar(
        name="TEXT_MODEL",
        default=_TEXT_MODEL_DEFAULT,
        required=False,
        purpose=(
            "Chat-completions model id the fill node streams the narrative "
            "from (read per call in agent/graph.py)."
        ),
    ),
    EnvVar(
        name="OPENROUTER_BASE_URL",
        default=_OPENROUTER_BASE_URL_DEFAULT,
        required=False,
        purpose=(
            "OpenAI-compatible base the text model and the chat decision "
            "fallback POST to; when set to a non-default value the decisions "
            "transport uses {base}/decisions (a fake/test server)."
        ),
    ),
    EnvVar(
        name="DECISION_ENDPOINT",
        default=_DECISION_ENDPOINT_DEFAULT,
        required=False,
        purpose=(
            "Full URL of the decisions-modality endpoint (OpenRouter's alpha "
            "API by default); overrides the OPENROUTER_BASE_URL-derived "
            "decisions path for a gateway or fake server."
        ),
    ),
    EnvVar(
        name="DECISION_MODEL",
        default=_DECISION_MODEL_DEFAULT,
        required=False,
        purpose=(
            "Decisions-modality model the primary decision transport POSTs "
            "the question batch to; reported verbatim in decisions[].model."
        ),
    ),
    EnvVar(
        name="DECISION_MODEL_CHOICE",
        default="",
        required=False,
        purpose=(
            "Per-type DECISION_MODEL override for choice questions only "
            "(issue #19); empty or whitespace = use DECISION_MODEL."
        ),
    ),
    EnvVar(
        name="DECISION_MODEL_NOUL",
        default="",
        required=False,
        purpose=(
            "Per-type DECISION_MODEL override for noul questions only "
            "(issue #19); empty or whitespace = use DECISION_MODEL."
        ),
    ),
    EnvVar(
        name="DECISION_MODEL_SCORE",
        default="",
        required=False,
        purpose=(
            "Per-type DECISION_MODEL override for score questions only "
            "(issue #19); empty or whitespace = use DECISION_MODEL."
        ),
    ),
    EnvVar(
        name="DECISION_FALLBACK_MODELS",
        default=_DECISION_FALLBACK_MODELS_DEFAULT,
        required=False,
        purpose=(
            "Comma-separated chat-completions model chain, walked with "
            "response_format: json_schema when the decisions endpoint fails; "
            "members must be chat models, not decisions-only models."
        ),
    ),
    EnvVar(
        name="DECISION_THRESHOLD",
        default=_DECISION_THRESHOLD_DEFAULT,
        required=False,
        purpose=(
            "Endorsement line (inclusive >=) for above_threshold flagging; "
            "components whose choice probability falls below it are cut "
            "from the layout. A non-numeric value fails the decision "
            "client's wiring loud."
        ),
    ),
    EnvVar(
        name="DECISION_TIMEOUT_S",
        default=_DECISION_TIMEOUT_S_DEFAULT,
        required=False,
        purpose=(
            "Per-request timeout in seconds for both decision transports, "
            "keeping a stuck endpoint inside the live 10s answer budget."
        ),
    ),
    EnvVar(
        name="SPA_ORIGIN",
        default=_SPA_ORIGIN_DEFAULT,
        required=False,
        purpose=(
            "Browser origin allowed by the agent's CORS middleware. Set it "
            "when the SPA is served from a different origin than the "
            "native-dev default (e.g. a remapped Docker web port)."
        ),
    ),
    EnvVar(
        name="AGENT_HOST",
        default=_AGENT_HOST_DEFAULT,
        required=False,
        purpose=(
            "Bind host for `python server.py` (the --host default), read "
            "ahead of HOST."
        ),
    ),
    EnvVar(
        name="HOST",
        default=_AGENT_HOST_DEFAULT,
        required=False,
        purpose="Fallback bind host when AGENT_HOST is unset or empty.",
    ),
    EnvVar(
        name="AGENT_PORT",
        default=_AGENT_PORT_DEFAULT,
        required=False,
        purpose=(
            "Bind port for `python server.py` (the --port default), read "
            "ahead of PORT."
        ),
    ),
    EnvVar(
        name="PORT",
        default=_AGENT_PORT_DEFAULT,
        required=False,
        purpose="Fallback bind port when AGENT_PORT is unset or empty.",
    ),
    EnvVar(
        name="DB_PATH",
        default="",
        required=False,
        purpose=(
            "Path of the active per-dataset sandbox SQLite file "
            "(issue #73; agent/sandbox.py writes data/sandboxes/<id>.db). "
            "Empty or unset means no active DB; agent/db.py then falls "
            "back to data/app.db."
        ),
    ),
)
