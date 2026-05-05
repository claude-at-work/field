#!/bin/sh
# install_bash_hook.sh — wire command_not_found_handle to field run
#
# Idempotent: re-running won't duplicate the hook. Targets ~/.bashrc and
# ~/.zshrc; the bash hook is `command_not_found_handle`, the zsh hook is
# `command_not_found_handler` (different name).

set -eu

FIELD_BIN="${FIELD_BIN:-$HOME/.local/bin/field}"

BASH_HOOK_MARKER='# >>> field dispatcher hook >>>'
BASH_HOOK_END='# <<< field dispatcher hook <<<'

write_bash_hook() {
    rc="$1"
    if [ -f "$rc" ] && grep -qF "$BASH_HOOK_MARKER" "$rc"; then
        echo "  $rc: already installed"
        return
    fi
    cat >> "$rc" <<EOF

$BASH_HOOK_MARKER
command_not_found_handle() {
    if [ -x "$FIELD_BIN" ]; then
        "$FIELD_BIN" run "\$@"
        return \$?
    fi
    printf '%s: command not found\\n' "\$1" >&2
    return 127
}
$BASH_HOOK_END
EOF
    echo "  $rc: hook installed"
}

write_zsh_hook() {
    rc="$1"
    if [ -f "$rc" ] && grep -qF "$BASH_HOOK_MARKER" "$rc"; then
        echo "  $rc: already installed"
        return
    fi
    cat >> "$rc" <<EOF

$BASH_HOOK_MARKER
command_not_found_handler() {
    if [ -x "$FIELD_BIN" ]; then
        "$FIELD_BIN" run "\$@"
        return \$?
    fi
    printf '%s: command not found\\n' "\$1" >&2
    return 127
}
$BASH_HOOK_END
EOF
    echo "  $rc: hook installed"
}

[ -f "$HOME/.bashrc" ] && write_bash_hook "$HOME/.bashrc"
[ -f "$HOME/.zshrc" ] && write_zsh_hook "$HOME/.zshrc"

echo "done. open a new shell or run: source ~/.bashrc"
