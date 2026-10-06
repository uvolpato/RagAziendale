"""IL BANCO DEI DOCUMENTI: lo stesso metro, sull'altra meta' dell'archivio.

    D=/tmp/val-$(date +%s)
    docker compose cp valutazione orchestratore:$D
    docker compose exec -T orchestratore sh -c "cd /app && python $D/documenti.py 3 > /var/tmp/doc.txt 2>&1"
    docker compose exec -T orchestratore cat /var/tmp/doc.txt

PERCHE' ESISTE. Fino al 5/10/2026 l'unica misura del sistema erano dieci
domande di catalogo. Sui documenti si decideva a impressioni — e il 5/10,
cercando di far scegliere al coordinatore la mossa `leggi`, mi sono accorto
che non sapevo nemmeno dire se `leggi` fosse DAVVERO meglio di `cerca` su
«quali modelli usiamo»: non avevo un metro, avevo un'aspettativa mia. Senza
questo file, qualunque lavoro sui testi e' taratura su aneddoti.

PERCHE' RIUSA `banco.py` INVECE DI COPIARLO. Il giudice, il campione, la
tabella degli esiti e la disciplina dei tre giri sono gia' scritti e
collaudati. Qui si cambiano le quattro cose che cambiano davvero: le
domande, chi legge le risposte, da quale tabella si pesca il campione, e una
colpa in piu'. `banco.py` resta il cancello dei cataloghi, intoccato.

LA COLPA IN PIU', ed e' la meta' «giudizio» del problema. L'archivio
contiene la STORIA: una nota del 18/09/2026 dice che l'integrazione di
LiteLLM e' «in fase analisi», e il 24/09 LiteLLM e' stato tolto. Una
risposta fedele al documento e sbagliata sul progetto e' il difetto piu'
pericoloso che abbiamo, perche' chi legge non ha modo di accorgersene. Qui
si misura: una cosa datata presentata come lo stato di oggi e' una colpa, e
dire «secondo una nota del 18/09» la annulla.
"""
import sys

sys.path.insert(0, "/app")

import banco  # noqa: E402

# Chi fa queste domande non e' un venditore: e' qualcuno che lavora al
# progetto o deve applicare una procedura. Non cerca un articolo, cerca
# quello che sta SCRITTO — e deve potersi fidare della data.
TESTA = """Giudichi il comportamento di un assistente aziendale che risponde su DOCUMENTI: progetti, procedure, manuali, relazioni, decisioni.

A che cosa serve quel sistema, perche' senza questo giudichi male: chi lo usa lavora dentro l'azienda. Chiede a che punto e' una cosa, cosa prevede una procedura, come e' andata una decisione. Non cerca un articolo da vendere: cerca quello che sta scritto, e deve poter risalire al documento per rileggerlo. Un riferimento [[n]] non e' un ornamento: e' l'unico modo che ha di verificare.

L'archivio contiene la STORIA del progetto, non solo il suo stato di oggi: documenti di settembre convivono con documenti di ottobre, e si contraddicono. Una risposta fedele al documento puo' essere sbagliata sul progetto."""

banco.P_GIUDICE = (
    TESTA + banco.P_GIUDICE.split("Gli serve trovare la cosa, sapere dov'e', "
                                  "e potersi fidare di quello che legge.")[1]
    + """

ANCORA SULLA PRIMA COLPA, perche' su questi documenti si sbaglia in un modo
preciso. Quando la risposta nega uno STATO — «non e' piu' usato», «e' stato
rimosso», «non si fa piu' cosi'» — un documento che quella cosa la NOMINA non
smentisce niente: l'archivio e' pieno di tracce di cose superate, ed e'
normale. Quella negazione e' falsa solo se un testo dice che la cosa E' ancora
cosi', e lo dice piu' tardi. Altrimenti il campo resta vuoto.
Il 6/10/2026 hai bocciato «LiteLLM non e' piu' utilizzato, e' stato rimosso e
sostituito da llama-swap» esibendo come prova un frammento di configurazione
`litellm_params` dentro una guida vecchia. La risposta era giusta.

3. SPACCIA PER ATTUALE UNA COSA DATATA. La risposta afferma uno STATO — «e' in analisi», «e' previsto», «si usa» — e la riga da cui viene porta una data, o parla di un momento preciso, e la risposta non lo dice. Copia quella riga.
   Basta che la risposta dica da quando vale — «secondo una nota del 18/09/2026...», «nel documento di settembre...» — perche' questa colpa non ci sia.
   Una risposta che non afferma nessuno stato non la commette. Una risposta che dice di non sapere non la commette.
   Non inventare la colpa dalla tua conoscenza del progetto: la prova e' la data che sta nella riga citata, non quello che sai tu.

4. RIMANDA LA DOMANDA INVECE DI RISPONDERE. La risposta chiede alla persona di precisare — «hai ulteriori dettagli su...?», «quale progetto intendi?» — e la risposta a quello che aveva chiesto sta nelle righe citate o nel campione. Copia la riga che la contiene.
   Il 6/10/2026, a «quali modelli usiamo e perche'?», il sistema ha risposto «hai ulteriori dettagli sui modelli specifici che vengono utilizzati?» con cinque righe citate sotto, e in archivio c'e' scritto quale modello e' quello di riferimento. Chiedere a chi ha chiesto non e' una risposta.
   Una domanda di chiarimento su una richiesta DAVVERO ambigua non e' questa colpa: se nelle righe e nel campione quella risposta non c'e', il campo resta vuoto.

Consegna con lo strumento `verdetto`.""")

