#!/bin/sh
# Offline install: extract the bundled CPython, then install the pinned wheels.
set -eu

BASE=$(cd "$(dirname "$0")" && pwd)
PY="$BASE/python/bin/python3"

if [ ! -x "$PY" ]; then
    RT=$(ls "$BASE"/runtime/cpython-*.tar.gz 2>/dev/null | head -n 1)
    [ -n "$RT" ] || { echo "missing runtime: $BASE/runtime/" >&2; exit 1; }
    echo "[0/3] extracting portable CPython to $BASE/python"
    tar -xzf "$RT" -C "$BASE"
fi
[ -x "$PY" ] || { echo "portable CPython not found: $PY" >&2; exit 1; }

WHEELS="$BASE/wheels"
[ -d "$WHEELS" ] || { echo "missing dependencies: $WHEELS" >&2; exit 1; }

echo "[1/3] installing dependencies (offline)"
"$PY" -m pip install --no-index --find-links "$WHEELS" -r "$WHEELS/requirements.txt"
echo "[2/3] installing st-mcp (tenant edition)"
"$PY" -m pip install --no-index --no-deps "$BASE"/dist/st_mcp-*.whl
echo "[3/3] self-check"
"$PY" -c 'import st_mcp, st_mcp.tenancy, mcp; print("ok:", st_mcp.__file__)'

cat <<'EOF'
Done.
  Start the MCP server : sh run.sh
  Start the admin UI   : sh admin.sh
The vendor ShenTong driver (STPython + libaci.so) is not bundled; see source/vendor/PUT-FILES-HERE.md.
EOF
