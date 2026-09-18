# -*- coding: utf-8 -*-
"""Genera PDF esemplificativi del campione per la valutazione del retrieval.

Ripercorre la ripartizione di documenti_test/ESEMPI-FILE-DOCUMENTI.md §2.1.
I PDF sono FINTI E SEMPLIFICATI, con le casistiche da esercitare: servono a
far girare l'ingestion e lo script recall; vanno sostituiti con documenti veri
non sensibili (regola 0.5: i peggiori che ci sono).
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table,
                                TableStyle, Spacer, PageBreak)
from reportlab.pdfgen import canvas

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "campione")
os.makedirs(OUT, exist_ok=True)

GRIGIO = colors.HexColor("#e0e0e0")
STYLE_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14)
STYLE_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=20)
STYLE_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=16)
STYLE_SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=11)
STYLE_TBL = ParagraphStyle("tbl", fontName="Helvetica", fontSize=8, leading=10)


def cella(val):
    """reportlab 5 richiede flowable nelle celle: le stringhe vanno avvolte."""
    from reportlab.platypus import Paragraph as _P
    return val if isinstance(val, _P) else _P(str(val), STYLE_TBL)


def righe_tabella(n, col, pref_start=0):
    """Righe di dati plausibili per tabelle articoli/listini."""
    return [
        [str(pref_start + i + 1), f"ART-{1000 + pref_start + i}", col,
         f"{10 + i}.4{i}", f"{i * 7 + 12},00", f"{i * 3 + 5}%"]
        for i in range(n)
    ]


def pagina_semplice(nome, titolo, paragrafi, tabella=None, n_pagine=1):
    doc = SimpleDocTemplate(
        os.path.join(OUT, nome), pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm)
    storia = [Paragraph(titolo, STYLE_H1), Spacer(1, 8)]
    for p in paragrafi:
        storia.append(Paragraph(p, STYLE_BODY))
        storia.append(Spacer(1, 6))
    if tabella:
        tabella = [[cella(v) for v in riga] for riga in tabella]
        t = Table(tabella, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), GRIGIO),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f8f8")]),
        ]))
        storia.extend([Spacer(1, 8), t])
    for _ in range(n_pagine - 1):
        storia += [PageBreak(), Paragraph(titolo, STYLE_H1), Spacer(1, 8)]
    doc.build(storia)


def pagina_tabella_ruotata(nome, titolo, col):
    """Tabella ruotata di 90° (caso cataloghi/fornitori)."""
    from reportlab.lib.pagesizes import landscape
    w, h = landscape(A4)
    c = canvas.Canvas(os.path.join(OUT, nome), pagesize=(w, h))
    c.setTitle(titolo)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(2 * cm, h - 1.6 * cm, titolo)
    c.setFont("Helvetica", 9)
    # "scansione" storta: tutto il contenuto ruotato.
    c.saveState()
    c.translate(w * 0.55, h * 0.5)
    c.rotate(-3)
    c.translate(-w * 0.55, -h * 0.5)
    c.drawString(2 * cm, h - 2.6 * cm, "Riferimento catalogo: %s edition 2024/25" % col)
    righe = righe_tabella(16, col)
    y = h - 3.4 * cm
    x0, x1, x2, x3, x4, x5 = 1.6 * cm, 4.6 * cm, 9.6 * cm, 13.6 * cm, 16.6 * cm, 19.6 * cm
    for r in righe:
        c.drawString(x0, y, r[0]); c.drawString(x1, y, r[1])
        c.drawString(x2, y, r[2]); c.drawString(x3, y, r[3])
        c.drawString(x4, y, r[4]); c.drawString(x5, y, r[5])
        c.line(x0, y - 0.2 * cm, x5, y - 0.2 * cm)
        y -= 0.7 * cm
    c.restoreState()
    c.showPage()
    c.save()


def pagina_90gradi(nome, titolo, testi):
    """Testo ruotato di 90° dentro un rettangolo (caso peggiore)."""
    c = canvas.Canvas(os.path.join(OUT, nome), pagesize=A4)
    c.setTitle(titolo)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(2 * cm, A4[1] - 2 * cm, titolo)
    c.rect(2 * cm, A4[1] - 20 * cm, 16 * cm, 16 * cm, stroke=1, fill=0)
    c.saveState()
    c.rotate(90)
    c.setFont("Helvetica", 9)
    y = 3 * cm
    for t in testi:
        c.drawString(2.5 * cm, y, t)
        y += 0.7 * cm
    c.restoreState()
    c.showPage()
    c.save()


def pagina_immagini(nome, titolo, frasi):
    """Pagine con testo dentro immagini e colonne (simula scansione senza OCR)."""
    c = canvas.Canvas(os.path.join(OUT, nome), pagesize=A4)
    c.setTitle(titolo)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(2 * cm, A4[1] - 2 * cm, titolo)
    c.setFillColor(colors.HexColor("#ffffff"))
    c.rect(1.5 * cm, A4[1] - 8 * cm, 5 * cm, 4 * cm, stroke=0, fill=1)
    c.saveState()
    c.translate(2 * cm, A4[1] - 7.5 * cm)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.4 * cm, 3 * cm, "DICHIARAZIONE")
    c.setFont("Helvetica", 8)
    c.drawString(0.4 * cm, 2.4 * cm, frasi[0])
    c.drawString(0.4 * cm, 1.8 * cm, frasi[1])
    c.drawString(0.4 * cm, 1.2 * cm, frasi[2])
    c.restoreState()
    colonna_x = [6.5 * cm, 11.5 * cm]
    c.setFillColor(colors.HexColor("#222222"))
    c.setFont("Helvetica", 8)
    for k, x in enumerate(colonna_x):
        y = A4[1] - 4 * cm
        for f in frasi[k * 3:k * 3 + 3]:
            c.drawString(x, y, f[:45])
            y -= 0.5 * cm
    c.showPage()
    c.save()


def catalogo_scansione(nome, titolo, fonte):
    """Scansione: pagina con margini storti, note a mano, timbro."""
    c = canvas.Canvas(os.path.join(OUT, nome), pagesize=A4)
    c.setTitle(titolo)
    c.saveState()
    c.rotate(-2)
    c.translate(0.4, 0)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(2 * cm, A4[1] - 2 * cm, titolo)
    c.setFont("Helvetica", 9)
    for i in range(14):
        c.drawString(2 * cm, A4[1] - (3 + i * 1.1) * cm, f"{fonte} — voce {i+1:02d}: codice {1400+i} categoria {fonte.split()[0][:3]}")
    c.restoreState()
    c.setFillColor(colors.HexColor("#9e9e9e"))
    c.circle(15.5 * cm, 3 * cm, 1.5 * cm, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(15.5 * cm, 3 * cm, "TIMBRO")
    c.setStrokeColor(colors.grey)
    c.line(4 * cm, 1.2 * cm, 12 * cm, 1.6 * cm)
    c.showPage()
    c.save()


def lettera(nome, titolo, corpo):
    c = canvas.Canvas(os.path.join(OUT, nome), pagesize=A4)
    c.setTitle(titolo)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, A4[1] - 2 * cm, "Decobrands S.r.l. — Ufficio Commerciale")
    c.setFont("Helvetica", 9)
    c.drawString(2 * cm, A4[1] - 3.2 * cm, "Via delle Fabbriche 14 · 20861 Milano (MB) · info@decobrands.example")
    c.line(2 * cm, A4[1] - 3.6 * cm, A4[1] - 2 * cm, A4[1] - 3.6 * cm)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(2 * cm, A4[1] - 5.4 * cm, titolo)
    c.setFont("Helvetica", 10)
    y = A4[1] - 7.2 * cm
    for riga in corpo:
        c.drawString(2 * cm, y, riga)
        y -= 0.7 * cm
    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, y - 1 * cm, "Cordiali saluti,")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2 * cm, y - 2 * cm, "L. Bianchi")
    c.showPage()
    c.save()


# ---- 1. Manuali tecnici (6) — TOC, paragrafi, tabelle, note ----
pagina_semplice(
    "MAN-001-manuale-impianto-led.pdf", "Manuale tecnico — Impianto LED ART",
    ["1. Scopo", "2. Campo di applicazione", "3. Riferimenti normativi",
     "4.1 Generalità. Il modulo ART-0421 è progettato per 24/230V AC.",
     "4.2 Intervento tipico: sostituzione di un modulo elettronico ELD-90X.",
     "Tabella 4-1. Caratteristiche elettriche e meccaniche:",
     "Nota: i dati si intendono a temperatura ambiente di 25°C."],
    [["Parametro", "ART-0421", "TB-240"], ["Potenza", "18 W", "42 W"],
     ["Tensione", "24 V", "230 V"], ["Peso", "0,42 kg", "1,10 kg"],
     ["Flusso", "1.800 lm", "4.200 lm"]],
    n_pagine=2)
pagina_semplice(
    "MAN-002-manuale-controllo-remote.pdf", "Manuale utente — Unità di controllo ELD-90X",
    ["1. Introduzione", "2. Installazione.", "2.1 Collegamento alla rete.",
     "Attenzione: scollegare sempre la tensione prima di aprire il coperchio.",
     "3. Configurazione e messa in servizio.", "4. Manutenzione programmata."],
    [["Passo", "Azione", "Tempo"], ["1", "Scollegare tensione", "1 min"],
     ["2", "Aprire coperchio", "2 min"], ["3", "Estrarre moduli", "3 min"],
     ["4", "Ricontrollare i fissaggi", "2 min"]], n_pagine=2)
for i, arg in enumerate(["impianto-illuminazione", "gruppo-soccorso", "cabina-qe", "corridoio-tecnico"]):
    pagina_semplice(
        f"MAN-00{3+i}-istruzioni-{arg}.pdf", f"Istruzioni operative — {arg}",
        ["1. Scopo e ambito", "2. Termini e definizioni",
         "3. Procedura di verifica periodica.",
         "Ogni verificatore completa la checklist in appendice A e la firma.",
         "La checklist resta agli atti per 5 anni.",
         "4. Segnalazione anomalie: aprire una segnalazione."
         "5. Appendice A — checklist di verifica."],
        righe_tabella(8, "VER-" + str(1000 + i)), n_pagine=2)

# ---- 2. Procedure operative (6) ----
procedure = [
    ("PRO-001-procedura-reso.pdf", "Procedure operative — Gestione resi clienti",
     ["1. Oggetto", "2. Destinatari", "3.1 Ricezione della richiesta di reso.",
      "3.2 Verifica del numero merce a magazzino.", "3.3 Emissione della nota di credito.",
      "4.1 Firma del responsabile richiesta per resi oltre 30 giorni dall'ordine.",
      "5. Registrazione nel gestionale.",
      "Aggiornamento: chi opera in deroga compila il modulo in appendice."]),
    ("PRO-002-procedura-nonconformita.pdf", "Procedure — Gestione non conformità",
     ["1. Scopo: definire flusso per la non conformità in produzione.",
      "2. Identificazione e segregazione del materiale NC.",
      "3. Analisi delle cause con il metodo dei 5 perché.",
      "4. Azioni correttive e relative scadenze.",
      "5. Verifica di efficacia entro 30 giorni."]),
    ("PRO-003-procedura-acquisti.pdf", "Procedure — Acquisti e selezione fornitori",
     ["1. Oggetto", "2. Criteri di qualifica del fornitore.",
      "3. Limite di firma: doppia firma sopra i 10.000 Euro.",
      "4. Gestione delle eccezioni con motivazione scritta.",
      "5. Riesame annuale della qualifica."]),
    ("PRO-004-procedura-formazione.pdf", "Procedure — Formazione del personale",
     ["1. Oggetto", "2. Piano formativo annuale.",
      "3. Formazione obbligatoria e FAD.", "4. Verbalizzazione e firma."]),
    ("PRO-005-procedura-budget.pdf", "Procedure — Predisposizione del budget",
     ["1. Calendario del budget", "2. Raccolta dati dai gestionali.",
      "3. Consolidamento per azienda.", "4. Approvazione della direzione."]),
    ("PRO-006-procedura-archivio.pdf", "Procedure — Archiviazione documentale",
     ["1. Classificazione documenti", "2. Tempi di conservazione.",
      "3. Versamento e scarto.", "4. Accesso e riservatezza."]),
]
for nome, tit, par in procedure:
    pagina_semplice(nome, tit, par)

# ---- 3. Schede e listini prodotto (8) ----
listini = [
    ("LIS-001-listino-vendita-2026.pdf", "Listino di vendita PI/2026 — Luis S.r.l.",
     ["Listino prezzi 2026, valido fino a esaurimento o revisione.",
      "L'IVA non è inclusa. Sconti in cascata su ordini sopra i 200 pezzi.",
      "Tabella prezzi di vendita:"], "VENDITA"),
    ("LIS-002-listino-decobrands-2026.pdf", "Listino Decobrands 2026",
     ["Listino Decobrands 2026 — IVA esclusa.", "Nota: spedizioni franco magazzino.",
      "Nota: doccia come indicato su ciascuna riga."], "DECO"),
    ("LIS-003-listino-ricambi.pdf", "Listino ricambi — aggiornamento",
     ["Elenco ricambi per moduli ART, TB e ELD.", "Ordine minimo 10 pezzi."], "RIC"),
    ("LIS-004-listino-esterno-2023.pdf", "Listino 2023 (storico)",
     ["Listino storico 2023 conservato per raffronti.", "Prezzi 2023 non più applicabili."], "VENDITA"),
    ("SCH-001-scheda-tuboprofilo.pdf", "Scheda tecnica — Profilo tubolare TUBO-0209",
     ["Tolleranze dimensionali come da normativa.", "Zn poi verniciato.", "Peso 0,85 kg/m."],
     [["Caratteristica", "Valore"], ["Sezione", "20x09 mm"], ["Spessore", "1,5 mm"],
      ["Zinco", "130 g/m²"], ["Approvazioni", "EN 10346"]]),
    ("SCH-002-scheda-elettronica-90X.pdf", "Scheda tecnica — Elettronica ELD-90X",
     ["Centralina di interfaccia per gruppi di continuità.", "RS-485", "Grado IP20."],
     [["Caratteristica", "Valore"], ["Alimentazione", "24 V DC"], ["Interfaccia", "RS-485"],
      ["Porte", "1 ingresso / 2 uscite"], ["Temperatura", "-10..+55 °C"]]),
    ("SCH-003-scheda-laminato.pdf", "Scheda prodotto — Laminato LAM-0450",
     ["Formato 4.000 x 1.250 mm.", "Rivestimento poliestere ambedue facce.",
      "Certificazione biennale."], [["Caratteristica", "Valore"], ["Spessore", "0,8 mm"],
      ["Finitura", "RAL 9016"], ["Resa colore", "RL 3"]]),
    ("SCH-004-scheda-modulo-led.pdf", "Scheda prodotto — Modulo LED ART-0421",
     ["Descrizione: modulo illuminazione LED tecnico.",
      "Codici alternativi: 0421; EAN 8058470021421.", "Unità di misura: pz.",
      "Nota: minima 10 pz."],
     [["Caratteristica", "Valore"], ["Flusso", "1.800 lm"], ["Durata", "50.000 h"],
      ["CRI", "> 83"], ["Garanzia", "36 mesi"]]),
]
for nome, tit, par, tbl in listini:
    pagina_semplice(nome, tit, par, righe_tabella(12, tbl))

# ---- 4. Cataloghi fornitori (scansioni) (6) ----
for i, fonte in enumerate(["CATALOGO-FERRAMENTA-DUE", "CATALOGO-IMPIANTI-ELLISI",
                           "CATALOGO-ELETTROMECCANICA-BETA", "CATALOGO-ACCESSORI-GAMMA"]):
    catalogo_scansione(f"CAT-00{i+1}-catalogo-{fonte.split('-')[1].lower()}.pdf",
                       "Catalogo %s — Marca bianca" % fonte.split("-")[1], fonte)
for j, col in enumerate(["GHISA", "ACCIAIO"]):
    pagina_tabella_ruotata(f"CAT-00{5+j}-catalogo-{col.lower()}.pdf",
                           "Catalogo %s — tabella ruotata" % col, col)

# ---- 5. Documenti di vendita/acquisto (6) ----
documenti = [
    ("DOC-001-preventivo-2026-0143.pdf", "Preventivo PRE/2026/0143 — Ergon S.p.A.",
     ["Cliente: Ergon S.p.A. — cod. C-1042", "Agente: G. Ferraro",
      "Pagamento: D/P 60 gg d.f.f.m.", "Porto: franco destino",
      "Validità: 90 giorni", "Sconti di testata: 15% + 5%"]),
    ("DOC-002-ordine-2026-2210.pdf", "Ordine cliente ORD/2026/2210",
     ["Ordine ricevuto via portale B2B", "Data consegna prevista: 30/10/2026",
      "Fase attuale: evaso", "Consegna: franco magazzino"]),
    ("DOC-003-ddt-2026-3210.pdf", "DDT N. 3210 del 12/09/2026",
     ["Causale trasporto: vendita", "Porto: franco destino",
      "Vettore: trasporto internazionale DHL", "Colli dichiarati: 4"]),
    ("DOC-004-fattura-2025-8871.pdf", "Fattura FR/2025/8871",
     ["Fattura immediata n. 8871 del 30/11/2025", "Imponibile e IVA 22%",
      "Spese di trasporto addebitate", "Scadenza pagamento: 60 gg d.f.f.m."]),
    ("DOC-005-nota-credito-2026-231.pdf", "Nota di credito NC/2026/0231",
     ["Reso autorizzato dal responsabile", "Rif. DDT 3210", "IVA 22%"]),
    ("DOC-006-ordine-acquisto-2026-880.pdf", "Ordine di acquisto OA/2026/0880",
     ["Fornitore: Elettromeccanica Beta", "Doppia firma richiesta (importo > 10.000 €)",
      "Pagamento: D/P 90 gg f.d.m."]),
]
for nome, tit, par in documenti:
    pagina_semplice(nome, tit, par, righe_tabella(6, "DOC"))

# ---- 6. Lettere e comunicazioni (4) ----
lettera("LET-001-lettera-accompagnatoria.pdf", "Lettera di accompagnamento",
        ["Spett.le Ergon S.p.A.", "",
         "Vi inviamo in allegato il preventivo PRE/2026/0143 riferito alla",
         "vostra richiesta del 10/09/2026.", "",
         "Resta a disposizione il nostro ufficio commerciale per ogni chiarimento."])
lettera("LET-002-sollecito-pagamento.pdf", "Sollecito di pagamento",
        ["Spett.le Cliente,", "",
         "Con la presente segnaliamo la scadenza dell'importo di cui alla",
         "fattura FR/2025/8871, in essere da oltre 60 giorni.", "",
         "Vi preghiamo di provvedere al saldo entro 10 giorni."])
lettera("LET-003-conferma-ordine.pdf", "Conferma d'ordine ricevuto",
        ["Spett.le Cliente,", "",
         "confermiamo l'ordine ORD/2026/2210 ricevuto via portale B2B.",
         "Consegna prevista entro il 30/10/2026.", "",
         "Le condizioni applicate sono quelle del listino in vigore."])
lettera("LET-004-richiesta-offerta.pdf", "Richiesta di offerta",
        ["Spett.le Elettromeccanica Beta,", "",
         "chiediamo quotazione per il materiale di cui alla distinta in",
         "allegato, con consegna a novembre 2026.", "",
         "La presente è riferita alla procedura acquisti PRO-003."])

# ---- 7. Peggiori casi (4) ----
pagina_90gradi("PES-001-etichetta-verticale.pdf", "Etichetta tecnica (testo a 90°)",
               ["ART-0421", "EAN 8058470021421", "LOTTO 2026-09",
                "PESO 0,42 kg", "TEN 24/230V", "CRI 83", "T40", "IP20"])
pagina_90gradi("PES-002-prospetto-verticale.pdf", "Prospetto tecnico verticale",
               ["PROSPETTO 2026/09", "MODULI LED 18W", "DIRETTIVA EMC",
                "UNE EN 55015", "MARCAZIONE CE", "REV. B"])
pagina_immagini("PES-003-dichiarazione-foto.pdf", "Dichiarazione di conformità (immagine)",
                ["Il sottoscritto dichiara che il prodotto", "ART-0421 è conforme alla direttiva",
                 "2014/30/UE (EMC) e 2014/53/UE.", "", ""])
pagina_immagini("PES-004-verbale-foto.pdf", "Verbale di collaudo (immagine)",
                ["Il collaudo del lotto 2026-09 è avvenuto il", "15/09/2026 con esito positivo.",
                 "Firma del collaudatore presso la pagina seguente.", "", ""])

print("Generati", len(os.listdir(OUT)), "PDF in", OUT)