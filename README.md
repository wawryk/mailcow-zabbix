# Mailcow by Zabbix agent: szablon monitoringu

Szablon Zabbix do serwerów pocztowych opartych na **mailcow-dockerized**. Monitoruje reputację serwera, ruch pocztowy, kontenery, backup, rekordy DNS, certyfikaty TLS i dostępność usług. Jeden szablon obsługuje dowolną liczbę serwerów: wszystko, co zależy od konkretnego serwera, ustawia się makrami na hoście.

Format eksportu to 6.0. Szablon importuje się do Zabbix 6.0, 6.4 i 7.x; nowsze wersje same konwertują format. Działa z Zabbix agent 1 i agent 2.

## Zawartość paczki

- `template_mailcow_zabbix_agent.yaml`: szablon do importu.
- `scripts/mailrep.sh`: sprawdzenia RBL, DNS, PTR, FCrDNS i certyfikatów. Działa bez uprawnień root.
- `scripts/mailcow_mail.sh`: kolejka i log Postfixa, kontenery, backup. Uruchamiany przez sudo.
- `zabbix_agent/mailcow_reputation.conf`: definicje UserParameter dla agenta.
- `zabbix_agent/sudoers-zabbix-mailcow`: wpis sudoers ograniczony do jednego skryptu.
- `build_template.py`: generator szablonu. Zmiany wprowadzaj tutaj i generuj YAML od nowa.
- `tools/validate_template.py`: statyczna walidacja wygenerowanego YAML.

## Co jest monitorowane

### Reputacja

- **Listy RBL dla IP serwera, wykrywane przez LLD z makr.**
  - Listy krytyczne, wyzwalacz HIGH. Domyślnie: spamcop, Barracuda, PSBL, Mailspike, a po dodaniu klucza także Spamhaus DQS.
  - Listy informacyjne, wyzwalacz WARNING. Domyślnie: UCEPROTECT L1.
- **Listy RBL dla domen**, np. Spamhaus DBL. Każda domena z `{$MAIL.DOMAINS}` jest sprawdzana na każdej liście z `{$RBL.DOMAIN.ZONES}`.
- **Kody wyniku RBL:** 0 = czysto, 1 = **na liście**, 2 = błąd zapytania. Kod 2 przez 2 godziny włącza osobny trigger, żeby niedziałające sprawdzenie nie wyglądało jak „czysto”.
- **Rekord PTR (rDNS)** adresu IP musi wskazywać `{$MAIL.HOSTNAME}`.
- **FCrDNS:** rekord A hostname musi zawierać IP serwera.
- **Odrzucenia reputacyjne.** Skrypt szuka w logu Postfixa odrzuceń z serwerów docelowych:
  - Microsoft: S3140, S3150, SC-00x,
  - Gmail: 5.7.26, 4.7.0,
  - wzmianki o Spamhaus i blocklistach.
- **Microsoft SNDS:** lista IP zablokowanych przez Outlook i Hotmail. Item jest domyślnie wyłączony i wymaga klucza.

### Ruch pocztowy

- Wielkość kolejki Postfixa oraz liczba wiadomości odroczonych (deferred).
- Statusy dostarczeń z logu Postfixa w oknach 10-minutowych, z jednego odczytu logu (`postfix_stats`):
  - wysłane na zewnątrz (`sent`); gwałtowny wzrost zwykle oznacza **przejęte konto**,
  - dostarczone do skrzynek (dovecot), odroczone, odbite (bounced), wygasłe (expired),
  - odrzucone przychodzące: przez Postfixa/postscreen (`NOQUEUE: reject`) i przez rspamd (`milter-reject`).
- Sumy z ostatnich 24 h (itemy wyliczane `24h: ...`).

Liczone są linie logu, czyli odbiorcy: wiadomość do 5 adresatów to 5 dostarczeń.

### Spam (rspamd)

- Akcje rspamd na 10 minut: no action, greylist, add header, rewrite subject, soft reject, reject.
- Nauka Bayes (spam/ham). Mailcow uczy, gdy użytkownik przenosi wiadomość do Junk lub z Junk, więc wzrost nauki „ham” oznacza dobrą pocztę wpadającą do spamu (false positive), a „spam” — spam przechodzący przez filtr.
- Jakość ocen z historii rspamd (ostatnie 10 min):
  - średnia ocena poczty czystej i spamu (im większy odstęp, tym pewniejszy filtr),
  - mediana, 95. percentyl i maksimum,
  - udział spamu,
  - udział ocen „na granicy” (mniej niż 2 pkt od progu *add header*); duży udział oznacza niepewny filtr,
  - rozkład ocen w przedziałach < 0, 0–5, 5–10, 10–15, ≥ 15.

