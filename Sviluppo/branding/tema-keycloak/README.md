# Pagina di accesso SSO — brief per il designer

La pagina di login **non appartiene alla chat**: è servita da Keycloak, che
gestisce l'autenticazione. Si personalizza con un tema, e questa cartella è
quel tema.

L'obiettivo è che l'utente non percepisca due applicazioni diverse.

## Cosa si può modificare

| File | Contenuto |
|---|---|
| `login/resources/css/tema-azienda.css` | **Tutto l'aspetto.** È l'unico file di stile da toccare |
| ~~`login/resources/img/logo.png`~~ | **Non toccare**: è solo il punto di innesto. Il logo vero è `branding/logo.png`, montato qui dal compose |
| ~~`login/resources/img/favicon.ico`~~ | Idem: l'icona vera è `branding/favicon.ico` |
| `login/messages/messages_it.properties` | I testi italiani |
| `login/messages/messages_en.properties` | I testi inglesi |
| `login/messages/messages_de.properties` | I testi tedeschi |

## Cosa NON toccare

- `login/theme.properties` — dichiara l'ereditarietà da Keycloak e le lingue.
  Modificarlo può lasciare la pagina senza stile.
- `login/login.ftl` — **esiste e va toccato con cautela.** È una copia del
  template base con sole aggiunte di presentazione (logo, titolo,
  sottotitolo, nota). Nomi dei campi, ordine, tabindex e gestione errori
  sono quelli di Keycloak: cambiarli rompe autofill, navigazione a tab e
  messaggi d'errore. ⚠️ **Va riallineato a ogni aggiornamento maggiore di
  Keycloak**, confrontandolo con il nuovo `theme/base/login/login.ftl`:
  è il costo di avere una struttura propria, ed è il motivo per cui nessun
  altro template è stato copiato.
- `template.ftl` NON è stato copiato: l'impaginazione esterna resta di
  Keycloak, e la manutenzione è metà.

## I token grafici

Non sono stati inventati: sono la **palette aziendale Decobrands**, letta da
`Decobrands_Marketing/assets/css/tokens.css` (fonte canonica
`docs/STILE-GUIDA-DECOBRANDS.md` sez. 2), ridichiarata in cima a
`tema-azienda.css` con prefisso `--db-*` per essere autonoma a runtime.

| Ruolo | Chiaro | Scuro | Token della guida |
|---|---|---|---|
| Sfondo pagina | `#f5f6f8` | `#212529` | bg |
| Superficie scheda | `#fff` | `#2b3035` | `--db-dark` (dark) |
| Testo | `#212529` | `#dee2e6` | dark |
| Testo tenue | `#6c757d` | `#adb5bd` | secondary / muted-dim |
| Bordo | `#dee2e6` | `#495057` | border |
| Azione (pulsante) | `#0d6efd` | idem | primary |
| Azione hover | `#0b5ed7` | idem | hover btn-primary Bootstrap 5 |
| Errore | `#dc3545` | idem | danger |
| Successo | `#198754` | idem | success |
| Raggio angoli | `.5rem` | idem | — |
| Carattere | **Inter**, sans-serif | idem | |

Chiaro e scuro seguono `prefers-color-scheme`. I **ruoli scuri sono quelli
ufficiali della guida Decobrands** (`STILE-GUIDA` sez. 2.5 /
`palette-colori-decobrands.md` sez. 8), qui mirati sul login — vedi
`paletta-colori.md` accanto al CSS. Se la palette cambia, si riestraggono i
`--db-*` da `tokens.css`.

## Il DOM su cui agganciarsi

Keycloak genera questa struttura. Sono i selettori che servono: senza,
si procede a tentativi.

| Selettore | Elemento |
|---|---|
| `body` | Sfondo della pagina |
| `.login-pf-page .card-pf` | La scheda che contiene il form |
| `#kc-header-wrapper` | Intestazione — qui è sostituita dal logo via `background-image` |
| `#kc-page-title` | Titolo della pagina |
| `#kc-form-login` | Il form |
| `.pf-c-form-control`, `input[type=text]`, `input[type=password]` | I campi |
| `.pf-c-form__label-text`, `label` | Le etichette |
| `#kc-login`, `input[type=submit].pf-c-button`, `.pf-c-button.pf-m-primary` | Il pulsante di accesso |
| `#kc-info-wrapper` | Nota sotto il form |
| `.pf-c-alert.pf-m-danger` | Messaggio di errore |
| `#kc-locale-dropdown`, `#kc-locale` | Selettore di lingua |

Keycloak usa **PatternFly**: molte regole hanno bisogno di `!important` per
vincere sul foglio base. Nel CSS attuale è già così dove serve.

## Come vedere le modifiche

La cache dei temi è **disattivata in sviluppo**
(`KC_SPI_THEME_CACHE_THEMES=false` in `docker-compose.override.yml`): basta
salvare il file e ricaricare la pagina. Nessun riavvio.

Per aprire la pagina di login senza passare dalla chat:

```
https://assistente.localhost/oauth/openid
```

Per vederla in inglese, cambiare la lingua del browser, oppure aggiungere
`&ui_locales=en` all'URL di autorizzazione.

⚠️ Se le modifiche al CSS **non** compaiono, la cache è riaccesa: è l'unico
motivo, non c'è altro da cercare.

## Multilingua

Il realm ha l'internazionalizzazione attiva con `it`, `en` e `de`, default `it`.
**Keycloak segue l'`Accept-Language` del browser**, come la chat — quindi le
due interfacce concordano da sole, senza passaggi di parametri.

Verificato nelle tre lingue: `<html lang="it|en|de">` e i testi presi dal
file giusto. Il tedesco conferma anche che Keycloak legge i `.properties`
come **UTF-8**: gli umlaut compaiono corretti.

Nei `messages_*.properties` si sovrascrivono **solo le chiavi che servono**:
tutte le altre restano le traduzioni standard di Keycloak, che sono complete.

⚠️ **Trappola da conoscere: l'apostrofo.** Nei file `.properties` letti da
MessageFormat l'apostrofo è il carattere di escape, quindi **va raddoppiato**:

```properties
loginAccountTitle=Accedi all''assistente aziendale
```

Con un solo apostrofo la riga viene **ignorata in silenzio** e resta il testo
standard di Keycloak — nessun errore, solo la modifica che non compare.

Per aggiungere una lingua: creare `messages_<codice>.properties`, aggiungere
il codice a `locales=` in `theme.properties` e a `supportedLocales` nel realm
(`keycloak/realm-azienda.json.tmpl`).

## Per un cliente nuovo

Minimo indispensabile: sostituire `logo.png` e cambiare i sette valori dei
ruoli in cima a `tema-azienda.css` (`--sfondo`, `--superficie`, `--testo`,
`--testo-tenue`, `--bordo`, `--azione`, `--azione-hover`), più `--errore` e
`--successo` quando servono stati particolari.

I token `--db-*` in cima al foglio restano l'anello di collegamento con la
guida Decobrands (`tokens.css`): se anche la palette aziendale cambia, si
aggiornano da lì e basta.
