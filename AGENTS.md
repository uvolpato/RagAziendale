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