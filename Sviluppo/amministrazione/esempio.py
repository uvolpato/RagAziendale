"""Dati di esempio per l'amministrazione — SOLO SVILUPPO.

    py amministrazione/esempio.py              # carica (idempotente)
    py amministrazione/esempio.py --rimuovi    # toglie tutto cio' che ha caricato

Dati fittizi coerenti con la specifica (§9.4, §13.5) e con il prototipo:
Luis S.r.l. e Decobrands, ~30 persone, 18 fonti, le anomalie di §9.4.
Tutto cio' che crea e' riconoscibile: fonti e anomalie con prefisso
'esempio-', utenti Keycloak con attributo origine=esempio ed email
@example.com (MAI domini reali: un'email di reimpostazione arriverebbe a
una persona vera). Da non eseguire in produzione.
"""
import json
import pathlib
import re
import socket
import sys

import httpx
import psycopg

QUI = pathlib.Path(__file__).resolve().parent.parent
E = {m.group(1): m.group(2).split("#")[0].strip()
     for m in re.finditer(r"^([A-Z_]+)=(.*)$", (QUI / ".env").read_text(encoding="utf-8"), re.M)}
_o = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, *a, **k: _o(
    "127.0.0.1" if isinstance(h, str) and h.endswith(".localhost") else h, p, *a, **k)

AZIENDE = [("luis", "Luis S.r.l.", "01234567890", "001"), ("decobrands", "Decobrands S.r.l.", "09876543210", "002")]
L, D, G = ["luis"], ["decobrands"], ["luis", "decobrands"]

# (nome, cognome, gruppi operativi, aziende, attivo)
PERSONE = [
    ("Mario", "Rossi", ["vendite"], L, 1), ("Paolo", "Verdi", ["amministrazione"], L, 1),
    ("Giulia", "Neri", ["vendite"], L, 1), ("Stefano", "Galli", ["acquisti"], D, 1),
    ("Elena", "Costa", ["amministrazione"], L, 1), ("Marco", "Fontana", ["vendite"], D, 1),
    ("Anna", "Greco", ["direzione"], D, 1), ("Davide", "Ricci", ["magazzino"], D, 1),
    ("Sara", "Marino", ["amministrazione"], D, 1), ("Luca", "Rinaldi", ["vendite"], L, 1),
    ("Chiara", "Moretti", ["direzione"], G, 1), ("Andrea", "Conti", ["acquisti"], D, 1),
    ("Francesca", "Leone", ["amministrazione"], L, 1), ("Tommaso", "Sala", ["vendite"], [], 1),
    ("Ilaria", "Fontana", [], L, 1), ("Roberto", "Fabbri", ["magazzino"], [], 1),
    ("Cecilia", "Riva", ["acquisti"], L, 1), ("Massimo", "Villa", [], [], 0),
    ("Federica", "Sala", ["vendite"], L, 1), ("Giorgio", "Barone", ["magazzino"], [], 1),
    ("Silvia", "Longo", ["vendite"], D, 1), ("Pietro", "Serra", ["direzione"], L, 1),
    ("Marta", "Caruso", ["amministrazione"], D, 1), ("Enrico", "Russo", ["vendite"], L, 1),
    ("Valentina", "Fiore", ["acquisti"], L, 1), ("Carlo", "Gentile", ["direzione"], D, 1),
    ("Paola", "Mancini", [], L, 1), ("Alberto", "Ferrari", ["acquisti"], [], 0),
]

