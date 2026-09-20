# Impostazioni della lettura dei documenti

Tutte le manopole che governano come un documento entra nell'indice: cosa
decidono, quanto valgono oggi, e **a quale livello** vivono. Serve a due cose:
capire perché un file è stato letto in un certo modo, e sapere cosa diventerà
configurabile **per fonte** quando si farà D16 (`DECISIONI-APERTE.md`).

I valori misurati vengono dalle prove del 20/09/2026 su cataloghi prodotto veri
(RTX 5060 Ti 16 GB); i tempi sono in `PRESTAZIONI.md`.

## Tre livelli, e non si scambiano

| Livello | Significato | Chi lo cambia |
|---|---|---|
| **macchina** | descrive l'hardware e i servizi installati, non i documenti | chi installa |
| **fonte** | dipende da *che tipo di documenti* contiene quella cartella | chi collega la fonte, dal pannello (D16) |
| **sistema** | regole uguali per tutti, perché cambiarle per fonte romperebbe qualcosa | solo con una decisione di progetto |

## 1. Dove e quando si legge

| Parametro | Cosa decide | Oggi | Livello |
|---|---|---|---|
| `CARTELLE` | radice delle cartelle montate | `/cartelle` | macchina |
| `DOCLING_URL` | vuoto = legge il servizio stesso; valorizzato = manda a docling-serve su un server GPU | vuoto | macchina |
| `INTERVALLO` | secondi fra un giro e l'altro **quando non c'è niente da fare**; se il giro ha lavorato riparte subito | 300 | sistema |
| `GPU` | `auto` usa la scheda se c'è posto, `off` legge sempre sul processore | auto | macchina |
| `VRAM_MINIMA_MB` | sotto questa VRAM libera si legge in CPU | 3500 (picco misurato di Docling: 2,1-2,7 GB) | macchina |
| `FINESTRA_NOTTE` | quando è ammesso scaricare il modello di chat per fare spazio | 22:00-06:00 | **fonte** |
| `MB_MAX_DI_GIORNO` | fuori finestra i file più grossi aspettano — **ma solo se la GPU è occupata**: con posto in VRAM si legge tutto, sempre | 5 | **fonte** |
| `LMSTUDIO_HOST` | chi tiene il modello di chat, per poterlo scaricare | `host.docker.internal:1234` | macchina |
| `PAGINE_PER_BLOCCO` | pagine convertite per ogni processo figlio | 6 (30 con docling-serve) | macchina (memoria) |
| `SECONDI_PER_BLOCCO` | oltre questo tempo il blocco si chiude | 600 | macchina |

## 2. Immagini

| Parametro | Cosa decide | Oggi | Livello |
|---|---|---|---|
| estrazione delle immagini | se le figure vengono conservate e salvate accanto ai documenti | **sempre attiva** (`generate_picture_images`) | **fonte** — manca l'interruttore |
| `images_scale` | risoluzione con cui la pagina viene disegnata prima di ritagliare le figure | 3.0 = 216 dpi | **fonte** — oggi nel codice |
| `MAX_LATO` | tetto sul lato lungo del PNG salvato | 1600 px | **fonte** — oggi nel codice |
| scala dell'immagine mandata al VLM | quanto grande la vede chi la descrive | 3.0 — **misurato: non costa tempo** (91 s contro 81) | **fonte** — oggi nel codice |
| `picture_area_threshold` | sotto questa frazione della pagina, la figura non si descrive | 0,15 — a 0,05 si descrivevano 324 immagini su 41 pagine, quasi tutte loghi e cornici | **fonte** — oggi nel codice |

Le immagini si scrivono **blocco per blocco** in `<cartella fonte>/_immagini/<hash del documento>/`.
Tenerle in memoria fino a fine file costava 3,3 GB dei 4 del container a metà
di un catalogo da 107 pagine.

## 3. Descrizione delle immagini

