"""Data-source seam (issue #78): the `Source` protocol plus its verbatim
SQLite implementation.

Re-exports the public surface — `Source` and `SqliteSource` — so
consumers import the whole seam from one place (`from agent.data import
Source, SqliteSource`).

Dual-import contract, mirroring agent/quality.py: this package imports
both as `agent.data` from the repo root (package-style runtime probes)
and as `data` from agent/ (tests).
"""

try:  # package-style entry (import agent.data from the repo root)
    from agent.data.protocol import Source
    from agent.data.sqlite_source import SqliteSource
except ModuleNotFoundError:  # script-style entry (import data from agent/)
    from data.protocol import Source
    from data.sqlite_source import SqliteSource

# Re-export surface for `from data import X` consumers (ruff F401 reads
# __all__ as the use-site, so the dual-import blocks above stay flagged as
# intentional re-exports rather than dead imports).
__all__ = [
    "Source",
    "SqliteSource",
]
