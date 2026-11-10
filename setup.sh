# Setup/activate the project venv:  source setup.sh
# Works from any directory: it cds to the project folder first.
# Run it again in each new terminal: it reuses .venv and activates it.

# This script must be sourced, or the venv activation is lost.
_sourced=0
if [ -n "$ZSH_VERSION" ]; then
  case $ZSH_EVAL_CONTEXT in *:file) _sourced=1 ;; esac
elif [ -n "$BASH_VERSION" ]; then
  [ "${BASH_SOURCE[0]}" != "$0" ] && _sourced=1
fi
if [ "$_sourced" -ne 1 ]; then
  echo "Run with: source setup.sh"
  exit 1
fi
unset _sourced

# ${(%):-%x} is this file's path in zsh, BASH_SOURCE in bash.
if [ -n "$ZSH_VERSION" ]; then
  _this_file="${(%):-%x}"
else
  _this_file="${BASH_SOURCE[0]}"
fi
cd "$(cd "$(dirname "$_this_file")" && pwd)" || return 1
unset _this_file

if [ ! -x .venv/bin/python ]; then
  python3 -c 'import sys; sys.version_info >= (3, 10) or sys.exit("Python 3.10+ required, python3 is " + sys.version.split()[0])' &&
  python3 -m venv .venv
fi || {
  echo "SETUP FAILED: could not create .venv. python is still $(command -v python 2>/dev/null || echo missing)"
  return 1 2>/dev/null || exit 1
}

source .venv/bin/activate || {
  echo "SETUP FAILED: could not activate .venv"
  return 1 2>/dev/null || exit 1
}

# Use `python -m playwright` so install matches this venv's package version.
# Then verify Chromium can launch: `playwright install` can exit 0 after only
# removing unused browsers, leaving the required build missing.
if ! pip install -q -r requirements.txt; then
  echo "SETUP FAILED: pip install failed. python is $(command -v python)"
  return 1 2>/dev/null || exit 1
fi
if ! python -m playwright install chromium; then
  echo "SETUP FAILED: playwright install chromium failed. python is $(command -v python)"
  return 1 2>/dev/null || exit 1
fi
if ! python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    browser.close()
print("Playwright Chromium launch ok")
PY
then
  echo "SETUP FAILED: Chromium is installed incompletely for this Playwright version."
  echo "Try: python -m playwright install --force chromium"
  return 1 2>/dev/null || exit 1
fi

echo "Using $(command -v python) ($(python --version))"

# Desktop GUI needs Tk 8.6+ (_tkinter). Homebrew Python does not ship it; the matching
# python-tk@X.Y formula adds it to the same Framework the venv already uses.
if ! python -c 'import sys, tkinter; sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)' 2>/dev/null; then
  if [ "$(uname -s)" = Darwin ] && command -v brew >/dev/null; then
    _tk_formula="python-tk@$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    echo "Desktop GUI: installing Homebrew ${_tk_formula} for Tk support…"
    # Skip brew's auto-update during setup so Tk install stays predictable and short.
    if HOMEBREW_NO_AUTO_UPDATE=1 brew install "$_tk_formula"; then
      if python -c 'import sys, tkinter; sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)' 2>/dev/null; then
        echo "Desktop GUI: Tk ready ($(python -c 'import tkinter; print(tkinter.TkVersion)'))"
      else
        echo "Desktop GUI: ${_tk_formula} installed, but this Python still lacks Tk 8.6+."
        echo "  The app will try another Python on this Mac when you run: python main.py gui"
      fi
    else
      echo "Desktop GUI: could not install ${_tk_formula}."
      echo "  Collection still works. For the window, install Tk or use another Python 3.10+ with Tk."
    fi
    unset _tk_formula
  else
    echo "Desktop GUI: this Python has no Tk 8.6+. Collection still works."
    echo "  Provide Python 3.10+ with Tk, or on macOS Homebrew: brew install python-tk@\$(python -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")')"
  fi
fi

[ -f .env ] || printf 'OPENAI_API_KEY=\nGEMINI_API_KEY=\nANTHROPIC_API_KEY=\n' > .env
command -v mullvad >/dev/null || echo "VPN: brew install --cask mullvad-vpn, then mullvad account login"
