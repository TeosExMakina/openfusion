#!/bin/bash
# Builds OpenFusion.app for Apple Silicon Macs, with its own Python inside, so the
# people you share it with install nothing. Run it on your Mac from the project folder:
#
#     ./packaging/mac/build_app.sh            # app + .dmg + .zip in dist/
#     ./packaging/mac/build_app.sh --open     # ...and launch the new app
#
# Needs: macOS 12+ on Apple Silicon, internet, ~3 GB free while building.
# No Xcode needed: codesign, ditto, hdiutil and curl come with macOS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$ROOT/packaging/mac"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"
PY_VERSION="${PY_VERSION:-3.12}"
BUILD="$ROOT/build-mac"
APP="$BUILD/OpenFusion.app"
DIST="$ROOT/dist"
OPEN_AFTER=0
for a in "$@"; do
  case "$a" in
    --open) OPEN_AFTER=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }
fail() { printf '\n\033[31mStopped: %s\033[0m\n' "$1" >&2; exit 1; }
warn() { printf '\033[33mNote: %s\033[0m\n' "$1" >&2; }

[[ "$(uname -s)" == "Darwin" ]] || fail "run this on a Mac."
[[ "$(uname -m)" == "arm64" ]] || fail "this Terminal runs under Rosetta (x86_64). Open a native Terminal (Get Info > uncheck 'Open using Rosetta') and try again."
[[ -n "$VERSION" ]] || fail "could not read the version from pyproject.toml."
[[ -f "$ROOT/openfusion/gui/static/app.js" ]] || fail "openfusion/gui/static/app.js is missing (unzip the full project)."
mkdir -p "$BUILD" "$DIST"

# --------------------------------------------------------------------------- 1. Python
step 1/7 "Getting a standalone Python $PY_VERSION"
UV="$(command -v uv || true)"
if [[ -z "$UV" ]]; then
  UV="$BUILD/tools/uv"
  if [[ ! -x "$UV" ]]; then
    echo "Installing the 'uv' downloader into build-mac/tools (nothing outside this folder)..."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$BUILD/tools" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
  fi
fi
[[ -x "$UV" ]] || fail "could not get uv (check your internet connection)."
export UV_PYTHON_INSTALL_DIR="$BUILD/python-download"
"$UV" python install "$PY_VERSION" >/dev/null
PYSRC="$(find "$UV_PYTHON_INSTALL_DIR" -maxdepth 1 -type d -name "cpython-${PY_VERSION}.*-macos-aarch64-none" | sort | tail -1)"
[[ -n "$PYSRC" && -x "$PYSRC/bin/python$PY_VERSION" ]] || fail "uv did not produce a Python $PY_VERSION for arm64 in $UV_PYTHON_INSTALL_DIR."
echo "Using $(basename "$PYSRC")"

# --------------------------------------------------------------------------- 2. bundle layout
step 2/7 "Laying out OpenFusion.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
PYHOME="$APP/Contents/Resources/python"
ditto "$PYSRC" "$PYHOME"
find "$PYHOME/lib" -maxdepth 2 -name EXTERNALLY-MANAGED -delete
PY="$PYHOME/bin/python$PY_VERSION"
export PYTHONHOME="$PYHOME" PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
"$PY" -m pip --version >/dev/null 2>&1 || "$PY" -m ensurepip --upgrade >/dev/null

# --------------------------------------------------------------------------- 3. packages
step 3/7 "Installing OpenFusion, OpenCASCADE, VTK and the window toolkit (about 400 MB the first time)"
"$PY" -m pip install --no-warn-script-location --quiet --upgrade "${ROOT}[app]"
"$PY" -c "import OCP, numpy, webview, objc, WebKit, openfusion; print('installed openfusion', openfusion.__version__)"

# --------------------------------------------------------------------------- 4. trim
step 4/7 "Trimming what the app never uses"
STD="$PYHOME/lib/python$PY_VERSION"
rm -rf "$PYHOME/include" "$PYHOME/share" "$STD/test" "$STD/idlelib" "$STD/tkinter" "$STD/turtledemo" "$STD/ensurepip"
rm -rf "$PYHOME"/lib/tcl* "$PYHOME"/lib/tk* "$PYHOME"/lib/itcl* "$PYHOME"/lib/thread*
rm -f "$STD"/lib-dynload/_tkinter*.so
find "$STD/site-packages" -type d -name tests -prune -exec rm -rf {} +
find "$PYHOME" -name "__pycache__" -type d -prune -exec rm -rf {} +

