"""Env-contract tests for issue #19: every variable the agent reads from the
environment is (1) registered in agent/config.py with its default, (2) listed
as an active KEY=value line in agent/.env.example with the same default, and
(3) documented in README's ## Environment table — the three sets of names
must be exactly equal, which is the issue's acceptance clause 4 ("grep of
os.environ/getenv calls vs example file keys is an exact superset match")
turned into a graded test. Pure file scanning + imports, no network.
"""

import re
from pathlib import Path

import config
import decision.client

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parent

# The agent's own modules — everything that may read env — excluding the
# tests themselves (the contract is about production reads).
_SCAN_PATTERNS = ("*.py", "decision/*.py", "scripts/*.py")
_SCAN_FILES = sorted(
    path for pattern in _SCAN_PATTERNS for path in AGENT_DIR.glob(pattern)
)

# The read idioms agent code uses (a survey of the repo: os.getenv only, but
# the os.environ spellings are pinned here so future drift cannot hide a
# variable from the contract).
_READ_PATTERNS = (
    re.compile(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]+)"'),
    re.compile(r'os\.environ\[\s*"([A-Z][A-Z0-9_]+)"'),
    re.compile(r'os\.environ\.get\(\s*"([A-Z][A-Z0-9_]+)"'),
)
_ACTIVE_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]+)=")


def _code_env_names():
    """Env names read literally by agent code, per the idioms above."""
    names = set()
    for path in _SCAN_FILES:
        text = path.read_text(encoding="utf-8")
        for pattern in _READ_PATTERNS:
            names.update(pattern.findall(text))
    return names


def _example_lines():
    """Active KEY=value lines of agent/.env.example keyed by name (comments
    are documentation and never count as listing a variable)."""
    values = {}
    for line in (AGENT_DIR / ".env.example").read_text(encoding="utf-8").splitlines():
        match = _ACTIVE_LINE_RE.match(line)
        if match:
            values[match.group(1)] = line.split("=", 1)[1]
    return values


def _registry():
    return {row.name: row for row in config.ENV_VARS}


def test_code_reads_are_listed_everywhere():
    """Acceptance clause 4: every literal os.getenv/os.environ name the agent
    reads appears in agent/.env.example AND in config.ENV_VARS — no hidden
    reads, no unregistered variables."""
    reads = _code_env_names()
    assert reads, "env scan found nothing — check the scanned paths"
    missing_from_example = reads - set(_example_lines())
    assert not missing_from_example, (
        f"agent reads {sorted(missing_from_example)} but agent/.env.example "
        "does not list them"
    )
    missing_from_registry = reads - set(_registry())
    assert not missing_from_registry, (
        f"agent reads {sorted(missing_from_registry)} but agent/config.py "
        "does not register them"
    )


def test_registry_example_and_code_reads_are_exactly_equal():
    """The three contracts coincide: registry == .env.example keys == code
    reads — an example row without a reader (dead config) or a reader
    without a row (undocumented) fails the equality either way, and the
    registry cannot drift from the example's key set."""
    assert set(_registry()) == set(_example_lines()) == _code_env_names()


def test_issue_79_vars_are_pinned_three_ways():
    """Issue #79: the six datasource/guard vars are registered in
    config.py, documented in agent/.env.example AND read literally by
    agent code — named explicitly so a silent drop of any one fails."""
    pinned = {
        "DB_PATH",
        "DATASOURCE_URL",
        "DATASOURCE_RO",
        "UPLOAD_MAX_MB",
        "SQL_ROW_LIMIT",
        "SQL_TIMEOUT_S",
    }
    assert pinned <= set(_registry()), pinned - set(_registry())
    assert pinned <= set(_example_lines()), pinned - set(_example_lines())
    assert pinned <= _code_env_names(), pinned - _code_env_names()


def test_example_spa_origin_lists_tauri():
    """Issue #79: the SPA_ORIGIN example is a comma list whose members
    include the Vite dev origin and tauri://localhost, equal to the
    registered default verbatim."""
    raw = _example_lines()["SPA_ORIGIN"]
    assert raw == _registry()["SPA_ORIGIN"].default
    origins = [part.strip() for part in raw.split(",") if part.strip()]
    assert "http://localhost:5173" in origins, raw
    assert "tauri://localhost" in origins, raw


def test_example_values_equal_registered_defaults():
    """Copying agent/.env.example to agent/.env is behavior-neutral: every
    active value equals the registry's documented default, and the numeric
    values parse the way the owning modules read them (no inline comments
    leaking into a float()/int())."""
    example = _example_lines()
    for name, row in _registry().items():
        assert example[name] == row.default, name
    assert float(example["DECISION_THRESHOLD"]) == float(_registry()["DECISION_THRESHOLD"].default)
    assert float(example["DECISION_TIMEOUT_S"]) == float(_registry()["DECISION_TIMEOUT_S"].default)


def test_registry_defaults_match_the_owning_modules():
    """The registry's defaults are pinned against each owner's constant, so
    config.py cannot document a stale default after a code change."""
    defaults = {name: row.default for name, row in _registry().items()}
    assert defaults["DECISION_MODEL"] == decision.client.DEFAULT_DECISION_MODEL
    assert defaults["DECISION_FALLBACK_MODELS"] == ",".join(
        decision.client.DEFAULT_FALLBACK_MODELS
    )
    assert defaults["DECISION_THRESHOLD"] == str(decision.client.DEFAULT_THRESHOLD)
    assert defaults["DECISION_TIMEOUT_S"] == str(decision.client.DEFAULT_DECISION_TIMEOUT_S)
    assert defaults["OPENROUTER_BASE_URL"] == decision.client.OPENROUTER_BASE_URL
    assert defaults["DECISION_ENDPOINT"] == decision.client.DEFAULT_DECISION_ENDPOINT

    import graph as agent_graph

    assert defaults["TEXT_MODEL"] == agent_graph.DEFAULT_TEXT_MODEL

    import db as agent_db
    import queries as agent_queries
    import server as agent_server

    assert defaults["DATASOURCE_URL"] == agent_db._DATASOURCE_URL_DEFAULT
    assert defaults["DATASOURCE_RO"] == agent_db._DATASOURCE_RO_DEFAULT
    assert defaults["DATASOURCE_RO"] == agent_server._DATASOURCE_RO_FALLBACK
    assert defaults["SQL_ROW_LIMIT"] == agent_queries._SQL_ROW_LIMIT_FALLBACK
    assert defaults["SQL_TIMEOUT_S"] == agent_db._SQL_TIMEOUT_S_DEFAULT
    assert defaults["SPA_ORIGIN"] == agent_server._SPA_ORIGIN_FALLBACK


def test_readme_environment_table_lists_every_variable():
    """Every registered variable is documented in README's ## Environment
    section — the operator-facing half of the contract (issue #19: README
    env table)."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Environment" in readme
    section = readme.split("## Environment", 1)[1].split("\n## ", 1)[0]
    for name in _registry():
        assert name in section, f"{name} missing from README's Environment table"


def test_scanned_files_cover_the_agent_modules():
    """Assumption pin: the scan really covers the modules that read env —
    graph.py, server.py, the decision package — so the equality tests above
    scan what they claim to scan."""
    scanned = {path.name for path in _SCAN_FILES}
    assert {"graph.py", "server.py", "config.py"} <= scanned
    assert "decision/client.py" in {
        str(path.relative_to(AGENT_DIR)) for path in _SCAN_FILES
    }
