#!/usr/bin/env bash
# ~/.claude/statusline.sh — Claude Code status line
# Receives JSON on stdin; outputs a single styled line.

# ── ANSI helpers ────────────────────────────────────────────────────────────
esc="\033"
reset="${esc}[0m"
bold="${esc}[1m"
dim="${esc}[2m"

# fg_256 <n>   — 256-colour foreground
fg() { printf "${esc}[38;5;%sm" "$1"; }
# bg_256 <n>   — 256-colour background
bg() { printf "${esc}[48;5;%sm" "$1"; }

# Palette (256-colour, renders everywhere)
C_MODEL_FG=$(fg 230)   # warm white
C_MODEL_BG=$(bg 63)    # vivid violet  #5f5fd7
C_DIR_FG=$(fg 255)
C_DIR_BG=$(bg 24)      # ocean blue    #005f87
C_GIT_FG=$(fg 220)     # gold
C_GIT_BG=$(bg 236)     # dark grey
C_DIM=$(fg 244)        # mid-grey

# Powerline-style separators (terminals without a Powerline font show a box
# glyph, which is still readable — the script does NOT require Nerd Fonts)
SEP_HARD=""       # U+E0B0 right-pointing solid triangle (powerline)

# ── Read stdin ───────────────────────────────────────────────────────────────
input=$(cat)

# ── Parse JSON ───────────────────────────────────────────────────────────────
if ! command -v jq &>/dev/null; then
  # Minimal sed/grep fallback — no colours, plain text
  model=$(printf '%s' "$input" | grep -o '"display_name":"[^"]*"' | head -1 | cut -d'"' -f4)
  cwd=$(printf '%s' "$input"   | grep -o '"current_dir":"[^"]*"'  | head -1 | cut -d'"' -f4)
  [ -z "$model" ] && model="Claude"
  [ -z "$cwd"   ] && cwd="$(pwd)"
  short_dir="${cwd/#$HOME/\~}"
  printf "✦ %s  %s\n" "$model" "$short_dir"
  exit 0
fi