Dane rspamd skrypt czyta przez socket kontrolera `/var/lib/rspamd/rspamd.sock` w kontenerze `php-fpm-mailcow`, tak samo jak panel mailcow. Nie trzeba hasła ani dodatkowych pakietów na hoście. Historia rspamd w mailcow trzyma ok. 1000 ostatnich wiadomości, więc przy większym ruchu statystyki ocen obejmują tylko część okna; liczniki akcji są zawsze pełne.

### Infrastruktura mailcow

- Kontenery `*-mailcow-*` uruchomione oraz zatrzymane, restartujące się lub `unhealthy`.
- Wiek ostatniego backupu, czyli najnowszego katalogu `mailcow-*` w `{$MAILCOW.BACKUP.DIR}`.

### DNS domen (LLD z `{$MAIL.DOMAINS}`)

- SPF: musi zawierać `v=spf1`.
- DMARC: musi zawierać `v=DMARC1`.
- DKIM: `{$MAILCOW.DKIM.SELECTOR}._domainkey` musi zawierać `p=`.
- MX: musi wskazywać `{$MAIL.HOSTNAME}`.

### TLS i usługi

- Dni do wygaśnięcia certyfikatu na portach 443, 25 (STARTTLS), 587 (STARTTLS) i 993.
- Dostępność portów 25, 465, 587, 993 i 443. To simple checks wykonywane z serwera lub proxy Zabbix, czyli z perspektywy zewnętrznej.

### Dashboard „Mailcow”

Dashboard szablonu (na hoście: *Dashboards* w widoku hosta) ma cztery strony:

- **Overview:** stan serwera (kontenery, kolejka, backup, porty, certyfikaty, dostępność danych), sumy z 24 h, oceny spamu z ostatnich 10 min, udziały statusów i akcji rspamd, kolejka i kontenery.
- **Mail flow:** statusy dostarczeń (stos i udział), ruch wychodzący, odbicia, odrzucenia reputacyjne i przychodzące.
- **Spam:** akcje rspamd, jakość ocen (czysta poczta vs spam), rozkład ocen, udział spamu i ocen granicznych, nauka Bayes.
- **Reputation & TLS:** status list RBL, certyfikaty, wiek backupu.

Wykresy kołowe pokazują proporcje za wybrany okres (średnia z itemów o tym samym interwale).

### Wykresy

- Kolejka Postfixa.
- Ruch wychodzący, odbicia i odrzucenia reputacyjne.
- Kontenery.
- Wiek backupu.
- Certyfikaty.
- Status każdej listy RBL (prototypy wykresów).

Do monitorowania CPU, RAM i dysku podepnij dodatkowo standardowy szablon **Linux by Zabbix agent**. Ten szablon tych zasobów nie dubluje.

## Instalacja na serwerze mailcow

Wymagane pakiety: `dig` (`dnsutils` w Debianie/Ubuntu, `bind-utils` w RHEL), `openssl`, `sudo` i agent Zabbix.

```bash
# 1. Skrypty
install -m 755 scripts/mailrep.sh scripts/mailcow_mail.sh /usr/local/bin/

# 2. UserParameters (agent 2; dla agenta 1: /etc/zabbix/zabbix_agentd.d/)
cp zabbix_agent/mailcow_reputation.conf /etc/zabbix/zabbix_agent2.d/

# 3. sudoers: agent uruchamia jako root wyłącznie mailcow_mail.sh
install -m 440 zabbix_agent/sudoers-zabbix-mailcow /etc/sudoers.d/zabbix-mailcow
visudo -cf /etc/sudoers.d/zabbix-mailcow

# 4. Timeout agenta: sprawdzenia DNS i TLS trwają dłużej niż domyślne 3 s
#    w /etc/zabbix/zabbix_agent2.conf (lub zabbix_agentd.conf) ustaw:  Timeout=20
systemctl restart zabbix-agent2
```

