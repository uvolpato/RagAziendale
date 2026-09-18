-- Vista Integra -> giacenze (fotografia, MODELLO-DATI-GESTIONALE.md §6.2).
-- NON e' SCD2: il motore la scrive in erp.giacenze_istantanee con data = oggi.
-- Emette `articolo_id_origine` (il motore compone l'id canonico) e le quantita.
-- DA VERIFICARE sullo schema reale (maginv/maginvt).
SELECT
    m.pro_id::text                AS articolo_id_origine,
    m.magazzino::text             AS magazzino,
    m.esistenza                   AS esistenza,
    m.impegnato                   AS impegnato,
    m.ordinato                    AS ordinato,
    m.disponibile                 AS disponibile,
    m.valore                      AS valore
FROM maginv m
WHERE m.azi_cdazi = %(codice_azienda)s
