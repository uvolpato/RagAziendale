"""Porta su l'ambiente della fase 1, in ordine di dipendenza.

    py avvia.py            rende il realm, alza Postgres, migra, alza Keycloak
    py avvia.py --stato    mostra cosa c'e' e cosa manca

Idempotente: si puo' rilanciare.

Perche' esiste: il realm Keycloak va RESO da un template prima dell'avvio.
Keycloak 26 non sostituisce i $(env:VAR) nei file di import — i valori
finiscono nel realm come stringhe letterali, e il sintomo e' un
"Invalid user credentials" che non dice nulla. Dimenticare questo passo
costa mezza giornata, quindi non e' un passo di README: e' codice.
"""
import pathlib
import re
import socket
import string
import subprocess
import sys
import time

_orig_gai = socket.getaddrinfo


def _gai(host, porta, *a, **kw):
    # I browser risolvono *.localhost da soli (RFC 6761), il resolver di
    # Windows no: senza questo, pronto_http fallirebbe su un indirizzo che
    # nel browser funziona.
    if isinstance(host, str) and host.endswith(".localhost"):
        host = "127.0.0.1"
    return _orig_gai(host, porta, *a, **kw)


socket.getaddrinfo = _gai

QUI = pathlib.Path(__file__).parent
# I file resi da template: il realm (Keycloak non sostituisce le variabili
# nei file di import) e la configurazione LibreChat (dove vive tutta la
# personalizzazione cliente). Un solo meccanismo per entrambi.
TEMPLATE = QUI / "keycloak" / "realm-azienda.json.tmpl"
RESO = QUI / "keycloak" / "realm-azienda.json"
TEMPLATES = [
    (QUI / "keycloak" / "realm-azienda.json.tmpl", QUI / "keycloak" / "realm-azienda.json"),
    (QUI / "librechat.yaml.tmpl", QUI / "librechat.yaml"),
]


def leggi_env():
    f = QUI / ".env"
    if not f.exists():
        raise SystemExit("manca .env — copiarlo da .env.example e compilarlo")
    env = {}
    for riga in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", riga)
        if m:
            env[m.group(1)] = m.group(2).split("#")[0].strip()
    return env


def dc(*args, check=True):
    return subprocess.run(["docker", "compose", *args], cwd=QUI,
                          capture_output=True, text=True, check=check)


def rendi_template(env):
    """Rende i file da template. Le variabili facoltative (URL di disclaimer
    e privacy, che non tutti i clienti hanno) diventano stringa vuota invece
    di far fallire l'avvio."""
    FACOLTATIVE = {"URL_DISCLAIMER", "URL_PRIVACY"}
    for sorgente, destinazione in TEMPLATES:
        testo = sorgente.read_text(encoding="utf-8")
        servono = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)\}", testo)))
        mancanti = [k for k in servono
                    if not env.get(k) and k not in FACOLTATIVE]
        if mancanti:
            raise SystemExit(
                f"variabili vuote in .env, servono a {sorgente.name}: "
                + ", ".join(mancanti)
            )
        valori = {k: env.get(k, "") for k in servono}
        destinazione.write_text(
            string.Template(testo).substitute(valori), encoding="utf-8")
        print(f"  {destinazione.name} reso ({len(servono)} variabili)")