Nie dodawaj użytkownika `zabbix` do grupy `docker`, bo daje to w praktyce pełne uprawnienia roota. Wpis sudoers ogranicza uprawnienia do jednego skryptu, który tylko odczytuje dane.

### Test lokalny

Uruchom poniższe komendy na serwerze Zabbix lub proxy (pakiet `zabbix-get`):

```bash
zabbix_get -s MAILCOW_IP -k 'mailrep.rbl[127.0.0.2,bl.spamcop.net,1.1.1.1]'      # 1  (adres testowy, zawsze na liście)
zabbix_get -s MAILCOW_IP -k 'mailrep.rbl[1.1.1.1,bl.spamcop.net,1.1.1.1]'        # 0
zabbix_get -s MAILCOW_IP -k 'mailrep.discovery[zones,"bl.spamcop.net,psbl.surriel.com"]'
zabbix_get -s MAILCOW_IP -k 'mailcow.mail[queue_total]'                           # liczba >= 0  (-1 = problem z sudo/dockerem)
zabbix_get -s MAILCOW_IP -k 'mailcow.mail[containers_down]'                       # 0
zabbix_get -s MAILCOW_IP -k 'mailcow.mail[backup_age,/opt/backup]'                # godziny
zabbix_get -s MAILCOW_IP -k 'mailcow.mail[postfix_stats,10]'                      # {"ok":1,"sent":...}
zabbix_get -s MAILCOW_IP -k 'mailcow.mail[rspamd_stats,10]'                       # {"ok":1,"scanned":...}
zabbix_get -s MAILCOW_IP -k 'mailrep.certdays[mail.example.com,25,smtp]'          # dni
```

## Aktualizacja istniejącego szablonu

1. **Najpierw** podmień skrypty na każdym serwerze mailcow (`install -m 755 scripts/*.sh /usr/local/bin/`). Itemy *outbound sent*, *bounced* i *reputation rejects* są teraz liczone z trybu `postfix_stats`; stary skrypt go nie zna. Plik UserParameter i sudoers się nie zmieniły.
2. Zaimportuj `template_mailcow_zabbix_agent.yaml` z zaznaczonym *Update existing* i *Create new* (w tym dla *Template dashboards*). *Delete missing* nie jest potrzebne: nic nie zostało usunięte.
3. Klucze i UUID dotychczasowych itemów, triggerów i wykresów są bez zmian, więc historia zostaje. Itemy sent/bounced/reputation zmieniają tylko typ na zależny.
4. Jeśli serwer ma stary skrypt, po ok. 30 min pojawi się trigger *cannot read Postfix log stats* / *cannot read rspamd stats*.

Aktualizację przetestowano na Zabbix 6.0.49 i 7.0.32: import starej wersji, potem nowej, potem nowej ponownie. Identyfikatory itemów się nie zmieniły.

## Import i podpięcie do hosta

1. **Data collection → Templates → Import** (w 6.0: Configuration → Templates → Import). Wybierz `template_mailcow_zabbix_agent.yaml`.
2. Na hoście z mailcow podepnij szablon **Mailcow by Zabbix agent**, a obok niego **Linux by Zabbix agent**. Host musi mieć interfejs agenta.
3. Na hoście w zakładce *Macros* ustaw co najmniej `{$MAIL.IP}`, `{$MAIL.HOSTNAME}` i `{$MAIL.DOMAINS}`.
4. Po około godzinie powstaną elementy z LLD. Możesz to przyspieszyć przyciskiem *Execute now* na regułach discovery.

Przy kolejnych serwerach mailcow powtarzasz tylko instalację na serwerze i makra na hoście.

## Makra

