#!/bin/bash
# mailcow_mail.sh - metryki kontenerow mailcow dla szablonu Zabbix "Mailcow by Zabbix agent".
# Uruchamiany przez sudo (patrz zabbix_agent/sudoers-zabbix-mailcow) - agent nie musi byc w grupie docker.
#
#   queue_total | queue_deferred                 -> liczba wiadomosci w kolejce Postfixa
#   sent | bounced | rejects_reputation [minuty] -> zdarzenia z logu postfix-mailcow w ostatnich N minutach
#   containers_running | containers_down         -> kontenery mailcow (down = zatrzymane/restartujace/unhealthy)
#   backup_age [katalog]                         -> wiek najnowszego backupu mailcow-* w godzinach
#   postfix_stats [minuty]                       -> JSON: statusy dostarczen i odrzucenia z logu Postfixa
#   rspamd_stats [minuty]                        -> JSON: liczniki akcji rspamd, Bayes, rozklad ocen spamu
# Zwraca -1 (tryby JSON: {"ok":0}), gdy nie da sie odczytac danych (brak dockera, kontenera, backupu).

mode="$1"; arg="$2"
case "$mode" in
  sent|bounced|rejects_reputation|postfix_stats|rspamd_stats)
    [[ "${arg:-10}" =~ ^[0-9]+$ ]] || { echo "ZBX_NOTSUPPORTED: minutes must be a number"; exit 1; } ;;
esac
case "$mode" in
  postfix_stats|rspamd_stats) fail='{"ok":0}' ;;
  *) fail=-1 ;;
esac
command -v docker >/dev/null 2>&1 || { echo "$fail"; exit 0; }

postfix_c() { docker ps -qf name=postfix-mailcow 2>/dev/null | head -1; }
need_pf() { pf=$(postfix_c); [ -z "$pf" ] && { echo "$fail"; exit 0; }; }
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
  postfix_stats)
    # jeden odczyt logu dla wszystkich statusow; liczone sa linie (= odbiorcy), nie wiadomosci
    need_pf
    logs | awk '
      /status=sent/      { if ($0 ~ /relay=(dovecot|local|virtual)/) dl++; else sent++ }
      /status=deferred/  { def++ }
      /status=bounced/   { bnc++ }
      /status=expired/   { exp_++ }
      /NOQUEUE: reject:/ { rej++ }
      /milter-reject:/   { mrej++ }
      /status=(bounced|deferred)/ {
        l = tolower($0)
        if (l ~ /blocklist|blacklist|block list|spamhaus|reputation|s3140|s3150|sc-00[1-9]|5\.7\.26|4\.7\.0|5\.7\.1.*(spam|policy|listed|block)/) rep++
      }
      END { printf "{\"ok\":1,\"sent\":%d,\"delivered_local\":%d,\"deferred\":%d,\"bounced\":%d,\"expired\":%d,\"rejected\":%d,\"milter_rejected\":%d,\"rejects_reputation\":%d}\n",
                  sent, dl, def, bnc, exp_, rej, mrej, rep }' ;;
  rspamd_stats)
    # rspamd controller przez socket /var/lib/rspamd/rspamd.sock zamontowany w php-fpm-mailcow
    # (z tego samego korzysta panel mailcow); agregacja w PHP, bo na hoscie nie musi byc jq/pythona
    php=$(docker ps -qf name=php-fpm-mailcow 2>/dev/null | head -1)
    [ -z "$php" ] && { echo "$fail"; exit 0; }
    docker exec -i "$php" php -- "${arg:-10}" <<'PHP' 2>/dev/null || echo "$fail"
<?php
function get($path) {
  $c = curl_init("http://rspamd$path");
  curl_setopt_array($c, [CURLOPT_UNIX_SOCKET_PATH => '/var/lib/rspamd/rspamd.sock',
                         CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 10]);
  $r = curl_exec($c);
  return $r === false ? null : json_decode($r, true);
}
$st = get('/stat');
if (!is_array($st) || !isset($st['actions'])) { echo '{"ok":0}', "\n"; exit(0); }
$out = ['ok' => 1, 'scanned' => $st['scanned'] ?? 0, 'learned' => $st['learned'] ?? 0, 'actions' => [],
        'bayes' => ['spam' => 0, 'ham' => 0]];
foreach (['reject', 'soft reject', 'rewrite subject', 'add header', 'greylist', 'no action'] as $a)
  $out['actions'][str_replace(' ', '_', $a)] = $st['actions'][$a] ?? 0;
foreach ($st['statfiles'] ?? [] as $sf) {                    // revision = liczba nauczonych wiadomosci
  $cls = $sf['class'] ?? (($sf['symbol'] ?? '') === 'BAYES_HAM' ? 'ham' : 'spam');
  if (isset($out['bayes'][$cls])) $out['bayes'][$cls] += $sf['revision'] ?? 0;
}

// oceny z historii (history_redis) z ostatnich N minut
$since = time() - 60 * (int)($argv[1] ?? 10);
$h = get('/history');
$rows = is_array($h) ? ($h['rows'] ?? (isset($h[0]) ? $h : [])) : [];      // v2: {rows:[]}, v1: []
$sc = []; $ham = []; $spam = []; $border = 0;
$b = ['neg' => 0, '0_5' => 0, '5_10' => 0, '10_15' => 0, '15' => 0];
foreach ($rows as $r) {
  if (!isset($r['score'], $r['unix_time']) || $r['unix_time'] < $since || !empty($r['is_skipped'])) continue;
  $s = (float)$r['score']; $a = $r['action'] ?? '';
  $sc[] = $s;
  if ($a === 'no action') $ham[] = $s;
  elseif (in_array($a, ['add header', 'rewrite subject', 'reject'])) $spam[] = $s;
  $t = $r['thresholds']['add header'] ?? $r['thresholds']['rewrite subject'] ?? null;
  if ($t !== null && abs($s - $t) < 2) $border++;          // ocena blisko progu oznaczenia jako spam
  $b[$s < 0 ? 'neg' : ($s < 5 ? '0_5' : ($s < 10 ? '5_10' : ($s < 15 ? '10_15' : '15')))]++;
}
$n = count($sc);
$w = ['n' => $n, 'buckets' => $b];
if ($n) {
  sort($sc);
  $w += ['avg' => round(array_sum($sc) / $n, 2), 'p50' => $sc[intdiv($n - 1, 2)],
         'p95' => $sc[(int)ceil(0.95 * $n) - 1], 'max' => end($sc),
         'spam_pct' => round(100 * count($spam) / $n, 1), 'borderline_pct' => round(100 * $border / $n, 1)];
  if ($ham) $w['avg_ham'] = round(array_sum($ham) / count($ham), 2);
  if ($spam) $w['avg_spam'] = round(array_sum($spam) / count($spam), 2);
}
$out['window'] = $w;
echo json_encode($out), "\n";
PHP
    ;;
  *)
    echo "ZBX_NOTSUPPORTED: unknown mode '$mode'"; exit 1 ;;
esac
exit 0
