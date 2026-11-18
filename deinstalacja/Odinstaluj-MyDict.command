#!/bin/bash
# Deinstalator MyDict dla macOS (na Windows robi to deinstalator Inno Setup).
# Usuwa: aplikację, dane programu (pobrane składniki whisper.cpp i modele, dzienniki),
# ustawienia, klucze API z pęku kluczy i modele mowy z pamięci podręcznej Hugging Face.
#
#   --app <ścieżka .app>   pakiet aplikacji do usunięcia
#   --hf-cache <folder>    pamięć podręczna Hugging Face (hub)
#   --wait-pid <pid>       poczekaj, aż zamknie się program, który uruchomił deinstalację
set -uo pipefail

APP_NAME="MyDict"
APP_BUNDLE=""
HF_CACHE="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
WAIT_PID=0
FROM_TEMP=0
ARGS=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP_BUNDLE="${2:-}"; shift 2 ;;
    --hf-cache) HF_CACHE="${2:-}"; shift 2 ;;
    --wait-pid) WAIT_PID="${2:-0}"; shift 2 ;;
    --from-temp) FROM_TEMP=1; shift ;;
    *) shift ;;
  esac
done

# Skrypt leży w pakiecie, który usuwa — uruchom kopię z folderu tymczasowego.
if [[ $FROM_TEMP -eq 0 ]]; then
  COPY="$(/usr/bin/mktemp /private/tmp/Odinstaluj-MyDict.XXXXXX)"
  /bin/cp "$0" "$COPY"
  /usr/bin/nohup /bin/bash "$COPY" "${ARGS[@]}" --from-temp >/dev/null 2>&1 &
  exit 0
fi
trap '/bin/rm -f "$0"' EXIT

if [[ "$WAIT_PID" -gt 0 ]]; then
  for _ in $(/usr/bin/seq 1 60); do
    /bin/kill -0 "$WAIT_PID" 2>/dev/null || break
    /bin/sleep 1
  done
fi

FAILED=()
remove_path() {
  [[ -z "$1" ]] && return
  if [[ -e "$1" || -L "$1" ]]; then
    /bin/rm -rf "$1" 2>/dev/null || FAILED+=("$1")
  fi
}

# Aplikacja — tylko pakiet .app o nazwie programu (nigdy inny folder).
if [[ "$APP_BUNDLE" == *"/$APP_NAME.app" && -d "$APP_BUNDLE/Contents" ]]; then
  remove_path "$APP_BUNDLE"
fi
[[ -L "$HOME/Desktop/$APP_NAME" ]] && remove_path "$HOME/Desktop/$APP_NAME"

# Dane, dzienniki i ustawienia (także po dawnej nazwie BAPO-dyktowanie).
for name in "$APP_NAME" "BAPO-dyktowanie"; do
  remove_path "$HOME/Library/Application Support/$name"
  remove_path "$HOME/Library/Logs/$name"
  # „defaults delete” czyści też kopię w pamięci systemu (cfprefsd), samo usunięcie pliku nie.
  for domain in "com.bapo.$name" "com.BAPO.$name"; do
    /usr/bin/defaults delete "$domain" >/dev/null 2>&1 || true
    remove_path "$HOME/Library/Preferences/$domain.plist"
  done
done

# Klucze API zapisane w pęku kluczy (usługa „MyDict”).
for _ in $(/usr/bin/seq 1 100); do
  /usr/bin/security delete-generic-password -s "$APP_NAME" >/dev/null 2>&1 || break
done

# Modele mowy pobrane przez program (faster-whisper).
for repo in Systran--faster-whisper-small Systran--faster-whisper-medium Systran--faster-whisper-large-v3 mobiuslabsgmbh--faster-whisper-large-v3-turbo; do
  remove_path "$HF_CACHE/models--$repo"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  TEXT="Nie udało się usunąć: ${FAILED[*]}. Usuń je ręcznie."
else
  TEXT="$APP_NAME został odinstalowany."
fi
/usr/bin/osascript - "$TEXT" <<'APPLESCRIPT' >/dev/null 2>&1
on run argv
  display alert "Deinstalacja MyDict" message (item 1 of argv)
end run
APPLESCRIPT
