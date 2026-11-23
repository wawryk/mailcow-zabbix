#!/usr/bin/env python3
"""Generator szablonu Zabbix "Mailcow by Zabbix agent" (format eksportu 6.0, import w 6.0-7.x).

Uzycie:  python3 build_template.py > template_mailcow_zabbix_agent.yaml
Wymaga: PyYAML. UUID sa deterministyczne (UUIDv4 wyliczane z nazwy obiektu),
wiec ponowne wygenerowanie i import aktualizuje istniejacy szablon zamiast tworzyc duplikaty.
"""
import hashlib
import sys

import yaml

T = "Mailcow by Zabbix agent"
GROUP = "Templates/Applications"
VM_RBL = "Mailcow RBL status"
VM_BOOL = "Mailcow yes/no"


def uid(*parts):
    h = list(hashlib.md5(("mailcow-zbx|" + "|".join(parts)).encode()).hexdigest())
    h[12] = "4"                                   # UUID version 4
    h[16] = "89ab"[int(h[16], 16) % 4]            # RFC 4122 variant
    return "".join(h)


def tags(**kw):
    return [{"tag": k, "value": v} for k, v in kw.items()]


def trig(key_expr, name, prio, desc, kind="item", **tg):
    t = {
        "uuid": uid("trigger", name),
        "expression": key_expr,
        "name": name,
        "priority": prio,
        "description": desc,
        "manual_close": "YES",
    }
    if tg:
        t["tags"] = tags(**tg)
    return t


def ref(key):
    return f"/{T}/{key}"


def item(name, key, delay, desc, comp, value_type=None, units=None, history="31d",
         trends=None, valuemap=None, itype=None, triggers=None, extra=None, status=None):
    if value_type is None and key.startswith(("mailcow.mail[", "mailrep.certdays[")):
        value_type = "FLOAT"                      # skrypty zwracaja -1 przy bledzie (unsigned by odrzucil)
    it = {"uuid": uid("item", key), "name": name}
    if itype:
        it["type"] = itype
    it["key"] = key
    it["delay"] = delay
    it["history"] = history
    if value_type:
        it["value_type"] = value_type
    if trends is not None:
        it["trends"] = trends
    if units:
        it["units"] = units
    if status:
        it["status"] = status
    it["description"] = desc
    if valuemap:
        it["valuemap"] = {"name": valuemap}
    if extra:
        it.update(extra)
    it["tags"] = tags(component=comp)
    if triggers:
        it["triggers"] = triggers
    return it


def dep(name, key, master, path, desc, comp, units=None, change=False, missing=None, **kw):
    """Item zalezny od JSON-a z master itemu. change=True: licznik narastajacy -> przyrost na interwal
    (spadek po restarcie rspamd Zabbix odrzuca). missing: brak pola w JSON-ie -> '0' = zero,
    'discard' = pomin wartosc (np. srednia, gdy w oknie nie bylo wiadomosci), None = item unsupported."""
    jp = {"type": "JSONPATH", "parameters": [path]}
    if missing == "discard":
        jp["error_handler"] = "DISCARD_VALUE"
    elif missing is not None:
        jp.update(error_handler="CUSTOM_VALUE", error_handler_params=missing)
    pp = [jp] + ([{"type": "SIMPLE_CHANGE", "parameters": [""]}] if change else [])
    kw.setdefault("value_type", "FLOAT")
    return item(name, key, "0", desc, comp, units=units, itype="DEPENDENT",
                extra={"master_item": {"key": master}, "preprocessing": pp}, **kw)


def calc(name, key, formula, desc, comp, units=None):
    return item(name, key, "10m", desc, comp, value_type="FLOAT", units=units, itype="CALCULATED",
                extra={"params": formula})


# ----------------------------------------------------------------- items
items = []

# --- master itemy JSON (jeden odczyt logu Postfixa / rspamd na 10 min, reszta to itemy zalezne)
k_pf = "mailcow.mail[postfix_stats,10]"
items.append(item(
    "Postfix: log stats (10 min, JSON)", k_pf, "10m",
    "Statusy dostarczen i odrzucenia z logu postfix-mailcow w ostatnich 10 min. Zrodlo dla itemow zaleznych.",
    "mailflow", value_type="TEXT", history="0", trends=None))
k_rs = "mailcow.mail[rspamd_stats,10]"
items.append(item(
    "Rspamd: stats (JSON)", k_rs, "10m",
    "Liczniki rspamd (/stat) i oceny wiadomosci z historii (/history) z ostatnich 10 min, "
    "czytane przez socket kontrolera w php-fpm-mailcow. Zrodlo dla itemow zaleznych.",
    "spam", value_type="TEXT", history="0"))

# --- kolejka i ruch pocztowy (mailcow_mail.sh przez sudo)
k_q = "mailcow.mail[queue_total]"
items.append(item(
    "Postfix: queue size", k_q, "5m",
    "Liczba wiadomosci w kolejce Postfixa (postfix-mailcow). -1 = brak dostepu do dockera/kontenera.",
    "queue", units="msg", triggers=[
        trig(f"min({ref(k_q)},15m)>{{$MAIL.QUEUE.MAX}}",
             "Mailcow: Postfix queue > {$MAIL.QUEUE.MAX} for 15m", "HIGH",
             "Rosnaca kolejka zwykle oznacza problem z dostarczaniem albo przejete konto wysylajace spam. "
             "Sprawdz: docker compose exec postfix-mailcow mailq", scope="performance"),
        trig(f"last({ref(k_q)})<0",
             "Mailcow: cannot read mail metrics (docker/sudo/postfix-mailcow)", "WARNING",
             "Skrypt mailcow_mail.sh zwraca -1: brak dockera, brak kontenera postfix-mailcow albo brak wpisu sudoers.",
             scope="availability"),
    ]))