# id, descrizione, tipo, provenienza, percorso, gruppi, aziende, residenza, owner, versione, stato
FONTI = [
    ("manuali-tecnici", "Manuali tecnici", "documenti", "cartella", r"\\server\tecnico\manuali", ["vendite", "direzione"], L, "interno", "Ufficio tecnico", "cartella corrente", "attiva"),
    ("cataloghi-fornitori", "Cataloghi fornitori", "documenti", "sharepoint", "SharePoint «Ufficio tecnico»", ["acquisti", "direzione"], G, "cloud_ok", "Ufficio tecnico", "catalogo 2026", "attiva"),
    ("listini-luis", "Listini di vendita — Luis", "gestionale", "integra", "erp.listini_righe", ["vendite"], L, "interno", "Vendite", "gestionale", "attiva"),
    ("listini-decobrands", "Listini di vendita — Decobrands", "gestionale", "integra", "erp.listini_righe", ["vendite"], D, "interno", "Vendite", "gestionale", "attiva"),
    ("procedure-qualita", "Procedure qualità", "documenti", "cartella", r"\\server\qualita\procedure", ["direzione", "amministrazione"], G, "interno", "Direzione", "revisione 12", "attiva"),
    ("schede-prodotto", "Schede prodotto", "documenti", "cartella", r"\\server\commerciale\schede", ["vendite", "acquisti"], G, "cloud_ok", "Vendite", "cartella corrente", "attiva"),
    ("clienti-luis", "Dati clienti — Luis", "gestionale", "integra", "erp.soggetti", ["vendite", "direzione"], L, "interno", "Vendite", "gestionale", "attiva"),
    ("clienti-decobrands", "Dati clienti — Decobrands", "gestionale", "integra", "erp.soggetti", ["vendite", "direzione"], D, "interno", "Vendite", "gestionale", "attiva"),
    ("ordini-luis", "Ordini — Luis", "gestionale", "integra", "erp.documenti", ["vendite"], L, "interno", "Vendite", "gestionale", "attiva"),
    ("vendite-luis", "Documenti di vendita — Luis", "gestionale", "integra", "erp.documenti", ["vendite", "amministrazione"], L, "interno", "Amministrazione", "gestionale", "attiva"),
    ("acquisti-luis", "Documenti di acquisto — Luis", "gestionale", "integra", "erp.documenti", ["acquisti"], L, "interno", "Acquisti", "gestionale", "attiva"),
    ("scadenze-luis", "Scadenze — Luis", "gestionale", "integra", "erp.scadenze", ["amministrazione"], L, "interno", "Amministrazione", "gestionale", "attiva"),
    ("giacenze-luis", "Giacenze — Luis", "gestionale", "integra", "erp.giacenze_istantanee", ["magazzino"], L, "interno", "Magazzino", "gestionale", "attiva"),
    ("verbali", "Verbali delle riunioni", "documenti", "caricamento", "caricamento manuale", ["direzione"], G, "interno", "Direzione", None, "attesa"),
    ("contratti-fornitori", "Contratti fornitori", "documenti", "sharepoint", "SharePoint «Amministrazione»", ["amministrazione", "direzione"], G, "cloud_ok", "Amministrazione", "contratti in vigore", "attiva"),
    ("manuali-storico", "Manuali prodotto storici", "documenti", "cartella", r"\\server\archivio\_storico", ["direzione"], L, "interno", "da-assegnare", None, "sospesa"),
    ("modulistica", "Modulistica interna", "documenti", "caricamento", "caricamento manuale", ["amministrazione"], G, "interno", "Amministrazione", "moduli 2026", "attiva"),
    ("comunicazioni", "Comunicazioni commerciali", "documenti", "cartella", r"\\server\commerciale\comunicazioni", ["vendite"], G, "cloud_ok", "Vendite", None, "attesa"),
]

