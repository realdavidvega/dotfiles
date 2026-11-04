#!/usr/bin/env bash
# H2 for design 0006, run on the hub before approval. It drives a scratch
# interactive Claude in a private tmux server and prints one verdict per item.
# Nothing outside the scratch directory, its Claude project directory and the
# private tmux socket is written. ~/.claude/settings.json is never touched.
#
#   bash design/checks/0006-h2.sh [SYMLINKED_DIR]
#
# SYMLINKED_DIR is optional: a symlinked path to the vault, for the encoding item.
set -u

SOCK=ags-h2-$$
SESSION=h2
WAIT=${H2_WAIT:-90}
CONFIG=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
SCRATCH=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/ags-h2.XXXXXX")" && pwd -P)
LOG=$SCRATCH/hook.log
OUT=$SCRATCH/results.txt
HOOK=$SCRATCH/hook.sh
SETTINGS=$SCRATCH/settings.json

encode() { printf '%s' "$1" | sed 's#[^A-Za-z0-9]#-#g'; }
PROJECT=$CONFIG/projects/$(encode "$SCRATCH")

result() { printf '%-5s %s\n' "$1" "$2" | tee -a "$OUT"; }
t() { tmux -L "$SOCK" "$@"; }
type_line() { t send-keys -t "$SESSION:agent" -l "$1"; sleep 1; t send-keys -t "$SESSION:agent" Enter; }
wait_for() { # wait_for SECONDS COMMAND...
  local end=$((SECONDS + $1)); shift
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -ge "$end" ] && return 1
    sleep 2
  done
}
hook_lines() { local n; n=$(grep -c '^PAYLOAD' "$LOG" 2>/dev/null); echo "${n:-0}"; }
transcripts() { find "$PROJECT" -maxdepth 1 -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' '; }
hooks_above() { [ "$(hook_lines)" -gt "$1" ]; }
transcripts_above() { [ "$(transcripts)" -gt "$1" ]; }
nth_payload() { grep '^PAYLOAD' "$LOG" | sed -n "${1}p"; }
nth_env() { grep '^ENV' "$LOG" | sed -n "${1}p"; }
field() { sed -n "s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p" | head -1; }
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"; }
first_entrypoint() { grep -m1 -o '"entrypoint":"[^"]*"' "$1" | sed 's/.*:"\(.*\)"/\1/'; }
has_assistant() { grep -q '"type":"assistant"' "$1"; }
asking_trust() { t capture-pane -p -t "$SESSION:agent" | grep -q 'trust this folder'; }
hooked() { hooks_above 0; }
trust_if_asked() { # the dialog defaults to "No, exit"
  local end=$((SECONDS + 30))
  while [ "$SECONDS" -lt "$end" ]; do
    if asking_trust; then t send-keys -t "$SESSION:agent" Down; sleep 1; t send-keys -t "$SESSION:agent" Enter; return 0; fi
    hooked && return 0
    sleep 2
  done
}

cleanup() { t kill-server 2>/dev/null; }
trap cleanup EXIT

cat >"$HOOK" <<EOF
#!/usr/bin/env bash
payload=\$(cat)
printf 'PAYLOAD %s\n' "\$payload" >>"$LOG"
ppid=\$(ps -o ppid= -p "\${CLAUDE_PID:-0}" 2>/dev/null | tr -d ' ')
printf 'ENV pane=%s claude_pid=%s claude_ppid=%s entrypoint=%s\n' "\${TMUX_PANE:-}" "\${CLAUDE_PID:-}" "\$ppid" "\${CLAUDE_CODE_ENTRYPOINT:-}" >>"$LOG"
EOF
chmod +x "$HOOK"
printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"%s"}]}]}}\n' "$HOOK" >"$SETTINGS"

: >"$OUT"
echo "H2 for design 0006 on $(hostname), $(date -u +%FT%TZ)" | tee -a "$OUT"
echo "claude $(claude --version 2>/dev/null)" | tee -a "$OUT"
echo "scratch $SCRATCH" | tee -a "$OUT"

# 1. Flags.
help=$(claude --help 2>&1)
missing=""
for f in --session-id --name --fork-session; do
  printf '%s' "$help" | grep -q -- "$f" || missing="$missing $f"
done
[ -z "$missing" ] && result PASS "flags: --session-id --name --fork-session listed" \
  || result FAIL "flags: missing$missing (update Claude first)"