| Makro | Domyślnie | Opis |
|---|---|---|
| `{$MAIL.IP}` | – | **Wymagane.** Publiczny IPv4, z którego serwer wysyła pocztę. |
| `{$MAIL.HOSTNAME}` | – | **Wymagane.** `MAILCOW_HOSTNAME`, np. `mail.example.com`. Używany do sprawdzenia PTR, MX, TLS i portów. |
| `{$MAIL.DOMAINS}` | – | Domeny serwera po przecinku: `example.com,example.de`. |
| `{$DNS.RESOLVER}` | `1.1.1.1` | Resolver dla zapytań DNS i RBL. Patrz sekcja „Spamhaus i inne listy”. |
| `{$RBL.IP.ZONES}` | spamcop, barracuda, psbl, mailspike | Listy krytyczne (trigger HIGH). |
| `{$RBL.IP.ZONES.LOW}` | `dnsbl-1.uceprotect.net` | Listy informacyjne (trigger WARNING). |
| `{$RBL.DOMAIN.ZONES}` | puste | Listy domenowe, np. `KLUCZ.dbl.dq.spamhaus.net`. |
| `{$RBL.CHECK.INTERVAL}` | `30m` | Częstotliwość sprawdzeń RBL. Nie skracaj, bo listy mają limity zapytań. |
| `{$MAIL.QUEUE.MAX}` | `100` | Próg wielkości kolejki, liczony przez 15 minut. |
| `{$MAIL.DEFERRED.MAX}` | `50` | Próg wiadomości odroczonych, liczony przez 30 minut. |
| `{$MAIL.SENT.MAX}` | `500` | Maksimum wiadomości wychodzących na 10 minut. |
| `{$MAIL.BOUNCED.MAX}` | `20` | Maksimum odbić na 10 minut. |
| `{$MAILCOW.BACKUP.DIR}` | `/opt/backup` | Wartość `MAILCOW_BACKUP_LOCATION`. |
| `{$MAILCOW.BACKUP.MAXAGE}` | `26` | Maksymalny wiek ostatniego backupu w godzinach. |
| `{$MAILCOW.DKIM.SELECTOR}` | `dkim` | Selektor DKIM; w mailcow domyślnie `dkim`. |
| `{$CERT.EXPIRY.WARN}` | `14` | Liczba dni przed wygaśnięciem certyfikatu, od której pojawia się ostrzeżenie. |
| `{$SNDS.KEY}` | – | Klucz Microsoft SNDS. |

Progi `{$MAIL.SENT.MAX}`, `{$MAIL.QUEUE.MAX}` i `{$MAIL.BOUNCED.MAX}` ustaw dla każdego hosta po tygodniu obserwacji wykresów. Mały serwer firmowy i serwer z newsletterami mają zupełnie inny normalny ruch.

## Spamhaus i inne listy: ważne

- **Spamhaus odrzuca zapytania z publicznych resolverów** (1.1.1.1, 8.8.8.8, a także z DNS wielu providerów). Odpowiada wtedy `127.255.255.254`, a skrypt zwraca 2, czyli błąd zapytania, a nie „czysto”. Żeby sprawdzać Spamhaus:
  1. Załóż darmowe konto **Spamhaus DQS** (bezpłatne przy niskim wolumenie): https://www.spamhaus.com/free-trial/sign-up-for-a-free-data-query-service-account/
  2. Dopisz do `{$RBL.IP.ZONES}` strefę `TWOJKLUCZ.zen.dq.spamhaus.net`, a do `{$RBL.DOMAIN.ZONES}` strefę `TWOJKLUCZ.dbl.dq.spamhaus.net`.
- **URIBL** (`multi.uribl.com`) też odrzuca publiczne resolvery: odpowiada `127.0.0.1`, co skrypt traktuje jako błąd. Działa tylko przez własny resolver rekurencyjny.
- **Barracuda** (`b.barracudacentral.org`) wymaga bezpłatnej rejestracji adresu resolvera, z którego idą zapytania: https://www.barracudacentral.org/account/register
- **UCEPROTECT** bywa nadgorliwy, bo potrafi zablokować całą podsieć providera. Dlatego jest w listach informacyjnych.
- Własny resolver rekurencyjny (np. `unbound` na serwerze monitorującym) to alternatywa dla DQS. Wtedy w `{$DNS.RESOLVER}` wpisz jego adres.

Każdą listę można sprawdzić ręcznie adresem testowym `127.0.0.2`, który zawsze jest na liście:

```bash
mailrep.sh check 127.0.0.2 bl.spamcop.net 1.1.1.1      # 1 = lista działa przez ten resolver
```

## Microsoft SNDS (opcjonalnie)

1. Zarejestruj IP serwera na https://sendersupport.olc.protection.outlook.com/snds/ i potwierdź własność adresu, np. mailem na abuse@ lub postmaster@.
2. W SNDS włącz **Automated Data Access** i skopiuj klucz.
3. Ustaw makro `{$SNDS.KEY}` na hoście i **włącz** item *Microsoft SNDS: blocked IP status*. Jest domyślnie wyłączony, żeby bez klucza nie generował błędów.

