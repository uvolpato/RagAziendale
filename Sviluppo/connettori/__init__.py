"""Connettori ai gestionali e motore di importazione (SPECIFICA-CONNETTORI.md).

Un connettore traduce le tabelle di un gestionale nelle colonne del modello
canonico (MODELLO-DATI-GESTIONALE.md). Il motore (`base.py`) fa tutto il
resto — storico SCD2, controlli, anomalie, stato — ed e' uguale per tutti:
un connettore nuovo e' piccolo e non puo' rompere le garanzie dello storico.
"""
