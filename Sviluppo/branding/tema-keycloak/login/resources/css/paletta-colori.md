# Specifica palette colori — tema azienda

Fonte: `tema-azienda.css` (`:root`). È il foglio che detta i colori per tutte
le app associate. I valori non sono inventati: sono la **palette aziendale
Decobrands**, letta da `Decobrands_Marketing/assets/css/tokens.css`
(fonte canonica `docs/STILE-GUIDA-DECOBRANDS.md` sez. 2 e 2.5).

## Token base — palette Decobrands (prefisso `--db-*`)

Ridichiarata nel tema per essere autonoma a runtime. Se la palette cambia,
si aggiorna da `tokens.css` e basta.

| Token | Valore | Ruolo nella guida |
|---|---|---|
| `--db-bg` | `#f5f6f8` | sfondo pagina |
| `--db-border` | `#dee2e6` | bordo card/tabelle/divider |
| `--db-primary` | `#0d6efd` | azioni principali, CTA, link, focus |
| `--db-secondary` | `#6c757d` | stati neutri, etichette, placeholder |
| `--db-dark` | `#212529` | testi primari scuri, header |
| `--db-danger` | `#dc3545` | errori, eliminazione |
| `--db-success` | `#198754` | conferme, stati ok |
| `--db-muted-dim` | `#adb5bd` | testo tenue estremo |

## Token di ruolo — seguono `prefers-color-scheme`

| Ruolo | Chiaro | Scuro |
|---|---|---|
| `--sfondo` (pagina) | `#f5f6f8` | `#212529` |
| `--superficie` (card, campi) | `#fff` | `#2b3035` |
| `--testo` | `#212529` | `#dee2e6` |
| `--testo-tenue` (etichette, note) | `#6c757d` | `#adb5bd` |
| `--bordo` | `#dee2e6` | `#495057` |
| `--azione` (CTA, link, focus) | `#0d6efd` | `#0d6efd` |
| `--azione-hover` | `#0b5ed7` | `#0b5ed7` |
| `--azione-testo` (testo sul pulsante) | `#fff` | `#fff` |

> **Schema scuro**: sono i **ruoli scuri ufficiali** Decobrands
> (`STILE-GUIDA` sez. 2.5, `palette-colori-decobrands.md` sez. 8), mirati sul
> login. Provenienza: `#212529` = `--db-bg` dark, `#2b3035` = `--db-dark` dark
> (superfici elevate), `#dee2e6` = body-color Bootstrap dark, `#adb5bd` =
> `--db-secondary` dark, `#495057` = `--db-border` dark.

## Token derivati / fissi

| Token | Valore | Uso |
|---|---|---|
| `--raggio` | `.5rem` | angoli di card, campi, bottoni |
| `--ring` | `color-mix(in srgb, var(--azione) 25%, transparent)` | alone di focus |
| `--errore` | `#dc3545` | alert errore (tinta con `color-mix` 8%/40%) |
| `--successo` | `#198754` | alert successo (tinta con `color-mix` 10%/40%) |

Tipografia: **Inter**, fallback system sans-serif, `-webkit-font-smoothing: antialiased`.

`--azione-hover` = `#0b5ed7` è l'hover `btn-primary` di Bootstrap 5, non un
valore inventato (il CTA del login è l'unico punto che ne ha bisogno).

## Regola per un cliente nuovo

Cambiare solo i **7 ruoli**: `--sfondo`, `--superficie`, `--testo`,
`--testo-tenue`, `--bordo`, `--azione`, `--azione-hover` (e `--errore` /
`--successo` se servono stati particolari). I token `--db-*` restano come
anello di collegamento con la guida Decobrands.