k_d = "mailcow.mail[queue_deferred]"
items.append(item(
    "Postfix: deferred messages", k_d, "5m",
    "Wiadomosci odroczone (deferred) - serwery docelowe odrzucaja tymczasowo.",
    "queue", units="msg", triggers=[
        trig(f"min({ref(k_d)},30m)>{{$MAIL.DEFERRED.MAX}}",
             "Mailcow: deferred queue > {$MAIL.DEFERRED.MAX} for 30m", "AVERAGE",
             "Sprawdz przyczyny w logu (np. blokady reputacyjne Microsoft/Gmail): "
             "docker compose logs postfix-mailcow | grep deferred", scope="performance"),
    ]))

# sent/bounced/rejects_reputation: te same klucze i UUID co w poprzedniej wersji (historia zostaje),
# ale teraz jako itemy zalezne od postfix_stats zamiast osobnych wywolan agenta
k_s = "mailcow.mail[sent,10]"
items.append(dep(
    "Postfix: outbound sent (10 min)", k_s, k_pf, "$.sent",
    "Wiadomosci wyslane na zewnatrz w ostatnich 10 min (bez lokalnych dostarczen do dovecot).",
    "mailflow", units="msg", triggers=[
        trig(f"last({ref(k_s)})>{{$MAIL.SENT.MAX}}",
             "Mailcow: unusually high outbound volume (>{$MAIL.SENT.MAX}/10 min)", "HIGH",
             "Mozliwe przejete konto rozsylajace spam - grozi wpisem na listy RBL. "
             "Sprawdz nadawcow w panelu mailcow (Logs > Postfix) i ewentualnie zablokuj konto.", scope="security"),
    ]))

k_b = "mailcow.mail[bounced,10]"
items.append(dep(
    "Postfix: bounced (10 min)", k_b, k_pf, "$.bounced",
    "Wiadomosci trwale odrzucone (status=bounced) w ostatnich 10 min.",
    "mailflow", units="msg", triggers=[
        trig(f"last({ref(k_b)})>{{$MAIL.BOUNCED.MAX}}",
             "Mailcow: many bounces (>{$MAIL.BOUNCED.MAX}/10 min)", "WARNING",
             "Duza liczba odbic - sprawdz, czy nie dotycza jednego nadawcy (spam) lub jednej domeny docelowej.",
             scope="performance"),
    ]))

k_r = "mailcow.mail[rejects_reputation,10]"
items.append(dep(
    "Postfix: reputation rejects (10 min)", k_r, k_pf, "$.rejects_reputation",
    "Odrzucenia przez serwery docelowe z powodow reputacji/list blokujacych "
    "(Microsoft S3140/S3150, Gmail 5.7.26/4.7.0, Spamhaus, blocklist...).",
    "reputation", units="msg", triggers=[
        trig(f"last({ref(k_r)})>0",
             "Mailcow: remote servers reject mail for reputation reasons", "AVERAGE",
             "Serwery docelowe odrzucaja poczte powolujac sie na reputacje/listy blokujace. "
             "Sprawdz log: docker compose logs --since 1h postfix-mailcow | grep -iE 'block|reputation|S3150|5.7.26'",
             scope="reputation"),
    ]))

# --- pozostale statusy z logu Postfixa
k_pf_ok = "mailcow.postfix[ok]"
items.append(dep(
    "Postfix: log stats available", k_pf_ok, k_pf, "$.ok",
    "1 = log postfix-mailcow odczytany, 0 = brak kontenera/dockera.", "mailflow", missing="0",
    value_type="UNSIGNED", valuemap=VM_BOOL, triggers=[
        trig(f"last({ref(k_pf_ok)})=0 or nodata({ref(k_pf_ok)},30m)=1",
             "Mailcow: cannot read Postfix log stats", "WARNING",
             "mailcow_mail.sh postfix_stats nie zwraca danych: stara wersja skryptu na serwerze (brak trybu "
             "postfix_stats), brak sudoers albo kontenera postfix-mailcow. Itemy sent/bounced/reputation "
             "oraz dashboard nie dostaja wtedy danych.", scope="availability"),
    ]))
pf_status = [  # (pole JSON, nazwa, opis)
    ("delivered_local", "delivered to mailboxes", "Dostarczenia lokalne do dovecot (poczta przychodzaca i wewnetrzna)."),
    ("deferred", "deferred", "Proby dostarczenia odroczone (status=deferred) - serwer docelowy odrzucil tymczasowo."),
    ("expired", "expired", "Wiadomosci usuniete z kolejki po przekroczeniu czasu ponawiania (status=expired)."),
    ("rejected", "inbound rejected (smtpd/postscreen)", "Polaczenia przychodzace odrzucone przez Postfixa (NOQUEUE: reject) - RBL, bledni odbiorcy, polityki."),
    ("milter_rejected", "inbound rejected by rspamd", "Wiadomosci odrzucone przez rspamd w trakcie sesji SMTP (milter-reject)."),
]
k_pfs = {"sent": k_s, "bounced": k_b}
for field, label, desc in pf_status:
    k = f"mailcow.postfix[{field}]"
    k_pfs[field] = k
    items.append(dep(f"Postfix: {label} (10 min)", k, k_pf, f"$.{field}",
                     desc + " Liczone linie logu, czyli odbiorcy, nie wiadomosci.", "mailflow", units="msg"))