# impronta, gravita, sistema, titolo, cosa_fare, azienda, oggetto, occorrenze, stato, assegnata
ANOMALIE = [
    ("modelli-giu", "critico", "sistemi", "Il servizio dei modelli di intelligenza artificiale non risponde",
     "Le domande in chat non ricevono risposta. Verifica la macchina dei modelli prima di riavviarla.", None, "servizio:modelli", 1, "presa", "prova.admin"),
    ("clienti-calo", "critico", "qualita", "Righe clienti calate del 62% nell'ultima importazione",
     "Di solito indica un problema del collegamento, non clienti davvero cancellati. Verifica il connettore prima di rileggere tutto.", "luis", "importazione:luis/soggetti", 1, "aperta", None),
    ("listini-ko", "errore", "importazioni", "Importazione listini fallita: gestionale non raggiungibile",
     "Il gestionale non risponde. Con il collegamento a posto la prossima importazione riparte da sola.", "luis", "importazione:luis/listini", 12, "aperta", None),
    ("pdf-illeggibili", "errore", "documenti", "3 PDF non leggibili nella fonte «Cataloghi fornitori»",
     "Probabilmente scansioni senza testo. Controlla i file nella cartella collegata.", "decobrands", "fonte:esempio-cataloghi-fornitori", 3, "aperta", None),
    ("piva-dup", "attenzione", "qualita", "14 clienti con partita IVA duplicata",
     "Controlla prima di inviare comunicazioni: potrebbero essere lo stesso cliente.", "luis", "importazione:luis/soggetti", 14, "presa", "prova.admin"),
    ("prezzi-zero", "attenzione", "qualita", "57 articoli con prezzo di listino a zero",
     "Spesso manca l'aggiornamento dal gestionale: non sono davvero gratis.", "decobrands", "importazione:decobrands/listini", 57, "aperta", None),
    ("colonna-nuova", "attenzione", "importazioni", "Nuova colonna nel gestionale non prevista: pro_liberon3",
     "Il gestionale ha aggiunto una colonna: va aggiunta alla mappatura se serve.", "luis", "importazione:luis/articoli", 1, "risolta_auto", None),
    ("login-falliti", "attenzione", "accessi", "23 accessi falliti in 10 minuti per l'utente mario.rossi",
     "Possibile password dimenticata o tentativo di accesso. Verifica l'utente in Gestione utenti.", None, None, 23, "aperta", None),
    ("senza-azienda", "attenzione", "accessi", "4 utenti senza azienda abilitata: non vedono nessun dato",
     "Persone attive senza aziende: probabilmente un errore di configurazione.", "luis", None, 4, "aperta", None),
    ("rifiuti-interno", "attenzione", "modelli", "5 domande rifiutate: servizio interno non disponibile per dati riservati",
     "Le domande su fonti «Resta in azienda» sono rifiutate se il servizio interno non risponde.", None, "servizio:modelli", 5, "aperta", None),
    ("modello-cambiato", "info", "modelli", "Il modello per la ricerca è cambiato: indice da ricostruire",
     "L'indice va ricostruito per restare coerente con il nuovo modello.", None, None, 1, "risolta", "prova.admin"),
]


def keycloak():
    c = httpx.Client(verify=False, timeout=30)
    t = c.post(f"https://{E['SSO_HOST']}/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli", "username": "admin",
        "password": E["KEYCLOAK_ADMIN_PASSWORD"]}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {t}"
    return c, f"https://{E['SSO_HOST']}/admin/realms/azienda"


