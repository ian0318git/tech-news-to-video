"""Tier 7 thread-safety / concurrency integration test harness.

This package houses the reusable test infrastructure that every Phase 2
fix PR in the Tier 7 remediation arc consumes. It is intentionally
*infrastructure-only*: no production-code coverage, no per-bug regression
tests. Those live in dedicated per-fix PRs that ``import`` from this
package's ``conftest.py`` fixtures.

See ``README.md`` for fixture API and explicit non-goals.
"""
