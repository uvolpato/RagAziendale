"""Regole dell'amministrazione, senza rete ne database: si testano da sole.

- permessi: la matrice ruoli x schermate della specifica §3.4, UNA volta sola.
  Il server la applica alle API; il client la riceve solo per nascondere le
  voci. Nascondere non e' proteggere.
- contrasto: WCAG 2.1 per la schermata Aspetto (§11), con correzione proposta.
- visibilita: cosa vede un utente (Vedi come, §8), con lo STESSO predicato del
  gate (orchestratore/recupero.py), non con quello che vorremmo.
"""
import re

# ------------------------------------------------------------------ permessi
# Valori: "M" modifica, "L" sola lettura, assente = voce di menu assente.
# "vedicome" M = puo' consultare (e' una lettura, ma viene registrata).
MATRICE = {
    "admin-accessi":      {"panoramica": "L", "fonti": "L", "vedicome": "M", "anomalie": "L", "registro": "L",
                           "utenti": "M", "gruppi": "M"},
    "admin-fonti":        {"panoramica": "L", "fonti": "M", "vedicome": "M", "anomalie": "L", "registro": "L",
                           "utenti": "L", "gruppi": "L"},
    "admin-importazioni": {"panoramica": "L", "fonti": "L", "anomalie": "L", "registro": "L", "dagster": "L",
                           "importazioni": "M"},
    "admin-anomalie":     {"panoramica": "L", "fonti": "L", "anomalie": "M", "registro": "L"},
    "admin-sistemi":      {"panoramica": "L", "anomalie": "L", "registro": "L", "aspetto": "M", "uptime": "L", "litellm": "L", "azioni": "M"},
    "admin-ruoli":        {"panoramica": "L", "vedicome": "M", "registro": "L",
                           "utenti": "L", "gruppi": "L", "profili": "M"},
    "admin-revisore":     {"panoramica": "L", "fonti": "L", "vedicome": "M", "anomalie": "L", "registro": "L",
                           "aspetto": "L", "utenti": "L", "gruppi": "L", "profili": "L", "aziende": "L",
                           "collegamenti": "L", "importazioni": "L", "dagster": "L", "uptime": "L", "litellm": "L"},
    # Superutente: tutto, compresa la "struttura" (creare gruppi, comporre i
    # profili) e la console di Keycloak per i casi rari. Vede tutte le aziende.
    "admin-super":        {v: "M" for v in ("panoramica", "fonti", "vedicome", "anomalie", "registro", "aspetto",
                                             "utenti", "gruppi", "profili", "struttura", "aziende",
                                             "collegamenti", "importazioni", "keycloak", "dagster", "uptime", "litellm")},
}
RUOLI = tuple(MATRICE)
NOMI_RUOLI = {
    "admin-accessi": "Gestione accessi", "admin-fonti": "Gestione fonti",
    "admin-importazioni": "Gestione importazioni", "admin-anomalie": "Gestione anomalie",
    "admin-sistemi": "Gestione sistemi", "admin-ruoli": "Gestione amministratori",
    "admin-revisore": "Revisore", "admin-super": "Superutente",
}
# Tipi di connettore ai gestionali (decisione 69): uno per azienda. Le
# credenziali stanno in .env, mai nel database ne' nel pannello.
CONNETTORI = {"integra": "Integra"}
CODICE_AZIENDA = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")   # come il CHECK della migrazione 005

# Strumenti che non conoscono le aziende (§3.3): solo a chi le ha tutte.
SENZA_AZIENDE = {"dagster", "uptime", "litellm", "azioni"}

# Anomalie "della propria area" (§3.4 nota 2). None = tutte.
AREE_ANOMALIE = {
    "admin-accessi": {"accessi"},
    "admin-fonti": {"documenti"},
    "admin-importazioni": {"importazioni", "qualita"},
    "admin-anomalie": None, "admin-sistemi": None, "admin-revisore": None, "admin-super": None,
}
# Registro "della propria area" (§3.4 nota 3). None = tutto.
AREE_REGISTRO = {
    "admin-accessi": {"vedi-come", "accessi"},
    "admin-fonti": {"fonti", "vedi-come"},
    "admin-importazioni": {"anomalie"},
    "admin-anomalie": {"anomalie"},
    "admin-sistemi": {"aspetto", "anomalie"},
    "admin-ruoli": None, "admin-revisore": None, "admin-super": None,
}


