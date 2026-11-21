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


# ----------------------------------------------------------------- items
items = []

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

k_s = "mailcow.mail[sent,10]"
items.append(item(
    "Postfix: outbound sent (10 min)", k_s, "10m",
    "Wiadomosci wyslane na zewnatrz w ostatnich 10 min (bez lokalnych dostarczen do dovecot).",
    "mailflow", units="msg", triggers=[
        trig(f"last({ref(k_s)})>{{$MAIL.SENT.MAX}}",
             "Mailcow: unusually high outbound volume (>{$MAIL.SENT.MAX}/10 min)", "HIGH",
             "Mozliwe przejete konto rozsylajace spam - grozi wpisem na listy RBL. "
             "Sprawdz nadawcow w panelu mailcow (Logs > Postfix) i ewentualnie zablokuj konto.", scope="security"),
    ]))

k_b = "mailcow.mail[bounced,10]"
items.append(item(
    "Postfix: bounced (10 min)", k_b, "10m",
    "Wiadomosci trwale odrzucone (status=bounced) w ostatnich 10 min.",
    "mailflow", units="msg", triggers=[
        trig(f"last({ref(k_b)})>{{$MAIL.BOUNCED.MAX}}",
             "Mailcow: many bounces (>{$MAIL.BOUNCED.MAX}/10 min)", "WARNING",
             "Duza liczba odbic - sprawdz, czy nie dotycza jednego nadawcy (spam) lub jednej domeny docelowej.",
             scope="performance"),
    ]))

k_r = "mailcow.mail[rejects_reputation,10]"
items.append(item(
    "Postfix: reputation rejects (10 min)", k_r, "10m",
    "Odrzucenia przez serwery docelowe z powodow reputacji/list blokujacych "
    "(Microsoft S3140/S3150, Gmail 5.7.26/4.7.0, Spamhaus, blocklist...).",
    "reputation", units="msg", triggers=[
        trig(f"last({ref(k_r)})>0",
             "Mailcow: remote servers reject mail for reputation reasons", "AVERAGE",
             "Serwery docelowe odrzucaja poczte powolujac sie na reputacje/listy blokujace. "
             "Sprawdz log: docker compose logs --since 1h postfix-mailcow | grep -iE 'block|reputation|S3150|5.7.26'",
             scope="reputation"),
    ]))

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
        "Monitoring serwerow pocztowych mailcow-dockerized: kolejka i ruch Postfixa, kontenery, backup, "
        "reputacja (RBL, PTR/FCrDNS, Microsoft SNDS, odrzucenia reputacyjne), rekordy SPF/DKIM/DMARC/MX, "
        "certyfikaty TLS i dostepnosc portow.\n"
        "Wymaga skryptow mailrep.sh i mailcow_mail.sh, pliku UserParameter i wpisu sudoers - patrz README.md.\n"
        "Laczyc z szablonem 'Linux by Zabbix agent' (CPU/RAM/dysk)."),
    "groups": [{"name": GROUP}],
    "items": items,
    "discovery_rules": rules,
    "tags": tags(**{"class": "software", "target": "mailcow"}),
    "macros": [{"macro": m, "value": v, "description": d} for m, v, d in macros],
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
    "date": "2026-11-06T12:00:00Z",
    "groups": [{"uuid": uid("group", GROUP), "name": GROUP}],
    "templates": [template],
    "graphs": graphs,
}}


class Dumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):   # listy wciete jak w eksporcie Zabbixa
        return super().increase_indent(flow, False)


sys.stdout.write(yaml.dump(export, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=1000))