# --- rspamd: akcje, Bayes, jakosc ocen
k_rs_ok = "mailcow.rspamd[ok]"
items.append(dep(
    "Rspamd: stats available", k_rs_ok, k_rs, "$.ok",
    "1 = kontroler rspamd odpowiada przez socket w php-fpm-mailcow.", "spam", missing="0",
    value_type="UNSIGNED", valuemap=VM_BOOL, triggers=[
        trig(f"last({ref(k_rs_ok)})=0 or nodata({ref(k_rs_ok)},30m)=1",
             "Mailcow: cannot read rspamd stats", "WARNING",
             "mailcow_mail.sh rspamd_stats nie zwraca danych: stara wersja skryptu, brak kontenera php-fpm-mailcow "
             "albo socketu /var/lib/rspamd/rspamd.sock. Sprawdz: sudo /usr/local/bin/mailcow_mail.sh rspamd_stats",
             scope="availability"),
    ]))
k_scan = "mailcow.rspamd[scanned]"
items.append(dep("Rspamd: scanned (10 min)", k_scan, k_rs, "$.scanned",
                 "Wiadomosci sprawdzone przez rspamd w interwale (przychodzace i wychodzace).", "spam",
                 units="msg", change=True, missing="discard"))
rs_actions = [  # (pole, nazwa akcji)
    ("no_action", "no action"), ("greylist", "greylist"), ("add_header", "add header"),
    ("rewrite_subject", "rewrite subject"), ("soft_reject", "soft reject"), ("reject", "reject"),
]
k_act = {}
for field, label in rs_actions:
    k = f"mailcow.rspamd[action,{field}]"
    k_act[field] = k
    items.append(dep(f"Rspamd: action {label} (10 min)", k, k_rs, f"$.actions.{field}",
                     f"Wiadomosci z akcja rspamd '{label}' w interwale.", "spam", units="msg",
                     change=True, missing="discard"))
k_lspam, k_lham = "mailcow.rspamd[learned,spam]", "mailcow.rspamd[learned,ham]"
items.append(dep("Rspamd: Bayes learned as spam (10 min)", k_lspam, k_rs, "$.bayes.spam",
                 "Wiadomosci nauczone jako spam (uzytkownik przeniosl do Junk lub autolearn). "
                 "Duzo nauki recznej = spam przechodzi przez filtr (false negative).", "spam",
                 units="msg", change=True, missing="discard"))
items.append(dep("Rspamd: Bayes learned as ham (10 min)", k_lham, k_rs, "$.bayes.ham",
                 "Wiadomosci nauczone jako ham (uzytkownik wyjal z Junk lub autolearn). "
                 "Wzrost = dobra poczta trafia do spamu (false positive).", "spam",
                 units="msg", change=True, missing="discard"))
k_sc = {}
for field, label, units, desc in [
    ("n", "scored messages", "msg", "Liczba wiadomosci z historii rspamd w oknie 10 min (baza dla statystyk ocen). "
                                    "Historia mailcow trzyma ok. 1000 ostatnich wiadomosci - przy wiekszym ruchu okno jest niepelne."),
    ("avg", "average score", "", "Srednia ocena rspamd wszystkich wiadomosci w oknie."),
    ("avg_ham", "average score of clean mail", "", "Srednia ocena wiadomosci z akcja 'no action'. Im nizej, tym pewniej."),
    ("avg_spam", "average score of spam", "", "Srednia ocena wiadomosci oznaczonych jako spam (add header, rewrite subject, reject)."),
    ("p50", "median score", "", "Mediana ocen w oknie."),
    ("p95", "95th percentile score", "", "95. percentyl ocen w oknie."),
    ("max", "max score", "", "Najwyzsza ocena w oknie."),
    ("spam_pct", "spam share", "%", "Udzial wiadomosci oznaczonych jako spam w oknie."),
    ("borderline_pct", "borderline share", "%",
     "Udzial wiadomosci z ocena w odleglosci < 2 pkt od progu 'add header'. Duzy udzial = filtr niepewny, "
     "rosnie ryzyko false positive/negative."),
]:
    k = f"mailcow.rspamd[score,{field}]"
    k_sc[field] = k
    items.append(dep(f"Rspamd: {label} (10 min)", k, k_rs, f"$.window.{field}", desc, "spam",
                     units=units or None, missing="0" if field == "n" else "discard"))
score_buckets = [("neg", "< 0", "43A047"), ("0_5", "0-5", "9CCC65"), ("5_10", "5-10", "FDD835"),
                 ("10_15", "10-15", "FB8C00"), ("15", ">= 15", "E53935")]
k_bk = {}
for field, label, _ in score_buckets:
    k = f"mailcow.rspamd[score_bucket,{field}]"
    k_bk[field] = k
    items.append(dep(f"Rspamd: score {label} (10 min)", k, k_rs, f"$.window.buckets['{field}']",
                     f"Wiadomosci z ocena rspamd w przedziale {label} w oknie 10 min.", "spam",
                     units="msg", missing="0"))

# --- sumy z ostatnich 24 h (do widoku "ostatnie 24h" na dashboardzie)
k_24 = {}
for field, label in [("sent", "outbound sent"), ("delivered_local", "delivered to mailboxes"),
                     ("deferred", "deferred"), ("bounced", "bounced"), ("expired", "expired"),
                     ("rejected", "inbound rejected (smtpd/postscreen)"),
                     ("milter_rejected", "inbound rejected by rspamd")]:
    k = f"mailcow.total24h[{field}]"
    k_24[field] = k
    items.append(calc(f"24h: {label}", k, f"sum(//{k_pfs[field]},24h)",
                      f"Suma z ostatnich 24 h ({label}).", "mailflow", units="msg"))
k_24["scanned"] = "mailcow.total24h[scanned]"
items.append(calc("24h: rspamd scanned", k_24["scanned"], f"sum(//{k_scan},24h)",
                  "Wiadomosci sprawdzone przez rspamd w ostatnich 24 h.", "spam", units="msg"))