| Parametro | Cosa decide | Oggi | Livello |
|---|---|---|---|
| `VLM_DESCRIZIONI` | `api` = un modello visivo descrive le figure; `off` = si estraggono e basta | api | **fonte** |
| `VLM_URL` | dove sta quel modello | LM Studio sulla macchina | macchina |
| `VLM_MODELLO` | quale modello | `qwen/qwen3-vl-4b` | **fonte** |
| `PREZZI_DESCRIZIONI` | `escludi` scarta le descrizioni che contengono prezzi (decisione 72); `ammetti` li lascia entrare, per i cataloghi fornitore dove quel prezzo è l'unico che esiste | escludi | **fonte, con approvazione** |

Modelli provati per descrivere, il 20/09/2026:

| Modello | Esito |
|---|---|
| **Qwen3-VL 4B** (catalogo LM Studio) | **scelto**: trascrive le etichette e descrive colori e materiali. 120 s per 6 pagine, 3,3 GB di VRAM |
| glm-ocr | trascrive benissimo, **non descrive**: nessun colore nell'indice |
| mineru2.5 | descrive, ma con token da ripulire, qualche degenerazione, +47% di tempo |
| SmolVLM 256M (dentro l'immagine Docker) | «a few bottles». Rimosso: pesava 3,3 GB |
| granite-vision 3.3 2B, Qwen2.5-VL 3B (repository generici) | **inservibili** con LM Studio: rispondono file di virgole o di punti interrogativi anche a domande di solo testo |

Regola pratica che ne esce: **i modelli visivi si prendono dal catalogo di LM
Studio**, non da repository generici.

## 4. Testo, pezzi, vettori

| Parametro | Cosa decide | Oggi | Livello |
|---|---|---|---|
| lingue dell'OCR | Tesseract, su CPU | `ita`, `eng` | **fonte** — oggi nel codice |
| `do_table_structure` | riconoscimento della struttura delle tabelle | attivo | **fonte** — oggi nel codice |
| `MIN_PEZZO` / `MAX_PEZZO` | dimensione dei pezzi di testo | 300 / 1800 caratteri | **sistema**: pezzi di misura diversa fra fonti falsano il confronto dei punteggi |
| `LOTTO_VETTORI` | quanti testi per chiamata di embedding | 16 | sistema |
| modello di **embedding** | quale modello fa i vettori | `bge-m3` via LiteLLM | **sistema, immutabile**: cambiarlo impone di rileggere tutto (`index_meta`) |
| fogli di calcolo con prezzi | restano fuori, con il motivo (decisione 72) | attivo | **sistema**: è una regola su cosa entra nell'indice |

## 5. Risposta (orchestratore)

| Parametro | Cosa decide | Oggi | Livello |
|---|---|---|---|
| `limite` del recupero | quanti pezzi finiscono nel contesto | 8 | sistema |
| `MAX_IMMAGINI` | quante figure si citano per risposta | 4 | sistema |
| `IMMAGINI_SU_RICHIESTA` | `1` offre le immagini e le mostra a chi scrive «mostra»; `0` le allega sempre | 1 | sistema |
| `SUFFISSO_SISTEMA` | coda del prompt per le stranezze del modello: con Qwen3 serve `/no_think`, altrimenti la risposta arriva **vuota** | `/no_think` | macchina (dipende dal modello) |

## 6. Cosa manca perché diventino davvero «per fonte»

1. Le impostazioni delle immagini e dell'OCR **stanno nel codice**, non in
   variabili: vanno esposte prima di poterle mettere in una colonna `sources`.
2. Devono **viaggiare col file** fino al processo figlio che converte, non
   essere lette da variabili globali (D16, punto 7): altrimenti il giorno in cui
   due letture si sovrappongono un documento viene letto con i parametri di
   un'altra fonte, senza errori e senza che nessuno se ne accorga.
3. Serve l'interruttore per **non estrarre affatto le immagini**: oggi
   `VLM_DESCRIZIONI=off` spegne solo le descrizioni, mentre le figure vengono
   estratte e salvate comunque.
4. Il pannello deve mostrare i **valori efficaci** di ogni fonte, non solo
   quelli scelti.
