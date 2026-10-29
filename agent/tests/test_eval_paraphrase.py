"""Unit tests for the report-only paraphrase eval (issue #89).

The eval runner (`scripts/eval_paraphrase.py`) is stdlib-only at import
time, so these tests load it straight from the file path — no sandbox
builds, no graph runs, no key, no network. Table-size/keyword/helper
clauses are pure; the `--help` probe runs the script as a subprocess on
the bare interpreter.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "eval_paraphrase.py"

spec = importlib.util.spec_from_file_location("eval_paraphrase", SCRIPT)
eval_paraphrase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_paraphrase)

CATALOG_TYPES = (
    "kpi_row",
    "line_chart",
    "variance_callout",
    "data_table",
    "data_quality_card",
    "insight_text",
)


# --- Table sizes: exactly 20 / 20 / 20 golden paraphrases + 10 distractors ---

def test_sales_table_has_twenty_paraphrases():
    """The sales route table holds exactly 20 paraphrases."""
    assert len(eval_paraphrase.SALES_PARAPHRASES) == 20


def test_churn_table_has_twenty_paraphrases():
    """The churn route table holds exactly 20 paraphrases."""
    assert len(eval_paraphrase.CHURN_PARAPHRASES) == 20


def test_quality_table_has_twenty_paraphrases():
    """The quality route table holds exactly 20 paraphrases."""
    assert len(eval_paraphrase.QUALITY_PARAPHRASES) == 20


def test_distractor_table_has_ten_queries():
    """The distractor table holds exactly 10 queries."""
    assert len(eval_paraphrase.DISTRACTORS) == 10


# --- Keyword coverage: each paraphrase keeps its route's stems -------------

def test_sales_paraphrases_carry_sales_stems_only():
    """Every sales paraphrase contains a sales stem (sales/revenue/show)
    and none of the earlier-checked routes' stems (quality/churn tables
    win the classify order, so a leak would misroute)."""
    for query in eval_paraphrase.SALES_PARAPHRASES:
        lowered = query.lower()
        assert any(stem in lowered for stem in eval_paraphrase.SALES_STEMS), query
        assert not any(stem in lowered for stem in eval_paraphrase.QUALITY_STEMS), query
        assert not any(stem in lowered for stem in eval_paraphrase.CHURN_STEMS), query


def test_churn_paraphrases_carry_churn_stems_only():
    """Every churn paraphrase contains a churn stem
    (churn/cancel/pricing/price) and no quality stem (checked first)."""
    for query in eval_paraphrase.CHURN_PARAPHRASES:
        lowered = query.lower()
        assert any(stem in lowered for stem in eval_paraphrase.CHURN_STEMS), query
        assert not any(stem in lowered for stem in eval_paraphrase.QUALITY_STEMS), query


def test_quality_paraphrases_carry_quality_stems():
    """Every quality paraphrase contains a quality stem
    (clean/quality/duplicate/null/missing)."""
    for query in eval_paraphrase.QUALITY_PARAPHRASES:
        lowered = query.lower()
        assert any(stem in lowered for stem in eval_paraphrase.QUALITY_STEMS), query


def test_distractors_carry_no_route_stems():
    """Every distractor avoids all route stems (substring-matched exactly
    like the classifier), so each routes off-catalog to the fallback."""
    for query in eval_paraphrase.DISTRACTORS:
        lowered = query.lower()
        assert not any(stem in lowered for stem in eval_paraphrase.ALL_STEMS), query


# --- Classifier routing: the tables land on their expected routes -----------

def _classify(text):
    from decision.heuristic import classify

    return classify(text)


def test_sales_paraphrases_route_to_sales():
    """`heuristic.classify` routes every sales paraphrase to sales."""
    for query in eval_paraphrase.SALES_PARAPHRASES:
        assert _classify(query) == "sales", query


def test_churn_paraphrases_route_to_churn():
    """`heuristic.classify` routes every churn paraphrase to churn."""
    for query in eval_paraphrase.CHURN_PARAPHRASES:
        assert _classify(query) == "churn", query


def test_quality_paraphrases_route_to_quality():
    """`heuristic.classify` routes every quality paraphrase to quality."""
    for query in eval_paraphrase.QUALITY_PARAPHRASES:
        assert _classify(query) == "quality", query


def test_distractors_route_off_catalog():
    """`heuristic.classify` routes every distractor off-catalog (the guard
    transport then scores every option 0.0, emptying into the fallback)."""
    for query in eval_paraphrase.DISTRACTORS:
        assert _classify(query) == "off_catalog", query


# --- Helpers on crafted layouts: exact-pair accuracy, fallback, unknown ----

def test_exact_pair_match_is_correct_any_order():
    """`is_correct` is the exact expected-set match: order-insensitive,
    but subsets and supersets both fail."""
    assert eval_paraphrase.is_correct(
        ["line_chart", "kpi_row"], {"kpi_row", "line_chart"}
    )
    assert not eval_paraphrase.is_correct(["kpi_row"], {"kpi_row", "line_chart"})
    assert not eval_paraphrase.is_correct(
        ["kpi_row", "line_chart", "data_table"], {"kpi_row", "line_chart"}
    )
    assert not eval_paraphrase.is_correct(["insight_text"], {"kpi_row"})


def test_fallback_detection_matches_only_insight_text():
    """`is_fallback` is true only for the single-insight_text canvas —
    the catalog fallback shape, never a real route layout."""
    assert eval_paraphrase.is_fallback(["insight_text"])
    assert not eval_paraphrase.is_fallback(["kpi_row", "line_chart"])
    assert not eval_paraphrase.is_fallback([])
    assert not eval_paraphrase.is_fallback(["insight_text", "kpi_row"])


def test_unknown_type_detection_counts_off_catalog_items():
    """`unknown_type_count` is 0 for every in-catalog layout and counts
    each off-catalog item type (crafted layouts the schema would reject)."""
    assert eval_paraphrase.unknown_type_count(
        ["kpi_row", "line_chart"], CATALOG_TYPES
    ) == 0
    assert eval_paraphrase.unknown_type_count(["insight_text"], CATALOG_TYPES) == 0
    assert eval_paraphrase.unknown_type_count(
        ["kpi_row", "unicorn_chart"], CATALOG_TYPES
    ) == 1
    assert eval_paraphrase.unknown_type_count(
        ["dragon", "unicorn"], CATALOG_TYPES
    ) == 2


def test_item_types_tolerates_junk_layouts():
    """`item_types` reads string types off dict items and ignores
    everything else (None, missing items, non-dict entries)."""
    assert eval_paraphrase.item_types(None) == []
    assert eval_paraphrase.item_types({}) == []
    assert eval_paraphrase.item_types({"items": [{"type": "kpi_row"}, "junk", 42]}) == [
        "kpi_row"
    ]


# --- CLI contract: --help shows --seed/--live and exits 0 --------------------

def test_help_shows_seed_and_live_flags_with_exit_zero():
    """`python scripts/eval_paraphrase.py --help` exits 0 naming `--seed`
    and `--live` (stdlib-only import path: works on the bare interpreter
    with no agent venv installed)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--seed" in result.stdout
    assert "--live" in result.stdout