k_24["spam"] = "mailcow.total24h[spam]"
items.append(calc("24h: rspamd spam (add header + rewrite subject + reject)", k_24["spam"],
                  "+".join(f"sum(//{k_act[a]},24h)" for a in ("add_header", "rewrite_subject", "reject")),
                  "Wiadomosci oznaczone przez rspamd jako spam w ostatnich 24 h.", "spam", units="msg"))
k_24["reject"] = "mailcow.total24h[spam_rejected]"
items.append(calc("24h: rspamd rejected", k_24["reject"], f"sum(//{k_act['reject']},24h)",
                  "Wiadomosci odrzucone przez rspamd w ostatnich 24 h.", "spam", units="msg"))

# --- kontenery i backup
k_cr = "mailcow.mail[containers_running]"
items.append(item("Mailcow: containers running", k_cr, "2m",
                  "Liczba dzialajacych kontenerow mailcow (*-mailcow-*).", "containers"))
k_cd = "mailcow.mail[containers_down]"
items.append(item(
    "Mailcow: containers down/unhealthy", k_cd, "2m",
    "Kontenery mailcow zatrzymane, restartujace sie lub w stanie unhealthy.", "containers", triggers=[
        trig(f"min({ref(k_cd)},5m)>0",
             "Mailcow: {ITEM.LASTVALUE1} container(s) down or unhealthy", "HIGH",
             "Sprawdz: cd /opt/mailcow-dockerized && docker compose ps", scope="availability"),
    ]))

k_ba = "mailcow.mail[backup_age,{$MAILCOW.BACKUP.DIR}]"
items.append(item(
    "Mailcow: last backup age", k_ba, "30m",
    "Wiek najnowszego katalogu mailcow-* w {$MAILCOW.BACKUP.DIR} (h). -1 = brak backupu/katalogu.",
    "backup", units="h", triggers=[
        trig(f"last({ref(k_ba)})>{{$MAILCOW.BACKUP.MAXAGE}} or last({ref(k_ba)})<0",
             "Mailcow: no backup newer than {$MAILCOW.BACKUP.MAXAGE}h in {$MAILCOW.BACKUP.DIR}", "HIGH",
             "Ostatni backup jest za stary albo go nie ma. Sprawdz cron/timer backupu "
             "(helper-scripts/backup_and_restore.sh) i miejsce na dysku.", scope="backup"),
    ]))

# --- PTR / FCrDNS
k_ptr = "mailrep.ptr[{$MAIL.IP},{$DNS.RESOLVER}]"
items.append(item(
    "DNS: PTR of {$MAIL.IP}", k_ptr, "1h",
    "Rekord PTR (rDNS) adresu IP serwera. Musi wskazywac {$MAIL.HOSTNAME}.",
    "dns", value_type="CHAR", history="90d", triggers=[
        trig(f"find({ref(k_ptr)},,\"regexp\",\"^{{$MAIL.HOSTNAME}}$\")=0 and "
             f"find({ref(k_ptr)},,\"like\",\"DNS_ERROR\")=0",
             "Mailcow: PTR of {$MAIL.IP} is not {$MAIL.HOSTNAME}", "AVERAGE",
             "Brak zgodnego rDNS powoduje odrzucanie lub spam-folder u wielu odbiorcow. "
             "PTR ustawia sie u dostawcy serwera (Infomaniak/Hetzner).", scope="reputation"),
    ]))

k_fc = "mailrep.fcrdns[{$MAIL.HOSTNAME},{$MAIL.IP},{$DNS.RESOLVER}]"
items.append(item(
    "DNS: {$MAIL.HOSTNAME} resolves to {$MAIL.IP}", k_fc, "1h",
    "Forward-confirmed rDNS: rekord A nazwy hosta zawiera IP serwera.",
    "dns", valuemap=VM_BOOL, triggers=[
        trig(f"max({ref(k_fc)},#2)=0",
             "Mailcow: {$MAIL.HOSTNAME} does not resolve to {$MAIL.IP}", "AVERAGE",
             "Rekord A hosta pocztowego nie wskazuje na IP serwera (FCrDNS nie przejdzie).", scope="reputation"),
    ]))

# --- certyfikaty
for port, proto, label in [("443", "", "HTTPS"), ("25", "smtp", "SMTP STARTTLS"),
                           ("587", "smtp", "Submission STARTTLS"), ("993", "", "IMAPS")]:
    key = f"mailrep.certdays[{{$MAIL.HOSTNAME}},{port}" + (f",{proto}]" if proto else "]")
    items.append(item(
        f"TLS: {label} certificate days left", key, "6h",
        f"Dni do wygasniecia certyfikatu {label} na {{$MAIL.HOSTNAME}}:{port}. -1 = brak polaczenia.",
        "tls", units="d", triggers=[
            trig(f"last({ref(key)})>=0 and last({ref(key)})<{{$CERT.EXPIRY.WARN}}",
                 f"Mailcow: {label} certificate expires in {{ITEM.LASTVALUE1}} days", "AVERAGE",
                 "Certyfikat mailcow (ACME) powinien odnawiac sie automatycznie - sprawdz kontener acme-mailcow.",
                 scope="security"),
            trig(f"max({ref(key)},#3)<0",
                 f"Mailcow: cannot check {label} certificate on port {port}", "WARNING",
                 "Brak polaczenia TLS z serwera monitorowanego do samego siebie (port zamkniety, hairpin NAT, timeout).",
                 scope="availability"),
        ]))

