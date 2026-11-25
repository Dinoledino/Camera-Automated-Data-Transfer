#!/usr/bin/env bash
# Partage de connexion USB pour le Galaxy A50 + surveillance de KnoxGuard (Ubuntu).
#
# Le Wi-Fi de l'A50 est en panne : le PC lui donne Internet par le câble (gnirehtet, « reverse
# tethering »), pour que le client KnoxGuard puisse joindre les serveurs de Samsung.
#
# Ce script tourne en continu :
#   - il attend qu'un A50 (SM-A505…) soit branché, avec le débogage USB autorisé ;
#   - il lance le relais gnirehtet et le client sur le téléphone, et relance le client si le
#     partage se coupe ;
#   - toutes les 2 minutes, il lit l'état de KnoxGuard et prévient (notification de bureau +
#     journal) à chaque nouvelle ligne d'état.
#
# Utilisation :
#   ./a50-tether.sh            # en continu (Ctrl-C pour arrêter)
#   ./a50-tether.sh --check    # état actuel, une fois, puis s'arrête
#
# Lancement automatique à l'ouverture de session : voir a50-tether.service (même dossier).
#
# Journaux : ~/.local/state/a50-tether/ (tether.log, knoxguard.log, gnirehtet.log)

set -u

GNIREHTET_DIR="${GNIREHTET_DIR:-$HOME/.local/share/gnirehtet/gnirehtet-rust-linux64}"
GNIREHTET="$GNIREHTET_DIR/gnirehtet"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/a50-tether"
LOG="$STATE_DIR/tether.log"
KG_LOG="$STATE_DIR/knoxguard.log"
RELAY_LOG="$STATE_DIR/gnirehtet.log"
MODEL_PATTERN="${MODEL_PATTERN:-model:SM_A505}"
CHECK_EVERY=60          # secondes entre deux vérifications du téléphone et du partage
KG_EVERY=2              # lecture de KnoxGuard tous les KG_EVERY tours (2 min)
# Lignes de KnoxGuard qui méritent une alerte :
KG_ALERT='vaultState|isResultSuccess|StateToRpmb|Completed|Locked|Active'

mkdir -p "$STATE_DIR"
RELAY_PID=""

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG" >&2; }

notify() {  # notification de bureau si possible, toujours dans le journal
  log "ALERTE : $*"
  command -v notify-send >/dev/null 2>&1 && notify-send -u critical "A50 / KnoxGuard" "$*" 2>/dev/null || true
}

phone_serial() {  # numéro de série de l'A50 branché (et autorisé), ou rien
  adb devices -l 2>/dev/null | awk -v pat="$MODEL_PATTERN" '$2 == "device" && index($0, pat) { print $1; exit }'
}

unauthorized_phone() {
  adb devices 2>/dev/null | grep -q 'unauthorized'
}

vpn_connected() {
  adb -s "$1" shell dumpsys connectivity 2>/dev/null | grep -q 'type: VPN\[\], state: CONNECTED'
}

kg_dump() {
  timeout 30 adb -s "$1" shell dumpsys knoxguard_service 2>/dev/null | tr -d '\r'
}

kg_state_line() {
  kg_dump "$1" | grep -E 'vaultState|StateToRpmb' | tail -1
}

relay_running() {
  [ -n "$RELAY_PID" ] && kill -0 "$RELAY_PID" 2>/dev/null
}

start_relay() {
  if relay_running; then return; fi
  if pgrep -f "gnirehtet (run|relay|autorun)" >/dev/null 2>&1; then
    log "un relais gnirehtet tourne déjà (lancé ailleurs) : je l'utilise"
    return
  fi
  "$GNIREHTET" relay >>"$RELAY_LOG" 2>&1 &
  RELAY_PID=$!
  log "relais gnirehtet lancé (pid $RELAY_PID)"
  sleep 2
}

