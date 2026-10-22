#!/usr/bin/env sh
set -eu

script_path=$0
case $script_path in
    */*) ;;
    *) script_path=$(command -v "$script_path" 2>/dev/null || printf '%s' "$script_path") ;;
esac
script_dir=$(CDPATH= cd -P "$(dirname "$script_path")" && pwd)
project_root=$(CDPATH= cd -P "$script_dir/.." && pwd)

if [ ! -f "$project_root/pyproject.toml" ]; then
    printf '%s\n' "Cannot locate the project manifest under $project_root" >&2
    exit 1
fi

python_command=${PYTHON:-python3}
if ! command -v "$python_command" >/dev/null 2>&1; then
    printf '%s\n' "Cannot find $python_command; set PYTHON to the intended interpreter." >&2
    exit 127
fi

if "$python_command" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1; then
    set -- -m pip install --no-deps --no-build-isolation --disable-pip-version-check "$project_root"
else
    set -- -m pip install --no-deps --no-build-isolation --disable-pip-version-check --user "$project_root"
fi

"$python_command" "$@"
"$python_command" -m agentweaver validate