def permessi(ruoli, tutte_le_aziende):
    """Unione dei permessi dei ruoli: M vince su L. I ruoli si sommano (§3.1)."""
    out = {}
    for r in ruoli:
        for voce, livello in MATRICE.get(r, {}).items():
            if voce in SENZA_AZIENDE and not tutte_le_aziende:
                continue
            if out.get(voce) != "M":
                out[voce] = livello
    return out


def _unione_aree(ruoli, tabella):
    aree = set()
    for r in ruoli:
        if r not in tabella:
            continue
        if tabella[r] is None:
            return None
        aree |= tabella[r]
    return aree


def aree_anomalie(ruoli):
    """Sistemi di anomalie visibili; None = tutti; set() = nessuno."""
    return _unione_aree(ruoli, AREE_ANOMALIE)


def aree_registro(ruoli):
    return _unione_aree(ruoli, AREE_REGISTRO)


def aziende_da_gruppi(gruppi):
    """Gruppi Keycloak 'azienda-<codice>' -> codici azienda (decisione 53)."""
    return sorted({g[len("azienda-"):] for g in gruppi if g.startswith("azienda-") and len(g) > 8})


# ----------------------------------------------------------------- contrasto
ESADECIMALE = re.compile(r"^#[0-9a-fA-F]{6}$")
CONFIGURABILI = ("marchio", "marchio-secondario", "marchio-testo", "sfondo-pagina", "superficie")
PREDEFINITA = {"marchio": "#0d6efd", "marchio-secondario": "#475a6b", "marchio-testo": "#ffffff",
               "sfondo-pagina": "#f4f6f8", "superficie": "#ffffff"}
AA = 4.5


def _luminanza(esa):
    v = []
    for i in (1, 3, 5):
        c = int(esa[i:i + 2], 16) / 255
        v.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def contrasto(a, b):
    la, lb = _luminanza(a), _luminanza(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def correggi(sfondo, testo):
    """Il colore di sfondo piu' vicino all'originale che raggiunge AA col testo.

    Scurisce (o schiarisce, se il testo e' scuro) a piccoli passi: la tinta
    resta riconoscibile, e' il marchio del cliente."""
    verso_nero = _luminanza(testo) > 0.5
    r, g, b = (int(sfondo[i:i + 2], 16) for i in (1, 3, 5))
    for passo in range(101):
        f = passo / 100
        if verso_nero:
            c = (round(r * (1 - f)), round(g * (1 - f)), round(b * (1 - f)))
        else:
            c = (round(r + (255 - r) * f), round(g + (255 - g) * f), round(b + (255 - b) * f))
        esa = "#%02x%02x%02x" % c
        if contrasto(esa, testo) >= AA:
            return esa
    return "#000000" if verso_nero else "#ffffff"


def verifica_palette(p):
    """Errori bloccanti della palette (lista vuota = valida).

    Sotto AA non si salva: il prodotto si installa presso clienti diversi e
    un pulsante illeggibile e' un difetto per tutti (§2.1.4)."""
    errori = []
    for k in CONFIGURABILI:
        if not ESADECIMALE.match(str(p.get(k, ""))):
            errori.append(f"{k}: colore non valido, serve il formato #rrggbb")
    if errori:
        return errori
    if contrasto(p["marchio"], p["marchio-testo"]) < AA:
        errori.append("Il testo sul pulsante principale sara' poco leggibile "
                      f"(contrasto {contrasto(p['marchio'], p['marchio-testo']):.1f}:1, serve {AA}:1). "
                      f"Correzione proposta per il colore di marca: {correggi(p['marchio'], p['marchio-testo'])}")
    return errori


# ---------------------------------------------------------------- visibilita
def visibilita(fonte, gruppi, aziende_utente, attivo=True):
    """(visibile_per_l_assistente, motivo, avvisi) per Vedi come.

    STESSO predicato del gate (orchestratore/recupero.py): fonte attiva E
    gruppi che si intersecano E aziende che si intersecano. Se il gate cambia,
    cambia qui: Vedi come deve dire cio' che l'assistente fa davvero."""
    if not attivo:
        return False, "L'utente e' disattivato: non puo' accedere.", []
    if fonte["stato"] != "attiva":
        return False, f"La fonte e' {'sospesa' if fonte['stato'] == 'sospesa' else 'in attesa di approvazione'}.", []
    if not set(fonte["acl_groups"]) & set(gruppi):
        return False, "Non e' in nessuno dei gruppi: " + ", ".join(fonte["acl_groups"]) + ".", []
    if not set(fonte["aziende"]) & set(aziende_utente):
        return False, "Non e' abilitato a " + (", ".join(fonte["aziende"]) or "nessuna azienda della fonte") + ".", []
    return True, "", []
