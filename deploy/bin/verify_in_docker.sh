#!/usr/bin/env bash
# verify_in_docker.sh -- run the self-contained installer end to end inside a clean Linux
# container and prove the service starts. No systemd in a container, so shared.install is
# exercised with a writable unit path and the service is started with uvicorn directly.
#
#   bash deploy/bin/verify_in_docker.sh [path/to/pr-review-suite-install.sh] [image]
#
# Steps inside the container (python:3.12 has git, which repo-sync tests need):
#   1. installer --verify, then install into /opt/pr-review-suite (venv + pip)
#   2. pytest from the installed tree (fixtures are not bundled: that one test is skipped)
#   3. shared.install with --config-dir/--data-dir/--unit-path under /tmp, as a non-root
#      user would see them, then doctor --no-network
#   4. start uvicorn, hit /healthz and /console, /console/settings, /console/setup
#   5. apply one setting through the console API and read it back from config.toml
set -eu
BUNDLE="${1:-dist/pr-review-suite-install.sh}"
IMAGE="${2:-python:3.12}"
BUNDLE_ABS="$(cd "$(dirname "$BUNDLE")" && pwd)/$(basename "$BUNDLE")"

# The bundle goes in over stdin (no bind mount: Git Bash on Windows rewrites mount paths).
export MSYS_NO_PATHCONV=1
docker run --rm -i "$IMAGE" bash -eu -c '
export PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p /bundle && cat > /bundle/install.sh
echo "== 1. installer"
bash /bundle/install.sh --verify
bash /bundle/install.sh --target /opt/pr-review-suite
cd /opt/pr-review-suite
venv/bin/pip install -q pytest
echo "== 2. tests from the installed tree"
venv/bin/python -m pytest -q tests/ --deselect tests/test_core.py::test_fixture_count_matches_cases_and_readme
echo "== 3. shared.install + doctor"
export CONFIG_DIR=/tmp/etc DATA_DIR=/tmp/var
venv/bin/python -m shared.install --config-dir /tmp/etc --data-dir /tmp/var --unit-path /tmp/review-orchestrator.service --user root
ls -la /tmp/etc /tmp/review-orchestrator.service
export ADO_PAT=dummy LANGFUSE_PUBLIC_KEY=pk LANGFUSE_SECRET_KEY=sk
venv/bin/python -m shared.doctor --no-network || true
echo "== 4. start service"
venv/bin/uvicorn orchestrator.app:app --host 127.0.0.1 --port 8080 --log-level warning &
PID=$!
for i in $(seq 1 30); do
  if venv/bin/python -c "import urllib.request;urllib.request.urlopen(\"http://127.0.0.1:8080/healthz\",timeout=1)" 2>/dev/null; then break; fi
  sleep 1
done
venv/bin/python - <<"PYEOF"
import json, urllib.request, urllib.parse
def get(p):
    r = urllib.request.urlopen("http://127.0.0.1:8080" + p, timeout=10); return r.status, r.read().decode()
s, body = get("/healthz"); print("healthz", s, json.loads(body)["ok"], list(json.loads(body)["jobs"]))
for p in ("/console", "/console/settings", "/console/setup", "/console/reviews", "/console/knowledge", "/console/sources"):
    s, body = get(p); print(p, s, len(body), "bytes")
    assert s == 200 and "<html" in body
data = urllib.parse.urlencode({"max_findings": "12"}).encode()
r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8080/console/settings/review", data=data), timeout=10)
html = r.read().decode(); assert "已套用" in html, html[:200]
toml = open("/tmp/etc/config.toml", encoding="utf-8").read()
assert "max_findings = 12" in toml, toml
s, body = get("/healthz"); print("after apply: config.toml has max_findings = 12; healthz", s)
print("console round-trip ok")
PYEOF
kill $PID
echo "== all steps passed"
' < "$BUNDLE_ABS"