def scrivi_credenziali(env):
    """Genera il promemoria delle credenziali di sviluppo da .env.

    Rigenerato a ogni avvio: se i segreti cambiano o l'ambiente viene
    ricreato, il file resta vero. Un foglio scritto a mano invece invecchia
    e poi si perde tempo a capire quale password e' quella giusta.

    E' in .gitignore: contiene segreti.
    """
    f = QUI / "CREDENZIALI-SVILUPPO.md"
    app, sso = env.get("APP_HOST", ""), env.get("SSO_HOST", "")
    pw = env.get("TEST_USER_PASSWORD", "(non impostata)")

    f.write_text(f"""# Credenziali — SOLO SVILUPPO

> Generato da `avvia.py` leggendo `.env`. Non modificare a mano: si
> riscrive al prossimo avvio. In `.gitignore`.

## Chat

**https://{app}**

| Utente | Password | Gruppi | Cosa vede |
|---|---|---|---|
| `prova.magazzino` | `{pw}` | tutti, magazzino | solo i manuali |
| `prova.vendite` | `{pw}` | tutti, vendite | anche il listino riservato |
| `prova.direzione` | `{pw}` | tutti, vendite, amministrazione, direzione | tutto |

I gruppi non sono una gerarchia: il filtro e' un'intersezione di insiemi.
`prova.magazzino` non e' "livello 1", e' un insieme diverso.

### Amministratori di prova

| Utente | Password | Profilo | Cosa puo' fare |
|---|---|---|---|
| `prova.super` | `{pw}` | Superutente | tutto: aziende, struttura dei gruppi e dei profili, tutte le aziende |
| `prova.admin` | `{pw}` | Amministratore completo | tutto tranne la struttura; assegna i profili, non Superutente |
| `prova.accessi` | `{pw}` | profilo Gestione accessi, solo Luis | crea operatori Luis, gruppi e password; NON tocca gli amministratori |
| `prova.revisore` | `{pw}` | Revisore DPO | legge tutta l'amministrazione (aziende comprese), non modifica nulla |

**Amministrazione:** https://{app}/amministrazione/  (dati di esempio: `py amministrazione/esempio.py`)
Aziende, utenti, gruppi e profili si gestiscono dall'amministrazione (aziende: solo `prova.super`). Luis S.r.l. e Decobrands esistono solo come dati di esempio. Console di Keycloak (solo Superutente): https://{sso}/admin/{env.get("REALM", "azienda")}/console/
(si entra con un account del realm, non con `admin`).

## Console di amministrazione Keycloak — amministratore TECNICO

**https://{sso}/admin/** — realm `master`. Gestisce l'intero server Keycloak:
per utenti e gruppi usare gli amministratori di prova qui sopra.

| Utente | Password |
|---|---|
| `admin` | `{env.get("KEYCLOAK_ADMIN_PASSWORD", "(non impostata)")}` |

Il realm dell'applicazione e' `{env.get("REALM", "azienda")}`.

## Database (dall'host, client SQL)

```
host     localhost
porta    55432          <- non 5432 ne 5433: due cluster nativi le occupano
utente   postgres
password {env.get("POSTGRES_PASSWORD", "(non impostata)")}
database rag             (e `keycloak` per il realm)
```

## Modelli — LM Studio

```
{env.get("EMBEDDING_URL", "(non impostata)")}
modello embedding: {env.get("EMBEDDING_MODEL", "(non impostato)")}
```

## Certificato

Per togliere l'avviso del browser, da PowerShell **come amministratore**:

```
certutil -addstore -f "ROOT" "{QUI / "certs" / "caddy-root.crt"}"
```

Da rifare dopo un `docker compose down -v`: Caddy genera una CA nuova.

---

⚠️ **Prima della produzione:** rimuovere i tre utenti `prova.*` e il client
`test-token` dal realm (vedi `keycloak/README.md`). Gli utenti veri arrivano
da LDAP/AD.
""", encoding="utf-8")
    print(f"  credenziali in {f.name}")