# 2. A clean environment, as the launcher's scrub gives the agent.
CLEAN=(env -u CLAUDECODE -u CLAUDE_PID -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_SESSION_ID
  -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_SESSION_ATTENDED -u CLAUDE_PROJECT_DIR
  -u CLAUDE_ENV_FILE -u CLAUDE_CODE_EXECPATH -u TMUX -u TMUX_PANE)
"${CLEAN[@]}" tmux -L "$SOCK" new-session -d -s "$SESSION" -n agent -c "$SCRATCH" -x 200 -y 50
PANE_PID=$(t display -p -t "$SESSION:agent" '#{pane_pid}')
PANE_ID=$(t display -p -t "$SESSION:agent" '#{pane_id}')

type_line "claude --settings '$SETTINGS' --allowedTools 'Bash(claude:*)'"
trust_if_asked
if ! wait_for "$WAIT" hooks_above 0; then
  result FAIL "interactive: no SessionStart hook within ${WAIT}s, see the pane:"
  t capture-pane -p -t "$SESSION:agent" | tail -20
  exit 1
fi
sleep 3
type_line "Reply with the single word ok."
ID1=$(nth_payload 1 | field session_id)
TP1=$(nth_payload 1 | field transcript_path)
env1=$(nth_env 1)
wait_for "$WAIT" has_assistant "$PROJECT/$ID1.jsonl" || result FAIL "interactive: no reply in $PROJECT/$ID1.jsonl"

case "$env1" in *"pane=$PANE_ID "*) p=ok ;; *) p=bad ;; esac
case "$env1" in *"claude_ppid=$PANE_PID "*) pp=ok ;; *) pp=bad ;; esac
case "$env1" in *"entrypoint=cli") ep=ok ;; *) ep=bad ;; esac
[ "$TP1" = "$PROJECT/$ID1.jsonl" ] && tp=ok || tp=bad
e1=$(first_entrypoint "$PROJECT/$ID1.jsonl" 2>/dev/null)
if [ "$p$pp$ep$tp" = okokokok ] && [ "$e1" = cli ]; then
  result PASS "interactive hook: TMUX_PANE, CLAUDE_PID parent is the pane shell, entrypoint cli, transcript_path and first entrypoint cli"
else
  result FAIL "interactive hook: pane=$p parent=$pp entrypoint=$ep transcript_path=$tp first_entrypoint=$e1 | $env1 | $TP1"
fi

# 3. The sessions file of the running interactive Claude.
CPID=$(printf '%s' "$env1" | sed -n 's/.*claude_pid=\([0-9]*\).*/\1/p')
SF=$CONFIG/sessions/$CPID.json
if [ -f "$SF" ] && grep -Eq "\"pid\": *$CPID[,}]" "$SF" && [ "$(field sessionId <"$SF")" = "$ID1" ]; then
  result PASS "sessions file: $SF holds pid and sessionId $ID1"
else
  result FAIL "sessions file: $SF missing or not matching: $(cat "$SF" 2>/dev/null | head -c 300)"
fi

# 4. A nested claude -p through the agent's Bash tool.
before=$(transcripts)
known=$(find "$PROJECT" -maxdepth 1 -name '*.jsonl' | sort)
n_before=$(hook_lines)
type_line "Use your Bash tool to run exactly this command, then reply with only its output: claude -p --settings '$SETTINGS' 'say ok'"
if wait_for "$WAIT" transcripts_above "$before"; then
  sleep 5
  nested=$(find "$PROJECT" -maxdepth 1 -name '*.jsonl' | sort | comm -13 <(printf '%s\n' "$known") - | head -1)
  en=$(first_entrypoint "$nested")
  envn=$(nth_env $((n_before + 1)))
  case "$envn" in *"entrypoint=sdk-cli") nep=ok ;; *) nep=bad ;; esac
  case "$envn" in *"claude_ppid=$PANE_PID "*) npar=pane ;; *) npar=other ;; esac
  if [ "$en" = sdk-cli ] && [ "$nep" = ok ] && [ "$npar" = other ]; then
    result PASS "nested claude -p: transcript and hook say sdk-cli, parent is not the pane shell (rule 3 as written)"
  else
    result FAIL "nested claude -p: transcript=$en hook=$envn parent=$npar (D3 fallback applies)"
  fi
