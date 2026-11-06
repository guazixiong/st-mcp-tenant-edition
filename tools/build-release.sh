#!/bin/sh
# Build GitHub Release assets for the tenant edition:
# source tarball, wheel, linux-arm64, linux-x86_64 and windows-x64 bundles.
# Inputs are local files; no network access.
#
# Environment:
#   ARM64_PY_TGZ  portable CPython 3.11 (linux-arm64, install_only)
#   X64_PY_TGZ    portable CPython 3.11 (linux-x86_64, install_only)
#   ARM64_WHEELS  offline wheel directory for linux-arm64
#   X64_WHEELS    offline wheel directory for linux-x86_64
#   WIN_BUNDLE    unpacked Windows runtime (env/ with Python and dependencies)
# Optional:
#   VER (default 0.1.0) OUT (default release/) SEVENZIP (path to 7z on Windows)
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VER=${VER:-0.1.0}
OUT=${OUT:-$ROOT/release}
WHEEL="$ROOT/dist/st_mcp-$VER-py3-none-any.whl"
STAGE="$ROOT/.release-stage"
TEMPLATE="$ROOT/packaging/RELEASE-README.md"

need() { [ -e "$2" ] || { echo "missing $1: $2" >&2; exit 1; }; }
u() { if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf %s "$1"; fi; }

need ARM64_PY_TGZ "${ARM64_PY_TGZ:-}"
need X64_PY_TGZ "${X64_PY_TGZ:-}"
need ARM64_WHEELS "${ARM64_WHEELS:-}"
need X64_WHEELS "${X64_WHEELS:-}"
need WIN_BUNDLE "${WIN_BUNDLE:-}"
need wheel "$WHEEL"

rm -rf "$STAGE"
mkdir -p "$OUT" "$STAGE/src"
echo "[1/5] exporting source"
git -C "$ROOT" archive --format=tar HEAD | tar -xf - -C "$STAGE/src"

