#!/usr/bin/env sh
# Install the `conjectures` command. Uses uv when it is present; otherwise a private virtualenv,
# so Python 3.12 and nothing else is required.
set -eu

SOURCE=${CONJECTURES_SOURCE:-$(cd "$(dirname "$0")" && pwd)}
BIN=${BIN:-${PREFIX:-$HOME/.local}/bin}
VENV=${VENV:-${PREFIX:-$HOME/.local}/share/conjectures-miner}

if command -v uv >/dev/null 2>&1; then
    # `--refresh-package` is not optional: the version does not change between builds, so uv
    # otherwise reinstalls the wheel it cached the first time and the install silently lands a
    # revision old. `--force` replaces the tool; it does not rebuild it.
    UV_TOOL_BIN_DIR=$BIN uv tool install --force --refresh-package conjectures-miner "$SOURCE"
else
    PY=$(command -v python3.14 || command -v python3.13 || command -v python3.12 ||
        command -v python3) || {
        echo "install.sh: needs uv, or python3.12 or newer" >&2
        exit 1
    }
    "$PY" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' || {
        echo "install.sh: $PY is $("$PY" -V), and this needs 3.12 or newer" >&2
        exit 1
    }
    # An isolated environment, so nothing here can disturb the system Python and PEP 668 has
    # nothing to object to. bittensor is a large dependency; the first install takes a minute.
    "$PY" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade --quiet pip
    "$VENV/bin/python" -m pip install --upgrade "$SOURCE"
    mkdir -p "$BIN"
    ln -sf "$VENV/bin/conjectures" "$BIN/conjectures"
fi

"$BIN/conjectures" --version
# Completion is installed for whatever shell owns the calling process, which would be this
# script's `sh`. Ask the miner's own shell to make the call instead. `|| exit 1` is load-bearing:
# a lone command is one a shell hands straight to `exec`, which would put `sh` back in that slot.
"${SHELL:-sh}" -c "'$BIN/conjectures' --install-completion || exit 1" ||
    echo "install.sh: no completion for ${SHELL:-sh}; bash, zsh, fish and powershell have it." >&2

case ":$PATH:" in
*":$BIN:"*) ;;
*) echo "install.sh: add $BIN to your PATH to run it as \`conjectures\`." >&2 ;;
esac