else
  result FAIL "nested claude -p: no new transcript within ${WAIT}s (check the pane for a permission prompt)"
  t capture-pane -p -t "$SESSION:agent" | tail -15
fi
sleep 5

# 5. /clear: the hook's new id against the sessions file.
n_before=$(hook_lines)
type_line "/clear"
if wait_for "$WAIT" hooks_above "$n_before"; then
  ID2=$(nth_payload $((n_before + 1)) | field session_id)
  src=$(nth_payload $((n_before + 1)) | field source)
  sleep 3
  type_line "Reply with the single word ok."
  wait_for "$WAIT" has_assistant "$PROJECT/$ID2.jsonl"
  sid=$(field sessionId <"$SF" 2>/dev/null)
  if [ "$sid" = "$ID2" ] && [ "$ID2" != "$ID1" ]; then
    result PASS "/clear: hook source $src, new id $ID2, sessions file follows (rule 3 as written)"
  else
    result FAIL "/clear: hook source $src id $ID2, sessions file says $sid, startup id $ID1 (D3 fallback applies)"
  fi
else
  result FAIL "/clear: no SessionStart hook within ${WAIT}s"
fi

# 6. claude --resume ID keeps writing ID.jsonl.
type_line "/exit"
gone() { ! kill -0 "$CPID" 2>/dev/null; }
wait_for 30 gone
m0=$(mtime "$PROJECT/$ID1.jsonl")
sleep 2
n_before=$(hook_lines)
type_line "claude --settings '$SETTINGS' --resume $ID1"
wait_for "$WAIT" hooks_above "$n_before"
sleep 10
m1=$(mtime "$PROJECT/$ID1.jsonl")
rid=$(nth_payload $((n_before + 1)) | field session_id)
type_line "Reply with the single word ok."
sleep 2
moved() { [ "$(mtime "$PROJECT/$ID1.jsonl")" -gt "$m1" ]; }
wait_for "$WAIT" moved
m2=$(mtime "$PROJECT/$ID1.jsonl")
newer=$(find "$PROJECT" -maxdepth 1 -name '*.jsonl' -newer "$PROJECT/$ID1.jsonl" | wc -l | tr -d ' ')
[ "$m1" = "$m0" ] && bare="left the mtime" || bare="moved the mtime, so activity reads line timestamps"
if [ "$rid" = "$ID1" ] && [ "$m2" -gt "$m0" ] && [ "$newer" = 0 ]; then
  result PASS "--resume: hook id is $ID1 and a message lands in $ID1.jsonl (the bare resume $bare)"
else
  result FAIL "--resume: hook id $rid, mtimes $m0 $m1 $m2, newer transcripts $newer"
fi
type_line "/exit"
sleep 3

# 7. A hook under claude -p outside tmux.
n_before=$(hook_lines)
(cd "$SCRATCH" && "${CLEAN[@]}" claude -p --settings "$SETTINGS" 'say ok' >/dev/null 2>&1)
envp=$(nth_env $((n_before + 1)))
case "$envp" in *"entrypoint=sdk-cli") result PASS "claude -p hook: entrypoint sdk-cli, pane empty | $envp" ;;
  *) result FAIL "claude -p hook: $envp" ;; esac

# 8. A symlinked path files under the physical path's encoding.
if [ -n "${1:-}" ]; then
  phys=$(readlink -f "$1")
  if [ "$phys" = "$1" ]; then
    result SKIP "symlink: $1 is not a symlink"
  else
    n_before=$(hook_lines)
    (cd "$1" && "${CLEAN[@]}" claude -p --settings "$SETTINGS" 'say ok' >/dev/null 2>&1)
    tpl=$(nth_payload $((n_before + 1)) | field transcript_path)
    case "$tpl" in "$CONFIG/projects/$(encode "$phys")/"*) result PASS "symlink: filed under the physical path's encoding" ;;
      *) result FAIL "symlink: filed at $tpl, physical $phys" ;; esac
    rm -f "$tpl"
  fi
else
  result SKIP "symlink: no symlinked path given"
fi

# 9. Codex SessionStart, the Non-goals trigger, is read by hand.
if command -v codex >/dev/null; then
  result INFO "codex $(codex --version 2>/dev/null): check its hooks documentation for a SessionStart event carrying a session id"
fi

echo
echo "Results saved to $OUT. Paste them back into the design session."
echo "Scratch transcripts are under $PROJECT. Remove with: rm -r '$SCRATCH' '$PROJECT'"