# --- dostepnosc uslug z perspektywy serwera Zabbix (simple checks)
for svc, port, label, prio in [("smtp", "25", "SMTP", "HIGH"), ("tcp", "465", "SMTPS", "AVERAGE"),
                               ("tcp", "587", "Submission", "AVERAGE"), ("tcp", "993", "IMAPS", "HIGH"),
                               ("https", "443", "HTTPS (webmail/admin)", "HIGH")]:
    key = f"net.tcp.service[{svc},{{$MAIL.HOSTNAME}},{port}]"
    items.append(item(
        f"Service: {label} port {port}", key, "1m",
        f"Dostepnosc {label} z serwera/proxy Zabbix (simple check).",
        "services", itype="SIMPLE", valuemap=VM_BOOL, triggers=[
            trig(f"max({ref(key)},#3)=0",
                 f"Mailcow: {label} (port {port}) is not reachable", prio,
                 "Usluga nie odpowiada 3 razy z rzedu. Sprawdz kontenery, firewall i blokady portow u dostawcy.",
                 scope="availability"),
        ]))

# --- Microsoft SNDS (domyslnie wylaczone)
k_snds = "mailcow.snds.ipstatus"
items.append(item(
    "Microsoft SNDS: blocked IP status", k_snds, "6h",
    "Lista IP zablokowanych przez Microsoft (Outlook/Hotmail) z SNDS Automated Data Access. "
    "Pusta odpowiedz = brak blokad. Wlacz po ustawieniu {$SNDS.KEY}.",
    "reputation", itype="HTTP_AGENT", value_type="TEXT", history="90d", status="DISABLED",
    extra={"url": "https://sendersupport.olc.protection.outlook.com/snds/ipStatus.aspx?key={$SNDS.KEY}",
           "timeout": "15s"},
    triggers=[
        trig(f"find({ref(k_snds)},,\"regexp\",\"[0-9]+\\\\.[0-9]+\\\\.[0-9]+\\\\.[0-9]+\")=1",
             "Mailcow: Microsoft SNDS reports blocked IP", "HIGH",
             "Microsoft blokuje IP serwera. Szczegoly w panelu SNDS; wniosek o odblokowanie: "
             "https://sender.office.com", scope="reputation"),
    ]))

# ----------------------------------------------------------------- discovery rules


def rbl_rule(name, key, target, zones_macro, prio, suffix=""):
    pkey = f"mailrep.rbl[{target},{{#RBL}},{{$DNS.RESOLVER}}{suffix}]"
    is_domain = "#DOMAIN" in target
    what = "{#DOMAIN}" if is_domain else "{$MAIL.IP}"
    tag = " (informational)" if suffix else ""
    return {
        "uuid": uid("lld", key),
        "name": name,
        "key": key,
        "delay": "1h",
        "lifetime": "7d",
        "description": f"Listy z makra {zones_macro}, rozdzielone przecinkami.",
        "item_prototypes": [{
            "uuid": uid("proto", pkey),
            "name": f"RBL: {what} on {{#RBL}}{tag}",
            "key": pkey,
            "delay": "{$RBL.CHECK.INTERVAL}",
            "history": "90d",
            "description": "0 = czysto, 1 = NA LISCIE, 2 = blad zapytania (np. odmowa dla publicznego resolvera).",
            "valuemap": {"name": VM_RBL},
            "tags": tags(component="reputation", rbl="{#RBL}"),
            "trigger_prototypes": [
                {"uuid": uid("tproto", pkey, "listed"),
                 "expression": f"last({ref(pkey)})=1",
                 "name": f"Mailcow: {what} is listed on {{#RBL}}{tag}",
                 "priority": prio, "manual_close": "YES",
                 "description": "Sprawdz przyczyne (przejete konto? otwarty relay?), usun ja i zlec delisting na stronie listy.",
                 "tags": tags(scope="reputation")},
                {"uuid": uid("tproto", pkey, "error"),
                 "expression": f"min({ref(pkey)},2h)=2",
                 "name": f"Mailcow: RBL check {{#RBL}} for {what} is failing{tag}",
                 "priority": "WARNING", "manual_close": "YES",
                 "description": "Lista odrzuca zapytania (Spamhaus/URIBL przez publiczny DNS - uzyj klucza DQS) "
                                "albo makro {$MAIL.IP}/{$MAIL.DOMAINS} nie jest ustawione.",
                 "tags": tags(scope="reputation")},
            ],
        }],
        "graph_prototypes": [{
            "uuid": uid("gproto", pkey),
            "name": f"RBL status: {what} on {{#RBL}}{tag}",
            "ymin_type_1": "FIXED", "ymax_type_1": "FIXED", "yaxismax": "2",
            "graph_items": [{"color": "E53935", "item": {"host": T, "key": pkey}}],
        }],
    }


rules = [
    rbl_rule("RBL (IP) - critical lists", 'mailrep.discovery[zones,"{$RBL.IP.ZONES}"]',
             "{$MAIL.IP}", "{$RBL.IP.ZONES}", "HIGH"),
    rbl_rule("RBL (IP) - informational lists", 'mailrep.discovery[zones,"{$RBL.IP.ZONES.LOW}"]',
             "{$MAIL.IP}", "{$RBL.IP.ZONES.LOW}", "WARNING", suffix=",low"),
    rbl_rule("RBL (domains)", 'mailrep.discovery[domainzones,"{$MAIL.DOMAINS}","{$RBL.DOMAIN.ZONES}"]',
             "{#DOMAIN}", "{$RBL.DOMAIN.ZONES}", "HIGH"),
]