# --------------------------------------------------------------------------- 5. launcher, plist, icons
step 5/7 "Adding the launcher, icons and examples"
# The interpreter runs from Contents/MacOS so macOS treats the process as OpenFusion.app
# (name in the menu bar, Dock icon). Contents/lib lets it find libpython next to it.
cp "$PY" "$APP/Contents/MacOS/python3"
ln -s "Resources/python/lib" "$APP/Contents/lib"
cat > "$APP/Contents/MacOS/OpenFusion" <<'LAUNCH'
#!/bin/bash
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONHOME="$CONTENTS/Resources/python"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1
export OPENFUSION_EXAMPLES="$CONTENTS/Resources/examples"
mkdir -p "$HOME/Library/Logs"
LOG="$HOME/Library/Logs/OpenFusion.log"
[[ -f "$LOG" && $(stat -f%z "$LOG") -gt 5000000 ]] && mv -f "$LOG" "$LOG.old"
echo "--- $(date) OpenFusion starting" >>"$LOG"
exec "$CONTENTS/MacOS/python3" -m openfusion.app "$@" >>"$LOG" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/OpenFusion"
cp "$HERE/OpenFusion.icns" "$HERE/OpenFusionPart.icns" "$APP/Contents/Resources/"
ditto "$ROOT/examples" "$APP/Contents/Resources/examples"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>OpenFusion</string>
  <key>CFBundleDisplayName</key><string>OpenFusion</string>
  <key>CFBundleIdentifier</key><string>app.openfusion.OpenFusion</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>OpenFusion</string>
  <key>CFBundleIconFile</key><string>OpenFusion</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
  <key>NSRequiresAquaSystemAppearance</key><false/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeName</key><string>OpenFusion part</string>
      <key>CFBundleTypeRole</key><string>Editor</string>
      <key>LSHandlerRank</key><string>Owner</string>
      <key>CFBundleTypeIconFile</key><string>OpenFusionPart</string>
      <key>LSItemContentTypes</key><array><string>app.openfusion.part</string></array>
    </dict>
  </array>
  <key>UTExportedTypeDeclarations</key>
  <array>
    <dict>
      <key>UTTypeIdentifier</key><string>app.openfusion.part</string>
      <key>UTTypeDescription</key><string>OpenFusion part</string>
      <key>UTTypeConformsTo</key><array><string>public.python-script</string><string>public.plain-text</string></array>
      <key>UTTypeIconFile</key><string>OpenFusionPart</string>
      <key>UTTypeTagSpecification</key><dict><key>public.filename-extension</key><array><string>ofs</string></array></dict>
    </dict>
  </array>
</dict>
</plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null || fail "Info.plist is invalid."

# --------------------------------------------------------------------------- 6. precompile + smoke test
step 6/7 "Precompiling and testing the bundle"
# hash-checked .pyc files: valid wherever the app is copied, and nothing is written inside it later
"$APP/Contents/MacOS/python3" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$STD" >/dev/null 2>&1 \
  || warn "a few files did not precompile (harmless: they are compiled in memory when used)."
export PYTHONDONTWRITEBYTECODE=1
"$APP/Contents/MacOS/python3" -c "import sys, OCP, numpy, webview, objc, WebKit, openfusion.app; print('bundle ok:', sys.version.split()[0], 'at', sys.prefix)"
"$APP/Contents/MacOS/python3" -m openfusion.cli build "$APP/Contents/Resources/examples/showcase.ofs" --quiet -o "$BUILD/smoke-test.3mf" \
  || fail "the bundled engine could not build the showcase example."
unset PYTHONHOME PYTHONDONTWRITEBYTECODE

# --------------------------------------------------------------------------- 7. sign + package
step 7/7 "Signing (ad hoc) and packaging"
xattr -cr "$APP"
n=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    codesign --force --sign - --timestamp=none "$f" 2>/dev/null || warn "could not sign ${f#"$APP"/}"
    n=$((n + 1))
  fi
done < <(find "$APP/Contents" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0)
echo "signed $n binaries"
codesign --force --sign - --timestamp=none "$APP" 2>&1 | grep -v "replacing existing signature" || true
if codesign --verify --strict "$APP" 2>/dev/null; then
  echo "signature ok (ad hoc)"
else
  warn "the bundle signature did not verify; the app still runs, but friends may see 'damaged' instead of 'unverified'."
fi

ZIP="$DIST/OpenFusion-$VERSION-arm64.zip"
DMG="$DIST/OpenFusion-$VERSION-arm64.dmg"
rm -f "$ZIP" "$DMG"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
STAGE="$BUILD/dmg"
rm -rf "$STAGE"; mkdir -p "$STAGE"
ditto "$APP" "$STAGE/OpenFusion.app"
ln -s /Applications "$STAGE/Applications"
cp "$HERE/READ_ME_FIRST.txt" "$STAGE/Read me first.txt"
hdiutil create -quiet -volname "OpenFusion $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

printf '\n\033[1mDone.\033[0m\n'
printf '  App:  %s (%s)\n' "$APP" "$(du -sh "$APP" | cut -f1)"
printf '  DMG:  %s (%s)  <- send this to friends, with "Read me first"\n' "$DMG" "$(du -h "$DMG" | cut -f1)"
printf '  Zip:  %s (%s)\n' "$ZIP" "$(du -h "$ZIP" | cut -f1)"
printf '  Logs: ~/Library/Logs/OpenFusion.log\n'
if [[ "$OPEN_AFTER" == 1 ]]; then open "$APP"; fi
