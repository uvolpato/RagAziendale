-- Migrazione 026 — l'id del messaggio di LibreChat sulla traccia, per legare
-- il POLLICE a quello che il sistema ha davvero fatto in quel turno.
--
-- Il pollice su/giu' e' acceso in LibreChat da sempre (`feedback: true` in
-- librechat.yaml) e il voto finisce nel suo Mongo, su `messages.feedback`.
-- Noi abbiamo la colonna `traces.feedback` fin dall'inizio ed e' NULL su
-- tutte le righe: le due meta' non si sono mai parlate, perche' la traccia
-- conosceva solo `conversation_id` e in una conversazione i turni sono tanti.
-- Legarli per orario sarebbe approssimativo, ed e' il tipo di legame che
-- sbaglia proprio quando serve — su una conversazione lunga e concitata.
--
-- LibreChat sa iniettare negli header `{{LIBRECHAT_BODY_MESSAGEID}}`, cioe'
-- l'id del messaggio DELL'UTENTE che ha fatto partire il turno. La risposta
-- dell'assistente, in Mongo, ha quell'id come `parentMessageId`. Quindi il
-- legame e' esatto, non euristico:
--
--   SELECT t.domanda, t.strumenti, t.latenza_ms
--     FROM traces t
--    WHERE t.messaggio_id = <parentMessageId del messaggio votato>
--
-- A cosa serve davvero: un pollice giu' da solo e' un fatto binario e non
-- insegna niente. Legato alla traccia diventa «guarda che query ha scritto il
-- coordinatore, cosa ha confermato il critico, quanto ci ha messo» — cioe' il
-- modo in cui si e' lavorato tutto il 30/09 e l'1/10, ma a mano, cercando la
-- conversazione nel Mongo.
ALTER TABLE traces ADD COLUMN IF NOT EXISTS messaggio_id text;

-- Si cerca per id quando arriva un voto: una riga su centomila.
CREATE INDEX IF NOT EXISTS traces_messaggio_idx
    ON traces (messaggio_id) WHERE messaggio_id IS NOT NULL;
