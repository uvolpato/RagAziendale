# Realm Keycloak — note

JSON non ammette commenti, e **Keycloak rifiuta le chiavi non riconosciute**
(l'import fallisce con `Unrecognized field`). Quindi le spiegazioni stanno qui.

## Come viene applicato

`docker-compose.yml` monta questa cartella in `/opt/keycloak/data/import` e
avvia Keycloak con `start --import-realm`. L'import avviene **solo se il realm
non esiste**: rilanciare il container è innocuo.

I valori `$(env:VAR)` sono sostituiti da Keycloak con le variabili d'ambiente,
così il file resta committabile senza segreti dentro.

Per reimportare da zero durante lo sviluppo:

```bash
docker compose down -v            # cancella anche il DB
docker compose up -d postgres && py migrate.py && docker compose up -d keycloak
```

## Gruppi — i permessi sono un'intersezione, non una gerarchia

I gruppi sono l'**unica fonte di verità** sui permessi. Il filtro applicato alla
ricerca è `sources.acl_groups && groups_del_token`: un'intersezione di insiemi.

Non esiste un claim `access_level` numerico, ed è deliberato: un livello
numerico presuppone una gerarchia totale, e si rompe alla prima eccezione
organizzativa — il tipico "il magazzino deve vedere le schede tecniche ma non i
listini, e l'amministrazione il contrario". L'intersezione esprime quel caso
senza inventare un ordinamento che non c'è.

`tutti` è in `defaultGroups`: ogni nuovo utente lo riceve automaticamente.

## Client

| Client | Ruolo |
|---|---|
| `librechat` | Confidential, standard flow. L'unico che fa login |
| `orchestratore` | Bearer-only. Non fa login: esiste solo per essere l'**audience valida** dei token. Senza di lui il mapper di audience non ha bersaglio e l'orchestratore rifiuta ogni token |
| `test-token` | ⚠️ **Solo sviluppo.** Public client con password grant, per ottenere un token da riga di comando e testarne la forma |

### Il mapper di audience non è opzionale

`audience-orchestratore` aggiunge `orchestratore` al campo `aud` del token.
Senza, la firma del token risulta valida ma l'audience è sbagliata, e
l'orchestratore — che la verifica — rifiuta tutto. È il classico errore che
costa mezza giornata.

## Ottenere un token per i test

```bash
curl -s -X POST \
  http://localhost:8081/realms/azienda/protocol/openid-connect/token \
  -d grant_type=password -d client_id=test-token \
  -d username=prova.vendite -d "password=$TEST_USER_PASSWORD"
```

Il payload si ispeziona decodificando la parte centrale del JWT. Devono
comparire `groups` e `aud` con `orchestratore`.

## ⚠️ Prima della produzione

1. **Rimuovere i tre utenti `prova.*`** e il client **`test-token`**.
2. Gli utenti veri arrivano da LDAP/AD (user federation) o si creano in Keycloak.
3. Aggiornare `redirectUris` e `webOrigins` con l'hostname di produzione.
4. `sslRequired` è già `external`; verificare che valga anche per la rete interna.