Pusta odpowiedź oznacza brak blokad. Jeśli w odpowiedzi pojawi się adres IP, uruchomi się trigger HIGH.

Uzupełniająco, poza Zabbixem: **Google Postmaster Tools** (wymaga weryfikacji domeny, dane widać przy większym wolumenie) oraz raporty DMARC. Te ostatnie zbierasz, ustawiając `rua=mailto:dmarc@domena` i przeglądając je ręcznie albo przez parsedmarc.

## Rozwiązywanie problemów

- **„cannot read rspamd stats”**: sprawdź `sudo /usr/local/bin/mailcow_mail.sh rspamd_stats` i czy `docker compose exec php-fpm-mailcow ls -l /var/lib/rspamd/rspamd.sock` pokazuje socket.
- **„cannot read mail metrics”** albo wartości `-1` z `mailcow.mail[...]`:
  - sprawdź `sudo -u zabbix sudo -n /usr/local/bin/mailcow_mail.sh queue_total`,
  - brak sudoers albo `requiretty` daje błąd,
  - brak kontenera `postfix-mailcow` daje `-1`.
- **Timeouty i elementy *unsupported***: zwiększ `Timeout` agenta do 20–30 s. W Zabbix 7.x można zamiast tego ustawić timeout na pojedynczych itemach.
- **„cannot check … certificate”**: serwer łączy się sam ze sobą po nazwie publicznej. Przy NAT bez hairpinningu takie połączenie się nie uda. Wtedy dodaj wpis w `/etc/hosts` (hostname → lokalny IP) albo wyłącz te itemy.
- **Fałszywe alarmy SPF, DKIM lub MX**: domena może celowo mieć MX gdzie indziej, np. w okresie migracji. Wtedy usuń ją z `{$MAIL.DOMAINS}` albo wyłącz konkretny trigger na hoście.
- **`DNS_ERROR`** w itemach DNS oznacza, że resolver nie odpowiedział. Triggery SPF, DKIM, DMARC i MX celowo nie reagują na ten stan.
- **Licznik „outbound sent” wydaje się zawyżony**: skrypt liczy `status=sent` bez dostarczeń lokalnych (`relay=dovecot`). Jeśli na serwerze jest dodatkowy relay, dopisz go do wyjątków w `mailcow_mail.sh`.

## Zmiany w szablonie

Edytuj `build_template.py`, a potem wygeneruj i sprawdź szablon:

```bash
pip install pyyaml
python3 build_template.py > template_mailcow_zabbix_agent.yaml
python3 tools/validate_template.py template_mailcow_zabbix_agent.yaml
```

Identyfikatory UUID w szablonie są deterministyczne. Ponowny import aktualizuje istniejący szablon i nie tworzy duplikatów. Przy imporcie zaznacz *Update existing*, a jeśli chcesz usunąć elementy wycofane z szablonu, także *Delete missing*.

## Stan testów

- Skrypty przetestowano na Linuksie:
  - RBL na adresach testowych przez 1.1.1.1 (spamcop, Barracuda, PSBL, Mailspike i UCEPROTECT działają; Spamhaus i URIBL poprawnie zwracają błąd zapytania),
  - DNS, PTR i FCrDNS na rzeczywistych domenach,
  - certyfikaty na portach 443, 587 (STARTTLS) i 993,
  - zachowanie bez dostępu do Dockera.
- Szablon przeszedł walidację statyczną: składnia YAML, UUIDv4, unikalność kluczy, referencje w triggerach i wykresach, użycie makr.
- Import i aktualizacja przetestowane na Zabbix 6.0.49 i 7.0.32 (w 7.0 widgety *Plain text* są konwertowane na *Item history*). Itemy nie były zasilane danymi z żywego agenta.
- Tryb `rspamd_stats` przetestowano na rspamd 4.3 (socket kontrolera, `/stat`, `/history`) z PHP 8.2; ścieżki socketu i kontenera `php-fpm-mailcow` pochodzą z konfiguracji mailcow, nie z żywej instalacji.
- **Część `mailcow_mail.sh` wymagająca Dockera nie była uruchamiana na żywym mailcow.** Logikę parsowania (kolejka JSON, log Postfixa, statusy kontenerów, wiek backupu) przetestowano na atrapie polecenia `docker` z przykładowymi danymi. Po instalacji wykonaj testy `zabbix_get` z sekcji „Test lokalny”.