# --- rekordy DNS domen
dns_checks = [
    ("SPF", "TXT", "{#DOMAIN}", "v=spf1", "AVERAGE",
     "Brak rekordu SPF - poczta bedzie oznaczana jako spam lub odrzucana."),
    ("DMARC", "TXT", "_dmarc.{#DOMAIN}", "v=DMARC1", "WARNING",
     "Brak DMARC - Gmail/Yahoo wymagaja go od nadawcow masowych, obniza zaufanie."),
    ("DKIM", "TXT", "{$MAILCOW.DKIM.SELECTOR}._domainkey.{#DOMAIN}", "p=", "AVERAGE",
     "Brak klucza DKIM w DNS - podpis mailcow nie zostanie zweryfikowany. Klucz: panel mailcow > Configuration > ARC/DKIM keys."),
    ("MX", "MX", "{#DOMAIN}", "{$MAIL.HOSTNAME}", "AVERAGE",
     "Rekord MX domeny nie wskazuje na ten serwer mailcow."),
]
protos = []
for label, rtype, name, needle, prio, desc in dns_checks:
    pkey = f"mailrep.dns[{rtype},{name},{{$DNS.RESOLVER}}]"
    expr = (f"find({ref(pkey)},,\"like\",\"{needle}\")=0 and "
            f"find({ref(pkey)},,\"like\",\"DNS_ERROR\")=0")
    protos.append({
        "uuid": uid("proto", pkey),
        "name": f"DNS: {label} record of {{#DOMAIN}}",
        "key": pkey,
        "delay": "1h",
        "history": "90d",
        "value_type": "CHAR",
        "description": f"Zawartosc rekordu {rtype} {name}. Pusty = brak rekordu, DNS_ERROR = blad zapytania.",
        "tags": tags(component="dns", domain="{#DOMAIN}"),
        "trigger_prototypes": [{
            "uuid": uid("tproto", pkey),
            "expression": expr,
            "name": f"Mailcow: {label} record missing or wrong for {{#DOMAIN}}",
            "priority": prio, "manual_close": "YES",
            "description": desc,
            "tags": tags(scope="reputation"),
        }],
    })
rules.append({
    "uuid": uid("lld", "domains"),
    "name": "Mail domains (DNS records)",
    "key": 'mailrep.discovery[domains,"{$MAIL.DOMAINS}"]',
    "delay": "1h",
    "lifetime": "7d",
    "description": "Domeny z makra {$MAIL.DOMAINS}: kontrola SPF, DMARC, DKIM i MX.",
    "item_prototypes": protos,
})

# ----------------------------------------------------------------- graphs


def gi(color, key, side=None, draw=None):
    g = {"color": color, "item": {"host": T, "key": key}}
    if side:
        g["yaxisside"] = side
    if draw:
        g["drawtype"] = draw
    return g


graphs = [
    {"uuid": uid("graph", "queue"), "name": "Mailcow: Postfix queue",
     "graph_items": [gi("1E88E5", k_q, draw="GRADIENT_LINE"), gi("FB8C00", k_d)]},
    {"uuid": uid("graph", "flow"), "name": "Mailcow: Outbound mail flow (per 10 min)",
     "graph_items": [gi("43A047", k_s, draw="GRADIENT_LINE"), gi("FDD835", k_b), gi("E53935", k_r)]},
    {"uuid": uid("graph", "containers"), "name": "Mailcow: Containers",
     "graph_items": [gi("43A047", k_cr), gi("E53935", k_cd, side="RIGHT")]},
    {"uuid": uid("graph", "backup"), "name": "Mailcow: Backup age",
     "graph_items": [gi("8E24AA", k_ba, draw="GRADIENT_LINE")]},
    {"uuid": uid("graph", "certs"), "name": "Mailcow: TLS certificates days left",
     "graph_items": [gi(c, f"mailrep.certdays[{{$MAIL.HOSTNAME}},{p}]") for c, p in
                     [("1E88E5", "443"), ("00ACC1", "993")]]
                    + [gi(c, f"mailrep.certdays[{{$MAIL.HOSTNAME}},{p},smtp]") for c, p in
                       [("43A047", "25"), ("FB8C00", "587")]]},
]

# --- wykresy dashboardu: statusy poczty, rspamd, jakosc ocen
pf_colors = [("sent", "43A047"), ("delivered_local", "1E88E5"), ("deferred", "FB8C00"),
             ("bounced", "E53935"), ("expired", "8E24AA")]
act_colors = [("no_action", "43A047"), ("greylist", "9CCC65"), ("add_header", "FDD835"),
              ("rewrite_subject", "FB8C00"), ("soft_reject", "8E24AA"), ("reject", "E53935")]


def g(name, items_, gtype=None, **kw):
    gr = {"uuid": uid("graph", name), "name": name}
    if gtype:
        gr["type"] = gtype
    gr.update(kw)
    gr["graph_items"] = items_
    return gr