stage_wheels() {
    w_src=$1
    w_dst=$2
    mkdir -p "$w_dst"
    for w in "$w_src"/*.whl; do
        [ -e "$w" ] || continue
        base=$(basename "$w")
        case "$base" in
            STPython*|st_mcp-*|stpython-*) continue ;;
        esac
        cp -p "$w" "$w_dst/"
    done
    cp -p "$ROOT/wheels/requirements.txt" "$w_dst/"
}

build_linux() {
    arch=$1
    py_tgz=$2
    wheels_dir=$3
    name="st-mcp-tenant-$VER-linux-$arch"
    dst="$STAGE/$name"
    echo "[linux-$arch] assembling $name"
    mkdir -p "$dst/source"
    cp -a "$STAGE/src/." "$dst/source/"
    mkdir -p "$dst/runtime"
    cp -p "$(u "$py_tgz")" "$dst/runtime/"
    stage_wheels "$wheels_dir" "$dst/wheels"
    mkdir -p "$dst/dist" "$dst/tenants" "$dst/secrets"
    cp -p "$WHEEL" "$dst/dist/"
    cp -p "$ROOT/packaging/linux/install.sh" "$dst/install.sh"
    cp -p "$ROOT/packaging/linux/run.sh" "$dst/run.sh"
    cp -p "$ROOT/packaging/linux/admin.sh" "$dst/admin.sh"
    chmod +x "$dst/install.sh" "$dst/run.sh" "$dst/admin.sh"
    cp -a "$ROOT/tools" "$dst/tools"
    cp -a "$ROOT/tenants/README.md" "$dst/tenants/"
    cp -p "$ROOT/secrets/admin.env.example" "$dst/secrets/"
    sed -e "s/{PLATFORM}/linux-$arch/g" -e "s/{VER}/$VER/g" "$TEMPLATE" > "$dst/RELEASE-README.md"
    for f in install.sh run.sh admin.sh RELEASE-README.md tools/st-mcp-admin.py \
             dist/st_mcp-$VER-py3-none-any.whl tenants/README.md; do
        [ -e "$dst/$f" ] || { echo "linux-$arch missing $f" >&2; exit 1; }
    done
    [ -d "$dst/source/src" ] || { echo "linux-$arch source incomplete" >&2; exit 1; }
    tar -czf "$(u "$OUT/$name.tar.gz")" -C "$STAGE" "$name"
}

build_linux arm64 "$ARM64_PY_TGZ" "$ARM64_WHEELS"
build_linux x86_64 "$X64_PY_TGZ" "$X64_WHEELS"

echo "[4/5] assembling Windows bundle"
WIN_NAME="st-mcp-tenant-$VER-windows-x64"
WIN_STAGE="$STAGE/$WIN_NAME"
cp -a "$WIN_BUNDLE" "$WIN_STAGE"
rm -rf "$WIN_STAGE/env/STPython-doc" \
       "$WIN_STAGE/env/Lib/site-packages/STPython" \
       "$WIN_STAGE/env/Lib/site-packages/st_mcp" \
       "$WIN_STAGE/env/Lib/site-packages"/st_mcp-*.dist-info \
       "$WIN_STAGE/env/Lib/site-packages"/stpython-*.dist-info \
       "$WIN_STAGE/env/Lib/site-packages"/STPython-*.dist-info
# Drop files that belong to the upstream single-tenant bundle.
rm -rf "$WIN_STAGE/config" "$WIN_STAGE/docs" "$WIN_STAGE/scripts" \
       "$WIN_STAGE/wheels" "$WIN_STAGE/README.md" "$WIN_STAGE/START-HERE.md" \
       "$WIN_STAGE/SHA256SUMS.txt" "$WIN_STAGE/一键启动.bat" "$WIN_STAGE/验收检查.bat"
find "$WIN_STAGE" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
mkdir -p "$WIN_STAGE/dist" "$WIN_STAGE/tenants" "$WIN_STAGE/secrets"
cp -p "$WHEEL" "$WIN_STAGE/dist/"
"$WIN_STAGE/env/python.exe" -m pip install --no-index --no-deps --force-reinstall "$(u "$WHEEL")" >/dev/null
"$WIN_STAGE/env/python.exe" -c "import st_mcp, st_mcp.tenancy, mcp; print('tenant wheel ok')"
cp -a "$ROOT/tools" "$WIN_STAGE/tools"
cp -a "$STAGE/src" "$WIN_STAGE/source"
cp -p "$ROOT/packaging/windows/start.bat" "$WIN_STAGE/start.bat"
cp -p "$ROOT/packaging/windows/admin.bat" "$WIN_STAGE/admin.bat"
cp -p "$ROOT/tenants/README.md" "$WIN_STAGE/tenants/"
cp -p "$ROOT/secrets/admin.env.example" "$WIN_STAGE/secrets/"
sed "s/{VER}/$VER/g" "$ROOT/packaging/RELEASE-README.md" > "$WIN_STAGE/RELEASE-README.md"
for f in start.bat admin.bat dist/st_mcp-$VER-py3-none-any.whl tools/st-mcp-admin.py; do
    [ -e "$WIN_STAGE/$f" ] || { echo "Windows bundle missing $f" >&2; exit 1; }
done
rm -f "$OUT/$WIN_NAME.zip"
if [ -n "${SEVENZIP:-}" ] && [ -x "$SEVENZIP" ]; then
    ( cd "$STAGE" && "$SEVENZIP" a -tzip -mx=5 -mcu=on "$OUT/$WIN_NAME.zip" "$WIN_NAME" >/dev/null )
else
    ( cd "$STAGE" && zip -qr "$OUT/$WIN_NAME.zip" "$WIN_NAME" )
fi

echo "[5/5] source archive and checksums"
git -C "$ROOT" archive --format=tar.gz --prefix="st-mcp-tenant-$VER/" \
    -o "$OUT/st-mcp-tenant-$VER-source.tar.gz" HEAD
cp -p "$WHEEL" "$OUT/"
( cd "$OUT" && LC_ALL=C sha256sum "st-mcp-tenant-$VER-"*.tar.gz "st-mcp-tenant-$VER-"*.zip "st_mcp-$VER-"*.whl > SHA256SUMS )

echo "done: $OUT"
ls -l "$OUT"
