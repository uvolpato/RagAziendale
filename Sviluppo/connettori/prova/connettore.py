"""Connettore sintetico per i test (SPECIFICA-CONNETTORI.md §2.4, kit di conformita').

Non parla con nessun gestionale: i dati stanno in un dizionario in memoria
(`tabella -> lista di righe`) che i test riempiono. E' il connettore di
riferimento per provare il motore senza l'Integra vero, e l'esempio minimo di
cosa deve fare un connettore reale.

Ogni riga emette `id_origine` (l'id nel gestionale) e le colonne di business
del modello canonico; l'azienda nostra la aggiunge il motore (base.py).
"""
import json
import pathlib


class Connettore:
    def __init__(self, parametri=None):
        self.parametri = parametri or {}
        self.dati = {}          # tabella -> [righe]; i test lo riempiono

    # ---- catalogo ---------------------------------------------------------
    def manifesto(self):
        return json.loads((pathlib.Path(__file__).parent / "manifesto.json").read_text(encoding="utf-8"))

    def entita(self):
        return self.manifesto()["entita"]

    # ---- interfaccia comune (base.py) -------------------------------------
    def prova(self):
        return {"ok": True, "versione": "prova-v1", "aziende": ["001"]}

    def aziende(self):
        return [{"codice": "001", "ragione_sociale": "Prova S.r.l.", "partita_iva": "00000000000"}]

    def estrai(self, tabella, codice_azienda, dal=None):
        for r in self.dati.get(tabella, []):
            dmo = r.get("data_modifica_origine")
            if dal is not None and dmo and dmo <= dal:
                continue
            yield dict(r)

    def identificativi(self, tabella, codice_azienda):
        return {r["id_origine"] for r in self.dati.get(tabella, [])}

    def leggi_diretto(self, cosa, codice_azienda, chiave):
        raise NotImplementedError("il connettore di prova non ha letture dirette")

    def schema(self):
        return {"tipo": "prova", "versione": 1}