G_STATUS, G_STATUS_PIE = "Mailcow: Mail delivery status (per 10 min)", "Mailcow: Mail delivery status share"
G_INREJ = "Mailcow: Inbound rejected (per 10 min)"
G_ACT, G_ACT_PIE = "Mailcow: Rspamd actions (per 10 min)", "Mailcow: Rspamd actions share"
G_SCORE, G_BUCKETS = "Mailcow: Spam score quality", "Mailcow: Spam score distribution (per 10 min)"
G_SPAMPCT, G_BAYES = "Mailcow: Spam share and borderline scores", "Mailcow: Bayes learning (per 10 min)"
graphs += [
    g(G_STATUS, [gi(c, k_pfs[f]) for f, c in pf_colors], "STACKED"),
    # pie z AVG: wszystkie itemy maja ten sam interwal, wiec proporcje srednich = proporcje sum za okres
    g(G_STATUS_PIE, [gi(c, k_pfs[f]) for f, c in pf_colors], "PIE", height="300"),
    g(G_INREJ, [gi("FB8C00", k_pfs["rejected"]), gi("E53935", k_pfs["milter_rejected"]),
                gi("8E24AA", k_r)]),
    g(G_ACT, [gi(c, k_act[f]) for f, c in act_colors], "STACKED"),
    g(G_ACT_PIE, [gi(c, k_act[f]) for f, c in act_colors], "PIE", height="300"),
    g(G_SCORE, [gi("43A047", k_sc["avg_ham"], draw="BOLD_LINE"), gi("E53935", k_sc["avg_spam"], draw="BOLD_LINE"),
                gi("1E88E5", k_sc["p50"]), gi("FB8C00", k_sc["p95"], draw="DASHED_LINE")]),
    g(G_BUCKETS, [gi(c, k_bk[f]) for f, _, c in score_buckets], "STACKED"),
    g(G_SPAMPCT, [gi("E53935", k_sc["spam_pct"], draw="GRADIENT_LINE"), gi("FB8C00", k_sc["borderline_pct"])],
      ymin_type_1="FIXED", ymax_type_1="FIXED", yaxismax="100"),
    g(G_BAYES, [gi("E53935", k_lspam), gi("43A047", k_lham)]),
]

# ----------------------------------------------------------------- dashboard (format 6.0: siatka 24 kolumny)
cert_keys = [f"mailrep.certdays[{{$MAIL.HOSTNAME}},{p}]" for p in ("443", "993")] + \
            [f"mailrep.certdays[{{$MAIL.HOSTNAME}},{p},smtp]" for p in ("25", "587")]
svc_keys = [f"net.tcp.service[{s},{{$MAIL.HOSTNAME}},{p}]" for s, p in
            [("smtp", "25"), ("tcp", "465"), ("tcp", "587"), ("tcp", "993"), ("https", "443")]]


def w_graph(name, graph, x, y, wd, h):
    return {"type": "GRAPH_CLASSIC", "name": name, "x": str(x), "y": str(y), "width": str(wd), "height": str(h),
            "fields": [{"type": "INTEGER", "name": "source_type", "value": "0"},
                       {"type": "GRAPH", "name": "graphid", "value": {"name": graph, "host": T}}]}


def w_text(name, keys, x, y, wd, h):
    return {"type": "PLAIN_TEXT", "name": name, "x": str(x), "y": str(y), "width": str(wd), "height": str(h),
            "fields": [{"type": "ITEM", "name": "itemids", "value": {"host": T, "key": k}} for k in keys]
                      + [{"type": "INTEGER", "name": "show_lines", "value": "1"}]}


def w_proto(name, graph, x, y, wd, h, cols):
    return {"type": "GRAPH_PROTOTYPE", "name": name, "x": str(x), "y": str(y), "width": str(wd), "height": str(h),
            "fields": [{"type": "INTEGER", "name": "source_type", "value": "2"},
                       {"type": "GRAPH_PROTOTYPE", "name": "graphid", "value": {"name": graph, "host": T}},
                       {"type": "INTEGER", "name": "columns", "value": str(cols)},
                       {"type": "INTEGER", "name": "rows", "value": "1"}]}


dashboards = [{
    "uuid": uid("dashboard", "Mailcow"),
    "name": "Mailcow",
    "pages": [
        {"name": "Overview", "widgets": [
            w_text("Server state", [k_cd, k_cr, k_q, k_d, k_ba, k_pf_ok, k_rs_ok] + svc_keys + cert_keys,
                   0, 0, 8, 10),
            w_text("Last 24h", [k_24[f] for f in ("sent", "delivered_local", "deferred", "bounced", "expired",
                                                   "rejected", "milter_rejected", "scanned", "spam", "reject")],
                   8, 0, 8, 5),
            w_text("Spam scoring (last 10 min)", [k_sc[f] for f in ("n", "spam_pct", "avg_ham", "avg_spam",
                                                                    "borderline_pct")] + [k_lham, k_lspam],
                   8, 5, 8, 5),
            w_graph("Delivery status share", G_STATUS_PIE, 16, 0, 8, 5),
            w_graph("Rspamd actions share", G_ACT_PIE, 16, 5, 8, 5),
            w_graph("Delivery status", G_STATUS, 0, 10, 12, 5),
            w_graph("Rspamd actions", G_ACT, 12, 10, 12, 5),
            w_graph("Postfix queue", "Mailcow: Postfix queue", 0, 15, 12, 5),
            w_graph("Containers", "Mailcow: Containers", 12, 15, 12, 5),
        ]},
        {"name": "Mail flow", "widgets": [
            w_graph("Delivery status", G_STATUS, 0, 0, 12, 6),
            w_graph("Delivery status share", G_STATUS_PIE, 12, 0, 12, 6),
            w_graph("Outbound, bounces, reputation rejects", "Mailcow: Outbound mail flow (per 10 min)", 0, 6, 12, 6),
            w_graph("Inbound rejected", G_INREJ, 12, 6, 12, 6),
            w_graph("Postfix queue", "Mailcow: Postfix queue", 0, 12, 24, 5),
        ]},
        {"name": "Spam", "widgets": [
            w_graph("Rspamd actions", G_ACT, 0, 0, 12, 6),
            w_graph("Rspamd actions share", G_ACT_PIE, 12, 0, 12, 6),
            w_graph("Score quality: clean vs spam", G_SCORE, 0, 6, 12, 6),
            w_graph("Score distribution", G_BUCKETS, 12, 6, 12, 6),
            w_graph("Spam share and borderline scores", G_SPAMPCT, 0, 12, 12, 6),
            w_graph("Bayes learning (user corrections)", G_BAYES, 12, 12, 12, 6),
        ]},
        {"name": "Reputation & TLS", "widgets": [
            w_proto("RBL (IP) - critical", "RBL status: {$MAIL.IP} on {#RBL}", 0, 0, 24, 5, 4),
            w_proto("RBL (IP) - informational", "RBL status: {$MAIL.IP} on {#RBL} (informational)", 0, 5, 12, 5, 2),
            w_proto("RBL (domains)", "RBL status: {#DOMAIN} on {#RBL}", 12, 5, 12, 5, 2),
            w_graph("TLS certificates", "Mailcow: TLS certificates days left", 0, 10, 12, 5),
            w_graph("Backup age", "Mailcow: Backup age", 12, 10, 12, 5),
        ]},
    ],
}]

