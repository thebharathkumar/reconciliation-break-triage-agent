#!/usr/bin/env bash
# SessionStart hook: make the project runnable in a fresh web session.
# Installs the package and dependencies, generates the seed data if missing,
# and runs the test suite as a health check. Never fails the session: a bad
# test run is reported, not fatal.
set -u

cd "$(dirname "$0")/.." || exit 0

echo "[session-setup] installing dependencies..."
pip install -q -e . >/dev/null 2>&1
pip install -q pytest >/dev/null 2>&1

if [ ! -f data/ledger.csv ] || [ ! -f data/bank.csv ]; then
  echo "[session-setup] generating seed data..."
  python data/seed_data.py >/dev/null 2>&1
fi

echo "[session-setup] running tests..."
if python -m pytest -q >/tmp/recon_pytest.log 2>&1; then
  tail -1 /tmp/recon_pytest.log
else
  echo "[session-setup] tests reported failures:"
  tail -5 /tmp/recon_pytest.log
fi

exit 0
