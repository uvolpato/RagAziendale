# Regole del progetto (legate a questo repository)

## Sii sintetico nelle risposte

Nelle risposte all'utente, vai dritto al punto. Niente elenchi lunghi, niente
riepiloghi estesi, niente spiegazioni che l'utente non ha chiesto: una risposta
breve e precisa. Se serve dettaglio, l'utente lo chiede. Non anticipare
approfondimenti non richiesti.

## Ogni problema si affronta strutturalmente — mai con soluzioni posticce

Non esistono "soluzioni per il caso specifico": un sintomo va ricondotto alla
causa nel flusso, e si ripara la causa. Un cerotto (regola di prompt, parametro
tuned, caso speciale) sopprime il sintomo di OGGI e lascia intatto il difetto
di Domani, che ricompare col prossimo esempio dell'attributo di turno.

I problemi si classificano su una scala, e la risposta deve essere
proporzionata alla classe:

| Classe | Cosa significa | Cosa si fa |
|---|---|---|
| marginale | fastidio estetico/minore, non influisce sulla risposta | si può registrare, non si blocca nulla |
| basso | difetto visibile ma con workaround accettabile | si corregge localmente restando nell'architettura |
| medio | un comportamento sbaglia su casi reali, il workaround degrada il sistema | si interviene sul flusso, non sul sintomo |
| **fallimento progetto** | un comportamento sbaglia in modo sistematico e il sistema **risponde inventando / mettendo in errore l'utente** | va affrontato per intero: progetto, non rattoppo |

Un problema di classe «fallimento progetto» richiede la soluzione di progetto:
il difetto sta nell'architettura del flusso (recupero, modelli, dati), e lì va
riparato — anche se la soluzione tocca più componenti.

Criterio di verifica per distinguere posticcio da strutturale:
- la soluzione sparisce quando il caso specifico cambia (colore → misura →
  formato → prezzo)? Allora è un posticcio.
- la soluzione vale per l'intera CLASSE di casi, e il caso specifico è solo il
  primo che l'ha scoperta? Allora è strutturale.

## Milestone: il sistema funziona come funziona l'assistente

La bussola di ogni decisione di comportamento è una sola: guarda come funziona
tu, l'assistente, in questa conversazione, e fai funzionare il sistema allo
stesso modo.

Tu hai davanti l'INTERA conversazione e capisci il contesto: un «sì» dopo
un'offerta è un consenso, un «ciao» non è una ricerca, «mostrami il resto»
riprende ciò che avevi appena elencato. Non hai una catena di `if` per ogni
caso: leggi, capisci, decidi.

Il sistema deve fare lo stesso. L'agente riceve la conversazione intera (non
la sola domanda) e decide da sé — saluto, consenso, ricerca prodotti, lettura
di un testo — come faresti tu. La comprensione e la memoria stanno nell'agente,
non in `if` sparsi nel chiamante.

Regola pratica: davanti a un problema di comportamento, non chiederti «quale
caso speciale aggiungo?» ma «come lo farei io?».

## Il modello è una variabile, non un muro

Il modello non è un dato di fatto: è una scelta, e si cambia quando serve. C'è
una GPU con 128 GB di RAM condivisa (costa 5K, non milioni): ci gira un 70B Q4,
non un 36B Q2 come oggi. Se il limite è il modello, ci si collega a DeepSeek via
API e si testa.

La decisione deve essere CONSCIA, mai silenziosa. Davanti a un caso che il
sistema sbaglia, prima si separano le due cause:

- **difetto di codice** (parsing, filtri troppo stretti, un vincolo che scatta
  su parole generiche): si ripara in codice, il modello non c'entra.
- **limite di conoscenza** (un salto concettuale che il modello attuale non fa:
  «sfere trasparenti che trattengono l'acqua» → water beads): è il segnale che
  il modello è il collo di bottiglia, e lì si prova un 70B Q4 o DeepSeek per
  vedere se il salto lo fa.

«Il modello non lo sa» vuol dire sempre «il modello ATTUALE non lo sa, misurato
— vale la pena provarne uno più grande», mai una resa.