def attendi_sano(servizio, secondi=180):
    nome = f"assistente-{servizio}-1"
    for _ in range(secondi // 2):
        r = subprocess.run(["docker", "inspect", "--format",
                            "{{.State.Health.Status}}", nome],
                           capture_output=True, text=True)
        if r.stdout.strip() == "healthy":
            return True
        time.sleep(2)
    return False


def keycloak_pronto(secondi=240):
    for _ in range(secondi // 3):
        log = dc("logs", "keycloak", check=False).stdout
        if "Failed to run import" in log:
            raise SystemExit("import del realm FALLITO — vedi: docker compose logs keycloak")
        # Da Keycloak 26.x "Listening on" arriva PRIMA della fine dell'avvio:
        # per altri ~15 s (migrazioni, import) risponde 503 e LibreChat, se
        # parte adesso, fallisce la discovery OIDC. Il segnale giusto e questo.
        if "Bootstrap completed" in log:
            return True
        time.sleep(3)
    return False


def estrai_ca():
    """Copia la CA interna di Caddy in certs/, perche' LibreChat possa fidarsi.

    Serve perche' openid-client (Node) accetta SOLO discovery OIDC su HTTPS:
    non si puo' puntare a http://keycloak:8080. Quindi LibreChat passa da
    Caddy, e deve fidarsi del suo certificato.

    La CA nasce al primo avvio di Caddy e vive nel volume: se il volume viene
    ricreato la CA cambia, quindi questo passo va rifatto. Per questo sta in
    avvia.py e non in un README.
    """
    certs = QUI / "certs"
    certs.mkdir(exist_ok=True)
    sorgente = "/data/caddy/pki/authorities/local/root.crt"
    for tentativo in range(20):
        r = subprocess.run(
            ["docker", "compose", "cp", f"caddy:{sorgente}",
             str(certs / "caddy-root.crt")],
            cwd=QUI, capture_output=True, text=True,
        )
        if r.returncode == 0 and (certs / "caddy-root.crt").stat().st_size > 100:
            print(f"  CA interna estratta ({(certs / 'caddy-root.crt').stat().st_size} byte)")
            return
        time.sleep(2)
    raise SystemExit("impossibile estrarre la CA di Caddy da /data/caddy/pki")


def oidc_configurato(secondi=90, da_offset=0):
    """LibreChat registra la strategia OIDC all'avvio: se fallisce, il login
    aziendale non esiste e il sintomo e' solo una riga di log.

    Si confrontano le ULTIME occorrenze, non le prime: il log del container
    conserva anche i tentativi precedenti a un riavvio, e cercare la stringa
    di fallimento in tutto il log darebbe un falso negativo dopo un riavvio
    andato a buon fine.
    """
    OK = "OpenID Connect configured successfully"
    KO = "OpenID Connect configuration failed"
    for _ in range(secondi // 3):
        # Solo la coda prodotta dopo `da_offset`: altrimenti il controllo
        # legge il tentativo PRECEDENTE al riavvio e risponde subito, sbagliando.
        log = dc("logs", "librechat", check=False).stdout[da_offset:]
        i_ok, i_ko = log.rfind(OK), log.rfind(KO)
        if i_ok >= 0 and i_ok > i_ko:
            return True
        if i_ko >= 0 and i_ko > i_ok:
            return False
        time.sleep(3)
    return False


def pronto_http(url, secondi=90):
    """Attende che il servizio risponda: dopo un riavvio serve qualche secondo,
    e senza questa attesa il primo test riceve un 502 da Caddy."""
    import ssl
    import urllib.error
    import urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for _ in range(secondi // 3):
        try:
            urllib.request.urlopen(url, timeout=5, context=ctx)
            return True
        except urllib.error.HTTPError:
            return True          # risponde: 4xx va benissimo
        except Exception:
            time.sleep(3)
    return False


def main():
    env = leggi_env()

    if "--stato" in sys.argv:
        print(dc("ps", check=False).stdout)
        print("realm reso:", "si" if RESO.exists() else "NO")
        return

    print("1/6 template")
    rendi_template(env)
    scrivi_credenziali(env)

    print("2/6 postgres")
    dc("up", "-d", "postgres")
    if not attendi_sano("postgres"):
        raise SystemExit("postgres non diventa healthy")
    print("  healthy")

    print("3/6 migrazioni")
    r = subprocess.run([sys.executable, str(QUI / "migrate.py")], cwd=QUI)
    if r.returncode:
        raise SystemExit("migrazioni fallite")

    print("4/6 keycloak")
    dc("up", "-d", "keycloak")
    if not keycloak_pronto():
        raise SystemExit("keycloak non risponde")
    print("  avviato, realm importato")

    print("5/6 caddy + CA interna")
    dc("up", "-d", "caddy")
    estrai_ca()
    # Deleghe di amministrazione (FGAP v2): idempotente, a ogni avvio, perche'
    # --import-realm non tocca un realm esistente. Vedi keycloak/deleghe.py.
    subprocess.run([sys.executable, str(QUI / "keycloak" / "deleghe.py")], check=True)

    print("6/6 mongo + librechat + amministrazione")
    dc("up", "-d", "mongo", "librechat")
    # --build: il codice dell'amministrazione e' nell'immagine, non montato.
    dc("up", "-d", "--build", "amministrazione")
    if not oidc_configurato():
        # Su Docker Desktop il bind mount di certs/ puo' non essere ancora
        # visibile dentro il container quando Node legge NODE_EXTRA_CA_CERTS:
        # il sintomo e' "Ignoring extra certs ... No such file or directory"
        # seguito da "fetch failed". Un riavvio basta, il file ora c'e'.
        print("  CA non ancora visibile nel container, riavvio librechat")
        offset = len(dc("logs", "librechat", check=False).stdout)
        dc("restart", "librechat")
        if not oidc_configurato(da_offset=offset):
            raise SystemExit(
                "LibreChat non ha configurato OIDC.\n"
                "  docker compose logs librechat | grep -i openid"
            )
    print("  OIDC configurato")

    if not pronto_http(f"https://{env['APP_HOST']}/"):
        raise SystemExit("LibreChat non risponde attraverso Caddy")
    print("  raggiungibile via HTTPS")

    print(f"\nPronto.  https://{env['APP_HOST']}")
    print("  utenti di prova: prova.magazzino / prova.vendite / prova.direzione")
    print("  password in .env -> TEST_USER_PASSWORD")
    print("\nVerifiche:  py eval/verifica_token.py")
    print("            py -m orchestratore.test_gate")


if __name__ == "__main__":
    main()