# La terza colpa nel verdetto. I primi due campi restano identici: il
# giudice dei cataloghi e questo sono lo stesso agente con un capitolo in
# piu', e un campo in piu' non cambia l'ordine di quelli che c'erano.
_p = banco.I_VERDETTO[0]["function"]["parameters"]
_p["properties"]["datato"] = {
    "type": "boolean",
    "description": "true SOLO se la risposta afferma uno stato attuale e la "
                   "riga da cui viene lo data a un momento preciso, senza "
                   "che la risposta lo dica."}
_p["properties"]["datato_dove"] = {
    "type": "string", "description": "Le parole della risposta, o «»."}
_p["properties"]["datato_prova"] = {
    "type": "string", "description": "La riga citata che porta la data, "
                                     "copiata. «» se false."}
_p["properties"]["rimanda"] = {
    "type": "boolean",
    "description": "true SOLO se la risposta chiede alla persona di "
                   "precisare, e la risposta a quello che aveva chiesto sta "
                   "nelle righe citate o nel campione."}
_p["properties"]["rimanda_dove"] = {
    "type": "string", "description": "La domanda che ha rimandato, o «»."}
_p["properties"]["rimanda_prova"] = {
    "type": "string", "description": "La riga che contiene la risposta che "
                                     "avrebbe potuto dare, copiata. «» se "
                                     "false."}
_p["required"] += ["datato", "datato_dove", "datato_prova",
                   "rimanda", "rimanda_dove", "rimanda_prova"]

banco.COLPE = banco.COLPE + (("datato", "spaccia per attuale"),
                             ("rimanda", "rimanda invece di rispondere"))
banco.CAMPIONE_DA = "chunks"
banco.GRUPPI = [g.strip() for g in
                "Responsabile Sviluppo,sviluppo,azienda-luis".split(",")]

banco.BATTERIA = [
    {"nome": "stato", "domanda": "a che punto siamo col progetto?",
     "attesa": "Chiede lo STATO di un progetto. La risposta sta scritta nei "
               "documenti, e deve arrivare: che cosa e' fatto, che cosa e' in "
               "corso, con il riferimento [[n]] a dove l'ha letto. Una "
               "domanda di rimando («quale progetto?») e' un fallimento: in "
               "questo archivio il progetto c'e' ed e' uno. Rispondere a "
               "vuoto, senza citare niente, e' l'altro fallimento."},
    {"nome": "procedura",
     "domanda": "cosa dice la guida doganale sulle importazioni?",
     "attesa": "Chiede il CONTENUTO di un documento preciso. Deve riportare "
               "quello che il documento dice, con il riferimento. Non deve "
               "aggiungere obblighi, soglie o adempimenti che nel testo non "
               "ci sono: su una procedura, una frase inventata e' un danno "
               "vero."},
    {"nome": "modelli", "domanda": "quali modelli usiamo e perche'?",
     "attesa": "Sta scritto nei documenti del progetto. Deve nominare il "
               "modello di riferimento e dire perche', citando. Elencare "
               "modelli che nei documenti non ci sono e' un fallimento."},
    {"nome": "superato", "domanda": "usiamo ancora LiteLLM?",
     "attesa": "E' la domanda trappola dell'archivio. I documenti ne parlano "
               "in momenti diversi e si contraddicono: una nota di settembre "
               "lo da' in analisi, uno piu' recente lo da' tolto. La "
               "risposta giusta non e' «si'» ne' «no» detto secco: e' dire "
               "cosa dicono i documenti E DA QUANDO. Presentare la nota di "
               "settembre come lo stato di oggi e' il fallimento."},
    {"nome": "assente",
     "domanda": "cosa prevede la procedura sui rimborsi spese?",
     "attesa": "Di questo in archivio non c'e' niente. Deve dirlo. "
               "Inventare una procedura e' il fallimento grave; portare un "
               "documento che parla d'altro spacciandolo per quello "
               "chiesto e' lo stesso fallimento con un'altra faccia."},
    {"nome": "struttura", "domanda": "com'e' strutturato il progetto?",
     "attesa": "Domanda di sintesi su piu' documenti. Deve descrivere i "
               "pezzi del sistema e citare da dove viene quello che dice. "
               "Una descrizione senza nessun [[n]] e' inservibile: chi legge "
               "non puo' verificarla, ed e' tutto cio' per cui usa questo "
               "sistema."},
]

if __name__ == "__main__":
    banco.main()
