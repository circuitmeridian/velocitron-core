#!/usr/bin/env bash
# Dump the full help surface of the velocitron and velocitron-viz CLIs.
#
# For each console script: if it is on PATH, print its top-level --help, then
# introspect its subcommands (the argparse "{a,b,c}" choice list) and print
# --help for each one. If the script is not on PATH, print a clear marker so
# the skill can offer to install it.
#
# Emits plain text to stdout. No arguments.

set -u

dump_command() {
    # $1 = console-script name (e.g. "velocitron")
    local cmd="$1"

    if ! command -v "$cmd" >/dev/null 2>&1; then
        printf '%s CLI NOT INSTALLED\n' "$cmd"
        return
    fi

    printf '===== %s --help =====\n' "$cmd"
    local top_help
    top_help="$("$cmd" --help 2>&1)"
    printf '%s\n' "$top_help"

    # Extract the first "{sub1,sub2,...}" choice group argparse prints for a
    # subcommand dispatcher. A CLI with no subcommands (e.g. velocitron-viz)
    # simply yields nothing here and we stop after the top-level help.
    local group
    group="$(printf '%s\n' "$top_help" | grep -oE '\{[a-z0-9_-]+(,[a-z0-9_-]+)+\}' | head -n 1)"
    if [ -z "$group" ]; then
        return
    fi

    local subs
    subs="$(printf '%s' "$group" | tr -d '{}' | tr ',' ' ')"
    local sub
    for sub in $subs; do
        printf '\n===== %s %s --help =====\n' "$cmd" "$sub"
        "$cmd" "$sub" --help 2>&1
    done
}

dump_command velocitron
printf '\n'
dump_command velocitron-viz
