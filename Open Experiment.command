#!/bin/bash
# Finder launcher: use this project's environment even when no shell is activated.
cd -- "$(dirname -- "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
if [ ! -x .venv/bin/python ]; then
    printf 'The experiment needs its initial setup. From this folder, run: source setup.sh\n'
    read -r -p 'Press Return to close. '
    exit 1
fi
.venv/bin/python main.py gui
experiment_status=$?
if [ "$experiment_status" -ne 0 ]; then
    printf '\nThe window could not start. Review the message above.\n'
    read -r -p 'Press Return to close. '
fi
exit "$experiment_status"
