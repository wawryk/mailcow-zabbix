#!/bin/bash
# mailcow_mail.sh - metryki kontenerow mailcow dla szablonu Zabbix "Mailcow by Zabbix agent".
# Uruchamiany przez sudo (patrz zabbix_agent/sudoers-zabbix-mailcow) - agent nie musi byc w grupie docker.
#
#   queue_total | queue_deferred                 -> liczba wiadomosci w kolejce Postfixa
#   sent | bounced | rejects_reputation [minuty] -> zdarzenia z logu postfix-mailcow w ostatnich N minutach
#   containers_running | containers_down         -> kontenery mailcow (down = zatrzymane/restartujace/unhealthy)
#   backup_age [katalog]                         -> wiek najnowszego backupu mailcow-* w godzinach
# Zwraca -1, gdy nie da sie odczytac danych (brak dockera, kontenera, backupu).

mode="$1"; arg="$2"
command -v docker >/dev/null 2>&1 || { echo -1; exit 0; }

postfix_c() { docker ps -qf name=postfix-mailcow 2>/dev/null | head -1; }
need_pf() { pf=$(postfix_c); [ -z "$pf" ] && { echo -1; exit 0; }; }
logs() { docker logs --since "${arg:-10}m" "$pf" 2>&1; }

case "$mode" in
  queue_total)
    need_pf; docker exec "$pf" postqueue -j 2>/dev/null | grep -c . ;;
  queue_deferred)
    need_pf; docker exec "$pf" postqueue -j 2>/dev/null | grep -c '"queue_name": *"deferred"' ;;
  sent)
    # tylko poczta wychodzaca: pomijamy lokalne dostarczenia do dovecot (LMTP)
    need_pf; logs | grep 'status=sent' | grep -vcE 'relay=(dovecot|local|virtual)' ;;
  bounced)
    need_pf; logs | grep -c 'status=bounced' ;;
  rejects_reputation)
    # odrzucenia przez serwery docelowe z powodu reputacji / list blokujacych
    need_pf
    logs | grep -E 'status=(bounced|deferred)' | \
      grep -ciE 'blocklist|blacklist|block list|spamhaus|reputation|S3140|S3150|SC-00[1-9]|5\.7\.26|4\.7\.0|5\.7\.1.*(spam|policy|listed|block)' ;;
  containers_running)
    docker ps --filter name=-mailcow- -q 2>/dev/null | grep -c . ;;
  containers_down)
    docker ps -a --filter name=-mailcow- --format '{{.State}}|{{.Status}}' 2>/dev/null | \
      grep -cE '^(exited|dead|restarting|created|paused)|\(unhealthy\)' ;;
  backup_age)
    dir="${arg:-/opt/backup}"
    newest=$(find "$dir" -maxdepth 1 -type d -name 'mailcow-*' -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
    [ -z "$newest" ] && { echo -1; exit 0; }
    echo $(( ( $(date +%s) - ${newest%.*} ) / 3600 )) ;;
  *)
    echo "ZBX_NOTSUPPORTED: unknown mode '$mode'"; exit 1 ;;
esac
exit 0