def carica():
    with psycopg.connect(f"postgresql://postgres:{E['POSTGRES_PASSWORD']}@localhost:55432/rag") as db:
        for cod, rs, piva, orig in AZIENDE:
            db.execute("INSERT INTO aziende (codice, ragione_sociale, partita_iva, connettore, codice_origine)"
                       " VALUES (%s,%s,%s,'integra',%s) ON CONFLICT (codice) DO NOTHING", (cod, rs, piva, orig))
        for fid, desc, tipo, prov, perc, gr, az, res, owner, vers, stato in FONTI:
            db.execute("""INSERT INTO sources (id, descrizione, percorso, acl_groups, residency, owner, versione_autoritativa,
                          approvato_da, approvato_il, tipo, provenienza, stato, aziende)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                       ("esempio-" + fid, desc, perc, gr, res, owner, vers,
                        "Chiara Moretti" if res == "cloud_ok" else None, "2026-09-10" if res == "cloud_ok" else None,
                        tipo, prov, stato, az))
        for imp, grav, sist, tit, cosa, az, ogg, occ, stato, ass in ANOMALIE:
            imp = "esempio-" + imp
            if db.execute("SELECT 1 FROM anomalie WHERE impronta=%s", (imp,)).fetchone():
                continue
            for _ in range(occ):
                aid = db.execute("SELECT segnala_anomalia(%s,%s,%s,%s,%s,%s,%s,%s)",
                                 (imp, grav, sist, tit, cosa, az, ogg,
                                  json.dumps({"origine": "esempio", "connettore": az and f"integra_{az}"}))).fetchone()[0]
            if stato == "risolta_auto":
                db.execute("SELECT chiudi_anomalia(%s)", (imp,))
            elif stato != "aperta":
                db.execute("UPDATE anomalie SET stato=%s, assegnata_a=%s WHERE id=%s", (stato, ass, aid))
                db.execute("INSERT INTO anomalie_eventi (anomalia_id, chi, tipo) VALUES (%s,%s,%s)",
                           (aid, ass or "sistema", "presa" if stato == "presa" else "risolta"))
        # NIENTE erp.sincronizzazioni di esempio: la griglia Importazioni deve
        # restare vuota ("Mai importata") finche' non gira un'importazione vera.
        # Inserire qui righe finte farebbe sembrare attiva un'importazione che
        # non c'e'.
        if not db.execute("SELECT 1 FROM registro_modifiche WHERE chi = 'esempio'").fetchone():
            for q, nome, area, azione, ogg, az, mot in [
                ("2026-09-16 18:12", "Elena Costa", "vedi-come", "Ha consultato le fonti visibili da Chiara Moretti", "Chiara Moretti", None, None),
                ("2026-09-17 09:30", "Sara Marino", "fonti", "Ha sospeso la fonte «Manuali prodotto storici»", "Manuali prodotto storici", "luis", "Cartella in archiviazione: contenuti fuori produzione."),
                ("2026-09-17 11:05", "Chiara Moretti", "aspetto", "Ha aggiornato la palette", None, None, None),
                ("2026-09-17 17:40", "Paolo Verdi", "anomalie", "Ha risolto: 14 clienti con partita IVA duplicata", "14 clienti con partita IVA duplicata", "luis", "Corretti i duplicati nel gestionale."),
                ("2026-09-18 10:42", "Chiara Moretti", "fonti", "Ha reso la fonte «Cataloghi fornitori» utilizzabile da servizi esterni", "Cataloghi fornitori", "luis,decobrands", "Accordo con il fornitore: i cataloghi sono pubblici."),
            ]:
                db.execute("INSERT INTO registro_modifiche (quando, chi, chi_nome, area, azione, oggetto, azienda, motivo, prima, dopo)"
                           " VALUES (%s,'esempio',%s,%s,%s,%s,%s,%s,%s,%s)",
                           (q + "+02", nome, area, azione, ogg, az, mot,
                            json.dumps({"residenza": "interno"}) if "servizi esterni" in azione else None,
                            json.dumps({"residenza": "cloud_ok"}) if "servizi esterni" in azione else None))
    print(f"  database: {len(AZIENDE)} aziende, {len(FONTI)} fonti, {len(ANOMALIE)} anomalie, registro, importazioni")

    c, A = keycloak()
    gruppi = {g["name"]: g["id"] for g in c.get(f"{A}/groups", params={"max": 500, "briefRepresentation": "true"}).json()}
    nuovi = 0
    for nome, cognome, gr, az, attivo in PERSONE:
        user = f"{nome}.{cognome}".lower()
        if c.get(f"{A}/users", params={"username": user, "exact": "true"}).json():
            continue
        r = c.post(f"{A}/users", json={"username": user, "firstName": nome, "lastName": cognome,
                                        "email": f"{user}@example.com", "emailVerified": True, "enabled": bool(attivo),
                                        "attributes": {"origine": ["esempio"]}})
        r.raise_for_status()
        uid = c.get(f"{A}/users", params={"username": user, "exact": "true"}).json()[0]["id"]
        for g in ["tutti"] + gr + [f"azienda-{a}" for a in az]:
            c.put(f"{A}/users/{uid}/groups/{gruppi[g]}").raise_for_status()
        nuovi += 1
    print(f"  keycloak: {nuovi} persone nuove (senza password: servono solo a Vedi come)")


def rimuovi():
    with psycopg.connect(f"postgresql://postgres:{E['POSTGRES_PASSWORD']}@localhost:55432/rag") as db:
        db.execute("DELETE FROM anomalie WHERE impronta LIKE 'esempio-%'")
        db.execute("DELETE FROM sources WHERE id LIKE 'esempio-%'")
        db.execute("DELETE FROM erp.sincronizzazioni WHERE righe_lette = 1000")
        # Il registro NON si cancella, nemmeno qui: e' la sua garanzia (trigger).
    c, A = keycloak()
    for u in c.get(f"{A}/users", params={"q": "origine:esempio", "max": 500}).json():
        c.delete(f"{A}/users/{u['id']}")
    print("  dati di esempio rimossi (il registro modifiche resta: non si cancella)")


if __name__ == "__main__":
    rimuovi() if "--rimuovi" in sys.argv else carica()
