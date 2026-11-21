#!/bin/bash
# mailrep.sh - reputacja i DNS serwera pocztowego dla szablonu Zabbix "Mailcow by Zabbix agent".
# Nie wymaga uprawnien root ani dostepu do Dockera.
#
#   mailrep.sh check <IP|domena> <strefa_rbl> [resolver]   -> 0 czysto | 1 NA LISCIE | 2 blad/odmowa
#   mailrep.sh discover-zones "<z1,z2,...>"                  -> LLD JSON {#RBL}
#   mailrep.sh discover-domains "<d1,d2,...>"                -> LLD JSON {#DOMAIN}
#   mailrep.sh discover-domainzones "<d1,..>" "<z1,..>"      -> LLD JSON {#DOMAIN},{#RBL}
#   mailrep.sh dns <TYP> <nazwa> [resolver]                  -> rekordy w jednej linii | "" | DNS_ERROR
#   mailrep.sh ptr <IP> [resolver]                           -> nazwa z PTR | "" | DNS_ERROR
#   mailrep.sh fcrdns <hostname> <IP> [resolver]             -> 1 gdy A(hostname) zawiera IP, inaczej 0
#   mailrep.sh certdays <host> <port> [smtp|imap|pop3]       -> dni do wygasniecia certyfikatu | -1 blad

cmd="$1"; shift
DEFAULT_RESOLVER="1.1.1.1"

split_list() { echo "$1" | tr ',; ' '\n\n\n' | sed '/^$/d'; }
is_ip() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; }

case "$cmd" in
  check)
    target="$1"; zone="$2"; r="${3:-$DEFAULT_RESOLVER}"
    if [ -z "$target" ] || [ -z "$zone" ]; then echo 2; exit 0; fi
    if is_ip "$target"; then
      q=$(echo "$target" | awk -F. '{print $4"."$3"."$2"."$1}')
    elif [[ "$target" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
      q="$target"
    else
      echo 2; exit 0                                    # np. nieustawione makro
    fi
    out=$(dig +short +time=2 +tries=2 "$q.$zone" A @"$r" 2>/dev/null); rc=$?
    res=$(echo "$out" | grep -E '^[0-9.]+$' | head -1)
    if [ $rc -ne 0 ]; then echo 2
    elif [ -z "$res" ]; then echo 0                     # NXDOMAIN = brak wpisu
    elif [[ "$res" =~ ^127\.255\.255\. ]] || [[ "$res" == "127.0.0.1" ]] || [[ "$res" =~ ^127\.0\.0\.25[0-9]$ ]]; then
      echo 2                                            # odmowa zapytania / kod bledu listy
    elif [[ "$res" =~ ^127\. ]]; then echo 1
    else echo 2; fi
    ;;

  discover-zones)
    out=""; for z in $(split_list "$1"); do out+="{\"{#RBL}\":\"$z\"},"; done
    echo "[${out%,}]" ;;

  discover-domains)
    out=""; for d in $(split_list "$1"); do out+="{\"{#DOMAIN}\":\"$d\"},"; done
    echo "[${out%,}]" ;;

  discover-domainzones)
    out=""
    for d in $(split_list "$1"); do
      for z in $(split_list "$2"); do out+="{\"{#DOMAIN}\":\"$d\",\"{#RBL}\":\"$z\"},"; done
    done
    echo "[${out%,}]" ;;

  dns)
    type="$1"; name="$2"; r="${3:-$DEFAULT_RESOLVER}"
    full=$(dig +time=2 +tries=2 "$name" "$type" @"$r" 2>/dev/null); rc=$?
    status=$(echo "$full" | grep -o 'status: [A-Z]*' | head -1 | awk '{print $2}')
    if [ $rc -ne 0 ] || { [ "$status" != "NOERROR" ] && [ "$status" != "NXDOMAIN" ]; }; then
      echo "DNS_ERROR"; exit 0
    fi
    dig +short +time=2 +tries=2 "$name" "$type" @"$r" 2>/dev/null | sort | tr '\n' ' ' | sed 's/ *$//'
    echo ;;

  ptr)
    ip="$1"; r="${2:-$DEFAULT_RESOLVER}"
    is_ip "$ip" || { echo "DNS_ERROR"; exit 0; }
    out=$(dig +short +time=2 +tries=2 -x "$ip" @"$r" 2>/dev/null) || { echo "DNS_ERROR"; exit 0; }
    echo "$out" | head -1 | sed 's/\.$//' ;;

  fcrdns)
    host="$1"; ip="$2"; r="${3:-$DEFAULT_RESOLVER}"
    dig +short +time=2 +tries=2 "$host" A @"$r" 2>/dev/null | grep -qxF "$ip" && echo 1 || echo 0 ;;

  certdays)
    host="$1"; port="$2"; proto="$3"
    [ -z "$host" ] || [ -z "$port" ] && { echo -1; exit 0; }
    opts=(-connect "$host:$port" -servername "$host")
    [ -n "$proto" ] && opts+=(-starttls "$proto")
    end=$(echo | timeout 10 openssl s_client "${opts[@]}" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
    if [ -z "$end" ]; then echo -1; exit 0; fi
    echo $(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 )) ;;

  *)
    echo "ZBX_NOTSUPPORTED: unknown command '$cmd'"; exit 1 ;;
esac