# Single jq pass — empty string for any missing field. Unit-separator (\x1f)
# delimited: unlike tabs, a non-whitespace IFS preserves empty fields in read.
IFS=$'\x1f' read -r model cwd proj ctx_pct rl5_pct rl5_reset rl7_pct <<EOF
$(printf '%s' "$input" | jq -r '[
  (.model.display_name           // "Claude"),
  (.workspace.current_dir        // .cwd // ""),
  (.workspace.project_dir        // ""),
  (.context_window.used_percentage      // ""),
  (.rate_limits.five_hour.used_percentage // ""),
  (.rate_limits.five_hour.resets_at       // ""),
  (.rate_limits.seven_day.used_percentage // "")
] | map(tostring) | join("")')
EOF

# ── Resolve directory ─────────────────────────────────────────────────────────
[ -z "$cwd" ] && cwd="$(pwd)"
git_dir="${proj:-$cwd}"
short_dir="${cwd/#$HOME/\~}"
base_dir="$(basename "$short_dir")"
[ "$cwd" = "$HOME" ] && base_dir="~"

# ── Git segment ───────────────────────────────────────────────────────────────
git_seg=""
branch=$(git -C "$git_dir" symbolic-ref --short HEAD 2>/dev/null)
# Detached HEAD: show the short commit SHA instead
[ -z "$branch" ] && branch=$(git -C "$git_dir" rev-parse --short HEAD 2>/dev/null)
if [ -n "$branch" ]; then
  dirty=""
  if ! git -C "$git_dir" diff --quiet 2>/dev/null || \
     ! git -C "$git_dir" diff --cached --quiet 2>/dev/null; then
    dirty=" ✗"
  fi
  git_seg=" ⎇ ${branch}${dirty}"
fi

# ── Usage gauges ──────────────────────────────────────────────────────────────
# pct_int <pct> — truncate "23.5" → "23"; empty/garbage → empty
pct_int() { case "${1%%.*}" in (''|*[!0-9]*) ;; (*) printf '%s' "${1%%.*}";; esac }

# usage_col <int-pct> — green <50, gold <80, red ≥80
usage_col() {
  if   [ "$1" -ge 80 ]; then fg 203
  elif [ "$1" -ge 50 ]; then fg 220
  else fg 78; fi
}

# bar <int-pct> — 5-cell ▰▱ gauge
bar() {
  local filled=$(( ($1 * 5 + 50) / 100 )) out="" i
  [ "$filled" -gt 5 ] && filled=5
  for ((i = 0; i < filled; i++)); do out+="▰"; done
  for ((i = filled; i < 5; i++)); do out+="▱"; done
  printf '%s' "$out"
}

# gauge_seg <label> <pct> — " label ▰▰▱▱▱ 23%" coloured by usage
gauge_seg() {
  local p; p=$(pct_int "$2")
  [ -z "$p" ] && return
  printf '%b' " ${C_DIM}$1${reset} $(usage_col "$p")$(bar "$p")  ${bold}${p}%${reset}"
}

# Context window: quarter-circle icon by fill level
ctx_seg=""
ctx_i=$(pct_int "$ctx_pct")
if [ -n "$ctx_i" ]; then
  if   [ "$ctx_i" -ge 88 ]; then ctx_icon="●"
  elif [ "$ctx_i" -ge 63 ]; then ctx_icon="◕"
  elif [ "$ctx_i" -ge 38 ]; then ctx_icon="◑"
  elif [ "$ctx_i" -ge 13 ]; then ctx_icon="◔"
  else                           ctx_icon="○"; fi
  ctx_seg=" $(usage_col "$ctx_i")${ctx_icon}${reset} ${C_DIM}ctx${reset} $(usage_col "$ctx_i")$(bar "$ctx_i")  ${bold}${ctx_i}%${reset}"
fi

rl5_seg=$(gauge_seg "5h" "$rl5_pct")
# 5-hour reset time (local HH:MM) — macOS `date -r`, Linux `date -d @`
if [ -n "$rl5_seg" ] && [ -n "$rl5_reset" ]; then
  rl5_time=$(date -r "$rl5_reset" +%H:%M 2>/dev/null || date -d "@$rl5_reset" +%H:%M 2>/dev/null)
  [ -n "$rl5_time" ] && rl5_seg+=" ${C_DIM}${dim}↻${rl5_time}${reset}"
fi
rl7_seg=$(gauge_seg "1w" "$rl7_pct")

# ── Assemble the line ─────────────────────────────────────────────────────────
# Layout:
#  [  ✦ Model  |  dir  |  ⎇ branch  ] │ ◑ ctx ▰▰▱▱▱ 42% │ 5h ▰▱▱▱▱ 23% ↻15:00 │ 1w ▰▰▱▱▱ 41%

printf "%b" \
  "${C_MODEL_BG}${C_MODEL_FG}${bold}  ✦ ${model} ${reset}" \
  "${C_MODEL_BG}$(fg 24)${SEP_HARD}${reset}" \
  "${C_DIR_BG}${C_DIR_FG}  ${base_dir} ${reset}"

if [ -n "$git_seg" ]; then
  printf "%b" \
    "${C_DIR_BG}$(fg 236)${SEP_HARD}${reset}" \
    "${C_GIT_BG}${C_GIT_FG}${git_seg} ${reset}" \
    "$(fg 236)${SEP_HARD}${reset}"
else
  printf "%b" "${C_DIR_BG}$(fg 236)${SEP_HARD}${reset}"
fi

DIV=" ${C_DIM}${dim}│${reset}"
[ -n "$ctx_seg" ] && printf "%b" "${DIV}${ctx_seg}"
[ -n "$rl5_seg" ] && printf "%b" "${DIV}${rl5_seg}"
[ -n "$rl7_seg" ] && printf "%b" "${DIV}${rl7_seg}"

printf "%b\n" "${reset}"