# ----------------------------------------------------------------- macros
macros = [
    ("{$MAIL.IP}", "", "Publiczny IPv4 serwera (wysylka). WYMAGANE."),
    ("{$MAIL.HOSTNAME}", "", "Nazwa hosta MAILCOW_HOSTNAME, np. mail.example.com (PTR, MX, TLS, porty). WYMAGANE."),
    ("{$MAIL.DOMAINS}", "", "Domeny obslugiwane przez serwer, po przecinku: example.com,example.de"),
    ("{$DNS.RESOLVER}", "1.1.1.1", "Resolver dla zapytan DNS/RBL. Spamhaus/URIBL odrzucaja publiczne resolvery - patrz README."),
    ("{$RBL.IP.ZONES}", "bl.spamcop.net,b.barracudacentral.org,psbl.surriel.com,bl.mailspike.net",
     "Listy IP o krytycznym znaczeniu (trigger HIGH). Dodaj KLUCZ.zen.dq.spamhaus.net po rejestracji DQS."),
    ("{$RBL.IP.ZONES.LOW}", "dnsbl-1.uceprotect.net", "Listy IP informacyjne (trigger WARNING)."),
    ("{$RBL.DOMAIN.ZONES}", "", "Listy domenowe, np. KLUCZ.dbl.dq.spamhaus.net. Puste = brak sprawdzen."),
    ("{$RBL.CHECK.INTERVAL}", "30m", "Czestotliwosc sprawdzen RBL (nie przesadzaj - limity zapytan list)."),
    ("{$MAIL.QUEUE.MAX}", "100", "Prog kolejki Postfixa."),
    ("{$MAIL.DEFERRED.MAX}", "50", "Prog wiadomosci odroczonych."),
    ("{$MAIL.SENT.MAX}", "500", "Max wiadomosci wychodzacych na 10 min (dostroic po tygodniu obserwacji)."),
    ("{$MAIL.BOUNCED.MAX}", "20", "Max odbic na 10 min."),
    ("{$MAILCOW.BACKUP.DIR}", "/opt/backup", "Katalog MAILCOW_BACKUP_LOCATION z katalogami mailcow-*."),
    ("{$MAILCOW.BACKUP.MAXAGE}", "26", "Maksymalny wiek ostatniego backupu w godzinach."),
    ("{$MAILCOW.DKIM.SELECTOR}", "dkim", "Selektor DKIM (mailcow domyslnie: dkim)."),
    ("{$CERT.EXPIRY.WARN}", "14", "Ostrzezenie o wygasaniu certyfikatu (dni)."),
    ("{$SNDS.KEY}", "", "Klucz Microsoft SNDS Automated Data Access (item SNDS domyslnie wylaczony)."),
]

template = {
    "uuid": uid("template", T),
    "template": T,
    "name": T,
    "description": (
        "Monitoring serwerow pocztowych mailcow-dockerized: kolejka i ruch Postfixa (statusy dostarczen), "
        "rspamd (akcje, oceny spamu, Bayes), kontenery, backup, "
        "reputacja (RBL, PTR/FCrDNS, Microsoft SNDS, odrzucenia reputacyjne), rekordy SPF/DKIM/DMARC/MX, "
        "certyfikaty TLS i dostepnosc portow.\n"
        "Wymaga skryptow mailrep.sh i mailcow_mail.sh, pliku UserParameter i wpisu sudoers - patrz README.md.\n"
        "Laczyc z szablonem 'Linux by Zabbix agent' (CPU/RAM/dysk)."),
    "groups": [{"name": GROUP}],
    "items": items,
    "discovery_rules": rules,
    "tags": tags(**{"class": "software", "target": "mailcow"}),
    "macros": [{"macro": m, "value": v, "description": d} for m, v, d in macros],
    "dashboards": dashboards,
    "valuemaps": [
        {"uuid": uid("vm", VM_RBL), "name": VM_RBL, "mappings": [
            {"value": "0", "newvalue": "Clean"}, {"value": "1", "newvalue": "LISTED"},
            {"value": "2", "newvalue": "Check error"}]},
        {"uuid": uid("vm", VM_BOOL), "name": VM_BOOL, "mappings": [
            {"value": "0", "newvalue": "No / Down"}, {"value": "1", "newvalue": "Yes / Up"}]},
    ],
}

for g in graphs:                                   # sortorder jak w eksportach Zabbixa
    for n, gi_ in enumerate(g["graph_items"]):
        if n:
            gi_["sortorder"] = str(n)

export = {"zabbix_export": {
    "version": "6.0",
    "date": "2026-11-21T12:00:00Z",
    "groups": [{"uuid": uid("group", GROUP), "name": GROUP}],
    "templates": [template],
    "graphs": graphs,
}}


class Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):   # listy wciete jak w eksporcie Zabbixa
        return super().increase_indent(flow, False)


sys.stdout.write(yaml.dump(export, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=1000))