def test_seed_flag_is_repeatable_with_default_seeds():
    """`--seed` appends per occurrence and defaults to 7 11 23."""
    assert eval_paraphrase.parse_args([]).seed == [7, 11, 23]
    assert eval_paraphrase.parse_args(["--seed", "7", "--seed", "11"]).seed == [7, 11]
    assert eval_paraphrase.parse_args(["--live"]).live is True
    assert eval_paraphrase.parse_args([]).live is False


def test_report_renderer_has_required_contract_strings():
    """The rendered markdown carries the acceptance strings: a table
    header starting `| path | accuracy`, `fallback rate`, and
    `unknown-type count = 0` over a completed heuristic run."""
    rows = [
        {"types": ["kpi_row", "line_chart"], "latency_ms": 1.0},
        {"types": ["kpi_row", "line_chart"], "latency_ms": 2.0},
    ]
    results = {
        ("seeded", "sales", "heuristic"): (rows, {"kpi_row", "line_chart"}),
        ("seeded", "distractor", "heuristic"): (
            [{"types": ["insight_text"], "latency_ms": 1.0}],
            {"insight_text"},
        ),
    }
    report = eval_paraphrase.render_report(results, CATALOG_TYPES, [7], False)
    assert "| path | accuracy" in report
    assert "fallback rate" in report
    assert "unknown-type count = 0" in report