start_client() {
  local serial="$1"
  if ! adb -s "$serial" shell pm list packages 2>/dev/null | grep -q com.genymobile.gnirehtet; then
    log "installation du client gnirehtet sur le téléphone"
    (cd "$GNIREHTET_DIR" && timeout 60 "$GNIREHTET" install "$serial" >>"$RELAY_LOG" 2>&1)
  fi
  (cd "$GNIREHTET_DIR" && timeout 30 "$GNIREHTET" start "$serial" >>"$RELAY_LOG" 2>&1)
  log "client gnirehtet démarré (si une demande de VPN s'affiche sur le téléphone : l'accepter)"
}

cleanup() {
  log "arrêt demandé"
  if relay_running; then kill "$RELAY_PID" 2>/dev/null; fi
  exit 0
}

check_once() {
  local serial
  serial=$(phone_serial)
  if [ -z "$serial" ]; then
    if unauthorized_phone; then echo "Téléphone branché mais débogage USB pas encore autorisé (accepter sur l'écran)."
    else echo "Aucun A50 détecté."; fi
    return 1
  fi
  echo "A50 détecté : $serial"
  if vpn_connected "$serial"; then echo "Partage de connexion : actif"; else echo "Partage de connexion : inactif"; fi
  echo "KnoxGuard (dernière ligne d'état) : $(kg_state_line "$serial")"
}

# ------------------------------------------------------------------ programme principal

for tool in adb timeout; do
  command -v "$tool" >/dev/null 2>&1 || { echo "outil manquant : $tool" >&2; exit 1; }
done
[ -x "$GNIREHTET" ] || { echo "gnirehtet introuvable : $GNIREHTET (variable GNIREHTET_DIR)" >&2; exit 1; }

if [ "${1:-}" = "--check" ]; then
  check_once
  exit $?
fi

trap cleanup INT TERM
adb start-server >/dev/null 2>&1
log "démarrage : en attente d'un A50 branché"

serial="" ; prev_kg="" ; tick=0 ; warned_unauth=0
while true; do
  current=$(phone_serial)
  if [ -z "$current" ]; then
    if [ -n "$serial" ]; then log "téléphone débranché"; serial=""; fi
    if unauthorized_phone && [ $warned_unauth -eq 0 ]; then
      notify "Téléphone branché : autorise le débogage USB sur son écran."
      warned_unauth=1
    fi
    sleep 15
    continue
  fi
  warned_unauth=0

  if [ "$current" != "$serial" ]; then
    serial="$current"
    log "A50 détecté ($serial)"
    start_relay
    start_client "$serial"
    prev_kg=$(kg_dump "$serial")
    log "KnoxGuard au démarrage : $(printf '%s\n' "$prev_kg" | grep -E 'vaultState|StateToRpmb' | tail -1)"
    tick=0
  fi

  if ! relay_running && ! pgrep -f "gnirehtet (run|relay|autorun)" >/dev/null 2>&1; then
    log "relais gnirehtet arrêté : relance"
    RELAY_PID=""
    start_relay
  fi
  if ! vpn_connected "$serial"; then
    log "partage de connexion coupé : relance du client"
    start_client "$serial"
  fi

  tick=$((tick + 1))
  if [ $((tick % KG_EVERY)) -eq 0 ]; then
    dump=$(kg_dump "$serial")
    if [ -n "$dump" ]; then
      new=$(printf '%s\n' "$dump" | grep -vxF -f <(printf '%s\n' "$prev_kg"))
      prev_kg="$dump"
      if [ -n "$new" ]; then
        printf '%s\n' "$new" >>"$KG_LOG"
        if printf '%s\n' "$new" | grep -qE "$KG_ALERT"; then
          notify "KnoxGuard a changé : $(printf '%s\n' "$new" | grep -E "$KG_ALERT" | tail -1 | cut -c1-160)"
        fi
      fi
    fi
  fi
  sleep "$CHECK_EVERY"
done
