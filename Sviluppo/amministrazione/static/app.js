/* Amministrazione — portata da Prototipi/amministrazione-rag-aziendale.html.
   Stessa struttura (router a hash, pannello laterale, dialoghi), dati dalle API.
   I permessi arrivano dal server (/api/io) e qui servono SOLO a nascondere:
   ogni API li applica di nuovo. Nascondere non e' proteggere. */
(function () {
  'use strict';
  var BASE = '/amministrazione';
  var IO = null, AZIENDE = [], AZ = '';          // AZ: '' = tutte le aziende abilitate
  var ultimoFocus = null;

  /* ============================== helper ============================== */
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function ic(n) { return '<svg class="i" aria-hidden="true"><use href="#i-' + n + '"/></svg>'; }
  function toast(msg, tipo) {
    var t = document.createElement('div'); t.className = 'toast';
    t.innerHTML = ic(tipo === 'err' ? 'err' : 'check') + '<span>' + esc(msg) + '</span>';
    $('#toast-wrap').appendChild(t);
    setTimeout(function () { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(function () { t.remove(); }, 320); }, 3200);
  }
  function api(metodo, url, corpo) {
    var o = { method: metodo, credentials: 'same-origin', headers: { 'X-Richiesta': '1' } };
    if (corpo !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(corpo); }
    return fetch(BASE + '/api' + url, o).then(function (r) {
      if (r.status === 401) { location.href = BASE + '/'; throw new Error('sessione scaduta'); }
      return r.text().then(function (t) {
        var d = null; try { d = t ? JSON.parse(t) : null; } catch (e) { d = t; }
        if (!r.ok) { var e2 = new Error((d && d.detail) || ('HTTP ' + r.status)); e2.status = r.status; throw e2; }
        return d;
      });
    });
  }
  function can(v) { return IO && IO.permessi[v] === 'M'; }
  function may(v) { return !!(IO && IO.permessi[v]); }

  function quando(iso) {
    if (!iso) return '—';
    var d = new Date(iso), s = (Date.now() - d.getTime()) / 1000, rel;
    if (s < 60) rel = 'adesso';
    else if (s < 3600) rel = Math.round(s / 60) + ' min fa';
    else if (s < 86400) rel = Math.round(s / 3600) + ' ore fa';
    else if (s < 172800) rel = 'ieri';
    else rel = Math.round(s / 86400) + ' giorni fa';
    return '<span title="' + esc(d.toLocaleString('it-IT')) + '">' + esc(rel) + '</span>';
  }
  function dataOra(iso) { return iso ? new Date(iso).toLocaleString('it-IT', { dateStyle: 'short', timeStyle: 'short' }) : '—'; }

  /* stati semantici: sempre icona + testo, mai solo colore (§2.1.3) */
  var GRAVITA = { critico: ['crit', 'Critica', 'b-stato-critico'], errore: ['err', 'Errore', 'b-stato-errore'],
                  attenzione: ['warn', 'Avviso', 'b-stato-attenzione'], info: ['info', 'Info', 'b-stato-info'] };
  var STATO_ANOM = { aperta: ['err', 'Aperta', 'b-stato-errore'], presa: ['info', 'Presa in carico', 'b-stato-info'],
                     risolta: ['ok', 'Risolta', 'b-stato-ok'], ignorata: ['neut', 'Ignorata', 'b-stato-neutro'] };
  var STATO_FONTE = { attiva: ['ok', 'Attiva', 'b-stato-ok'], attesa: ['warn', 'In attesa di approvazione', 'b-stato-attenzione'],
                      sospesa: ['neut', 'Sospesa', 'b-stato-neutro'] };
  var STATO_COLL = { da_provare: ['neut', 'Da provare', 'b-stato-neutro'], funzionante: ['ok', 'Funzionante', 'b-stato-ok'],
                     non_raggiungibile: ['err', 'Non raggiungibile', 'b-stato-errore'],
                     credenziali_non_valide: ['err', 'Credenziali non valide', 'b-stato-errore'],
                     troppi_permessi: ['crit', 'Troppi permessi', 'b-stato-critico'],
                     disattivato: ['neut', 'Disattivato', 'b-stato-neutro'] };
  var SISTEMI = { importazioni: 'Importazioni', documenti: 'Documenti', modelli: 'Modelli AI', accessi: 'Accessi',
                  sistemi: 'Sistemi', qualita: 'Qualità dati' };
  var PROVENIENZA = { cartella: 'Cartella', sharepoint: 'SharePoint', onedrive: 'OneDrive', gdrive: 'Google Drive',
                      nextcloud: 'Nextcloud', posta: 'Casella di posta', caricamento: 'Caricamento manuale', integra: 'Integra' };
  function badge(m) { return m ? '<span class="badge ' + m[2] + '">' + ic(m[0]) + esc(m[1]) + '</span>' : ''; }
  function bAnom(a) {
    if (a.stato === 'risolta' && a.risolta_auto) return '<span class="badge b-stato-ok">' + ic('refresh') + 'Risolta automaticamente</span>';
    return badge(STATO_ANOM[a.stato]);
  }
  function resBadge(r) {
    return r === 'cloud_ok' ? '<span class="res res-cloud">' + ic('cloud') + 'Può usare servizi esterni</span>'
                            : '<span class="res res-interno">' + ic('shield') + 'Resta in azienda</span>';
  }
  function nomeAz(c) { var a = AZIENDE.filter(function (x) { return x.codice === c; })[0]; return a ? a.ragione_sociale : c; }
  function chipAz(lista) {
    if (lista == null || lista === '') return '<span style="color:var(--testo-debole)">—</span>';
    if (!Array.isArray(lista)) lista = String(lista).split(',');
    if (!lista.length) return '<span class="chipacloud">nessuna</span>';
    if (AZIENDE.length > 1 && AZIENDE.every(function (a) { return lista.indexOf(a.codice) > -1; })) return '<span class="chipacloud">Tutto il gruppo</span>';
    return lista.map(function (c) { return '<span class="chipacloud">' + esc(nomeAz(c)) + '</span>'; }).join(' ');
  }
  function inAzienda(lista) {           // filtro del selettore in testata
    if (!AZ) return true;
    if (lista == null || lista === '') return true;
    if (!Array.isArray(lista)) lista = String(lista).split(',');
    return lista.indexOf(AZ) > -1;
  }
  function roNota(ruolo) { return '<span class="ro-note">' + ic('lock') + 'Sola lettura — richiede il ruolo ' + esc(ruolo) + '</span>'; }

  /* ============================== stati schermata (§5.4) ============================== */
  function caricamento(host) {
    host.innerHTML = '<div class="card" aria-busy="true"><span class="sr">Caricamento in corso</span>' +
      [22, 14, 30, 18].map(function (w) { return '<div class="sk-row"><span class="sk" style="width:' + w + '%"></span><span class="sk" style="width:' + (80 - w) + '%"></span></div>'; }).join('') + '</div>';
  }
  function vuoto(titolo, testo) {
    return '<div class="card"><div class="state-box"><div class="s-ico">' + ic('info') + '</div><h3>' + esc(titolo) + '</h3><p>' + esc(testo) + '</p></div></div>';
  }
  function errore(host, err, riprova) {
    host.innerHTML = '<div class="card"><div class="state-box" role="alert"><div class="s-ico" style="background:var(--stato-critico-sfondo);color:var(--stato-critico)">' + ic('crit') + '</div>' +
      '<h3>' + (err && err.status === 403 ? 'Non hai accesso a questa schermata' : 'Qualcosa è andato storto') + '</h3>' +
      '<p>' + esc(err && err.status === 403 ? err.message : 'Il servizio non risponde. Non ci sono dati vecchi da mostrare: riprova tra poco.') + '</p>' +
      (err && err.status === 403 ? '' : '<button class="btn btn-secondary btn-sm" id="riprova">' + ic('refresh') + 'Riprova</button>') + '</div></div>';
    var b = $('#riprova'); if (b) b.onclick = riprova;
  }
  function schermata(titolo, sotto, destra, corpo) {
    return '<div class="page-head"><div><h1 class="page-title">' + titolo + '</h1>' + (sotto ? '<p class="page-sub">' + sotto + '</p>' : '') + '</div>' + (destra || '') + '</div>' + corpo;
  }
  /* Righe cliccabili: raggiungibili e attivabili anche da tastiera. */
  function righeAttive(host, apri) {
    $$('tr[data-apri]', host).forEach(function (tr) {
      tr.tabIndex = 0;
      tr.onclick = function () { apri(tr.dataset.apri, tr); };
      tr.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); apri(tr.dataset.apri, tr); } };
    });
  }
  function ordinabili(host, stato, ridisegna) {
    $$('th[data-k]', host).forEach(function (th) {
      th.tabIndex = 0;
      th.setAttribute('aria-sort', stato.k === th.dataset.k ? (stato.asc ? 'ascending' : 'descending') : 'none');
      function via() { if (stato.k === th.dataset.k) stato.asc = !stato.asc; else { stato.k = th.dataset.k; stato.asc = true; } ridisegna(); }
      th.onclick = via;
      th.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); via(); } };
    });
  }
  function ordina(arr, st, valore) {
    return arr.slice().sort(function (a, b) {
      var x = valore(a, st.k), y = valore(b, st.k);
      return (x < y ? -1 : x > y ? 1 : 0) * (st.asc ? 1 : -1);
    });
  }
  function freccia(st, k) { return st.k === k ? (st.asc ? ' ▲' : ' ▼') : ''; }
  function paginatore(host, pag, tot, ridisegna) {
    var pagine = Math.max(1, Math.ceil(tot / pag.per)); if (pag.n > pagine) pag.n = pagine;
    host.innerHTML = '<span>' + (tot ? ((pag.n - 1) * pag.per + 1) + '–' + Math.min(pag.n * pag.per, tot) + ' di ' + tot : '0 risultati') + '</span>' +
      '<button class="btn btn-secondary btn-sm" ' + (pag.n <= 1 ? 'disabled' : '') + '>Precedente</button>' +
      '<button class="btn btn-secondary btn-sm" ' + (pag.n >= pagine ? 'disabled' : '') + '>Successiva</button>';
    var b = $$('button', host);
    b[0].onclick = function () { pag.n--; ridisegna(); };
    b[1].onclick = function () { pag.n++; ridisegna(); };
  }

  /* ============================== pannello e dialogo ============================== */
  function apriPannello(testa, corpo) {
    ultimoFocus = document.activeElement;
    $('#drawer-head').innerHTML = testa; $('#drawer-body').innerHTML = corpo;
    document.body.setAttribute('data-drawer', '1'); $('#drawer').inert = false;
    $('#drawer-close').focus();
  }
  function chiudiPannello() {
    if (!document.body.hasAttribute('data-drawer')) return;
    document.body.removeAttribute('data-drawer'); $('#drawer').inert = true;
    if (ultimoFocus && document.contains(ultimoFocus)) ultimoFocus.focus();
  }
  var dopoModal = null;
  function dialogo(titolo, corpo, conferma, etichetta) {
    dopoModal = document.activeElement;
    $('#modal-title').textContent = titolo; $('#modal-body').innerHTML = corpo;
    $('#modal-ok').textContent = etichetta || 'Conferma';
    $('#modal-wrap').hidden = false;
    var primo = $('#modal-body input, #modal-body select, #modal-body textarea');
    (primo || $('#modal-ok')).focus();
    $('#modal-ok').onclick = function () {
      var r = conferma();
      if (r && r.then) { $('#modal-ok').disabled = true; r.then(function (ok) { $('#modal-ok').disabled = false; if (ok !== false) chiudiDialogo(); }); }
      else if (r !== false) chiudiDialogo();
    };
  }
  function chiudiDialogo() { $('#modal-wrap').hidden = true; if (dopoModal && document.contains(dopoModal)) dopoModal.focus(); }
  $('#modal-cancel').onclick = chiudiDialogo;
  /* Tab resta dentro il dialogo aperto: il resto della pagina e' sotto il velo. */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Tab') {
      var cont = !$('#modal-wrap').hidden ? $('#modal-wrap .modal') : (document.body.hasAttribute('data-drawer') ? $('#drawer') : null);
      if (!cont) return;
      var f = $$('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea,[tabindex]:not([tabindex="-1"]),summary', cont).filter(function (x) { return x.offsetParent !== null; });
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
    if (e.key !== 'Escape') return;
    if (!$('#modal-wrap').hidden) { chiudiDialogo(); e.preventDefault(); return; }
    if (document.body.hasAttribute('data-drawer')) { chiudiPannello(); e.preventDefault(); return; }
    if (!$('#pmenu').hidden) { menuPersona(false); $('#person').focus(); return; }
    if (document.body.classList.contains('side-open')) { laterale(false); e.preventDefault(); }
  });
  $('#drawer-close').onclick = chiudiPannello;
  $('#scrim').onclick = function () { chiudiPannello(); laterale(false); };

  /* ============================== intestazione ============================== */
  function laterale(aperto) { document.body.classList.toggle('side-open', aperto); $('#menu-btn').setAttribute('aria-expanded', aperto ? 'true' : 'false'); }
  $('#menu-btn').onclick = function (e) { e.stopPropagation(); laterale(!document.body.classList.contains('side-open')); };
  function menuPersona(aperto) { $('#pmenu').hidden = !aperto; $('#person').setAttribute('aria-expanded', aperto ? 'true' : 'false'); if (aperto) { var b = $('#pmenu button'); if (b) b.focus(); } }
  $('#person').onclick = function (e) { e.stopPropagation(); menuPersona($('#pmenu').hidden); };
  $('#person').onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); menuPersona($('#pmenu').hidden); } };
  $('#pmenu').onclick = function (e) { e.stopPropagation(); };
  document.addEventListener('click', function () { if (!$('#pmenu').hidden) menuPersona(false); });

  function tema(t) {
    try { if (t) localStorage.setItem('amm-tema', t); else localStorage.removeItem('amm-tema'); } catch (e) {}
    if (t) document.documentElement.setAttribute('data-tema', t); else document.documentElement.removeAttribute('data-tema');
    $$('#tema-sel button').forEach(function (b) { b.setAttribute('aria-pressed', b.dataset.tema === (t || '') ? 'true' : 'false'); });
  }
  $$('#tema-sel button').forEach(function (b) { b.onclick = function () { tema(b.dataset.tema); }; });

  function salvaScelta(v) { return api('POST', '/scelta', { scelta: v }); }
  $('#cambia-iniziale').onclick = function () {
    menuPersona(false);
    dialogo('Schermata iniziale',
      '<p style="font-size:13.5px;color:var(--testo-tenue);margin-top:0">Dove ti porta il login la prossima volta.</p>' +
      '<div class="field"><label for="m-iniziale">Dopo il login</label><select class="select" id="m-iniziale">' +
      '<option value="">Chiedi ogni volta</option><option value="chat">Chat</option><option value="amministrazione">Amministrazione</option></select></div>',
      function () { return salvaScelta($('#m-iniziale').value).then(function () { toast('Preferenza salvata'); }); }, 'Salva');
  };

  $('#azienda-sel').onchange = function () { AZ = this.value; instrada(); };

  /* ============================== scelta (§4) ============================== */
  function mostraScelta() {
    $('#view-scelta').hidden = false; $('#view-app').hidden = true;
    document.title = 'Scelta · ' + IO.assistente;
    var h = new Date().getHours();
    $('#saluto').textContent = (h < 13 ? 'Buongiorno' : h < 18 ? 'Buon pomeriggio' : 'Buonasera') + ', ' + (IO.nome.split(' ')[0] || IO.username);
    $('#card-chat').href = IO.chat;
    $('#ricorda').checked = false;
    $$('#view-scelta [data-scelta]').forEach(function (a) {
      a.onclick = function (e) {
        if (!$('#ricorda').checked) return;
        e.preventDefault(); var dove = a.getAttribute('href');
        salvaScelta(a.dataset.scelta).then(function () { if (dove.charAt(0) === '#') location.hash = dove; else location.href = dove; });
      };
    });
    var f = $('#flag-admin'); f.hidden = true;
    if (may('anomalie')) api('GET', '/panoramica').then(function (p) {
      var n = {}; (p.anomalie ? p.anomalie.conteggi : []).forEach(function (c) { n[c.gravita] = c.n; });
      var tot = (n.critico || 0) + (n.errore || 0) + (n.attenzione || 0) + (n.info || 0);
      if (n.critico) { f.className = 'badge b-stato-critico alert-flag'; f.innerHTML = ic('crit') + n.critico + (n.critico > 1 ? ' critiche' : ' critica'); }
      else if (tot) { f.className = 'badge b-stato-attenzione alert-flag'; f.innerHTML = ic('warn') + tot + ' aperte'; }
      else { f.className = 'badge b-stato-ok alert-flag'; f.innerHTML = ic('ok') + 'Nessuna anomalia aperta'; }
      f.hidden = false;
    }).catch(function () {});
    $('#card-chat').focus();
  }

  /* ============================== strumenti esterni ============================== */
  function renderEsterno(chiave) {
    var s = IO.strumenti[chiave], c = $('#content');
    if (!s) { location.hash = '#/panoramica'; return; }
    document.title = s.nome + ' · Amministrazione';
    c.innerHTML = '<div class="card pad-5" style="max-width:680px;margin:40px auto">' +
      '<div style="display:flex;gap:12px;align-items:center;margin-bottom:12px"><div class="s-ic" style="width:40px;height:40px;border-radius:10px;background:var(--azione-soft);color:var(--azione);display:grid;place-items:center">' + ic('ext') + '</div>' +
      '<h1 class="page-title" style="font-size:18px">' + esc(s.nome) + ' <span style="font-weight:400;color:var(--testo-debole)">— ' + esc(s.prodotto) + '</span></h1></div>' +
      (s.url
        ? '<p class="page-sub">Qui si apre <b>' + esc(s.nome) + '</b>, in una nuova scheda. Lo strumento ha il suo aspetto; l\'accesso è lo stesso (Keycloak) e non si reinseriscono le credenziali. Questa amministrazione resta aperta qui.</p>' +
          '<div style="margin-top:20px;display:flex;gap:10px;flex-wrap:wrap"><a class="btn btn-primary" href="' + esc(s.url) + '" target="_blank" rel="noopener">' + ic('ext') + 'Apri ' + esc(s.nome) + '<span class="sr"> (si apre in una nuova scheda)</span></a>' +
          '<a class="btn btn-secondary" href="#/panoramica">Torna all\'amministrazione</a></div>'
        : '<div class="notice notice-info" style="margin-top:6px">' + ic('info') + '<span><b>Non ancora installato.</b> Qui si aprirà <b>' + esc(s.nome) + '</b> (' + esc(s.prodotto) + ') quando sarà attivo: il collegamento lo configura chi installa il sistema.</span></div>' +
          '<div style="margin-top:20px"><a class="btn btn-secondary" href="#/panoramica">Torna all\'amministrazione</a></div>') +
      '</div>';
  }

  /* ============================== PANORAMICA (§6) ============================== */
  var ENTITA = [['soggetti', 'Clienti'], ['articoli', 'Articoli'], ['listini', 'Listini'], ['documenti_vendita', 'Documenti vendita'],
                ['documenti_acquisto', 'Documenti acquisto'], ['scadenze', 'Scadenze'], ['giacenze', 'Giacenze']];
  function blocco(titolo, intro, corpo) {
    return '<div class="card"><div class="c-head"><div><h2>' + titolo + '</h2>' + (intro ? '<p class="h-intro">' + intro + '</p>' : '') + '</div></div>' + corpo + '</div>';
  }
  function renderPanoramica() {
    var c = $('#content'); caricamento(c);
    api('GET', '/panoramica').then(function (p) {
      var out = '';
      if (p.anomalie) {
        var n = {}; p.anomalie.conteggi.forEach(function (x) { n[x.gravita] = x.n; });
        var gravi = p.anomalie.piu_gravi.filter(function (a) { return inAzienda(a.azienda); });
        out += '<div class="kpi-grid card">' +
          '<div class="kpi pad-4"><div class="k-t">Anomalie aperte ' + ic('crit') + '</div><div class="k-n">' + (n.critico || 0) + '<small> critiche</small></div><div class="k-l">' + (n.errore || 0) + ' errori · ' + (n.attenzione || 0) + ' avvisi · ' + (n.info || 0) + ' informative</div></div>' +
          '<div class="kpi pad-4"><div class="k-t">Nuove nelle ultime 24 ore ' + ic('clock') + '</div><div class="k-n">' + p.anomalie.nuove_24h + '</div><div class="k-l">Contate per problema, non per evento</div></div></div><div style="height:16px"></div>';
        out += blocco('Anomalie aperte', 'Le più gravi prima.', gravi.length
          ? '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Gravità</th><th scope="col">Titolo</th><th scope="col">Azienda</th><th scope="col" class="num-col">Occorrenze</th><th scope="col">Ultima</th></tr></thead><tbody>' +
            gravi.map(function (a) { return '<tr data-apri="' + a.id + '"><td>' + badge(GRAVITA[a.gravita]) + '</td><td class="prim">' + esc(a.titolo) + '</td><td>' + chipAz(a.azienda) + '</td><td class="num-col">' + a.occorrenze + '</td><td>' + quando(a.ultima) + '</td></tr>'; }).join('') +
            '</tbody></table></div><div style="padding:12px 18px;border-top:1px solid var(--bordo)"><a class="btn btn-ghost btn-sm" href="#/anomalie">Vedi tutte le anomalie</a></div>'
          : '<div class="notice notice-ok" style="margin:14px 18px">' + ic('ok') + '<span>Nessuna anomalia aperta nelle tue aree.</span></div>');
      }
      var imp = p.importazioni.filter(function (r) { return inAzienda(r.azienda); });
      var perAz = {}; imp.forEach(function (r) { (perAz[r.azienda] = perAz[r.azienda] || { nome: r.ragione_sociale, e: {} }).e[r.entita] = r; });
      var codici = Object.keys(perAz);
      out += blocco('Importazioni', 'Esito dell\'ultima esecuzione per azienda e tipo di dato.', codici.length
        ? '<div style="overflow:auto"><div class="imp" role="table" aria-label="Importazioni per azienda e tipo di dato"><div class="ih" role="columnheader">Azienda</div>' +
          ENTITA.map(function (e) { return '<div class="ih" role="columnheader">' + e[1] + '</div>'; }).join('') +
          codici.map(function (cod) {
            return '<div class="ih" role="rowheader" style="font-weight:400;text-transform:none">' + esc(perAz[cod].nome) + '</div>' + ENTITA.map(function (e) {
              var r = perAz[cod].e[e[0]];
              if (!r) return '<div class="icell" role="cell"><div class="t t-na">' + ic('neut') + 'Non disponibile</div><div class="s">Il gestionale non lo fornisce</div></div>';
              var m = r.esito === 'ok' ? ['t-ok', 'ok', 'Riuscita'] : r.esito === 'errore' ? ['t-err', 'err', 'Fallita'] : ['t-warn', 'clock', 'In corso'];
              return '<div class="icell" role="cell"><div class="t ' + m[0] + '">' + ic(m[1]) + m[2] + '</div><div class="s">' + quando(r.completata_il || r.iniziata_il) + '</div></div>';
            }).join('');
          }).join('') + '</div></div>'
        : '<div class="state-box" style="padding:28px 24px"><p>Nessuna importazione registrata: nessun gestionale è ancora collegato. Il collegamento lo configura chi installa il sistema.</p></div>') +
        (may('dagster') ? '<div style="padding:12px 18px;border-top:1px solid var(--bordo)"><a class="btn btn-ghost btn-sm" href="#/esterno/dagster">' + ic('ext') + 'Gestione importazioni</a></div>' : '');
      var sis = p.sistemi || { semaforo: 'sconosciuto', non_verdi: [], monitor: [] };
      var corpoSis;
      if (sis.semaforo === 'sconosciuto') {
        corpoSis = '<div class="notice notice-info" style="margin:14px 18px">' + ic('neut') + '<span><b>Stato sconosciuto.</b> Il monitoraggio dei sistemi non risponde: «sconosciuto» non vuol dire «funziona».</span></div>';
      } else {
        var azioni = can('azioni');
        corpoSis = '<div class="sys-list">' + sis.monitor.map(function (m) {
          var barra = (m.barra || []).map(function (s) {
            return '<i class="seg ' + (s === 1 ? 'seg-up' : s === 0 ? 'seg-down' : 'seg-pend') + '"></i>';
          }).join('');
          var b = m.stato === 'up' ? ['ok', 'Attivo', 'b-stato-ok'] : m.stato === 'down' ? ['err', 'Giù', 'b-stato-errore'] : ['warn', 'In attesa', 'b-stato-attenzione'];
          return '<div class="sys-row">' +
            '<span class="sys-barra" title="ultimi ' + (m.barra || []).length + ' controlli">' + (barra || '<i class="seg seg-pend"></i>') + '</span>' +
            '<span class="sys-nome">' + esc(m.nome) + ' <small>' + esc(m.tipo) + '</small></span>' +
            '<span class="sys-stato">' + badge(b) + '</span>' +
            '<span class="sys-ping">' + (m.ping != null ? m.ping + ' ms' : '—') + '</span>' +
            (azioni ? '<span class="sys-azioni"><button class="btn btn-ghost btn-sm" data-az="riavvia" data-nome="' + esc(m.nome) + '" title="Riavvia">' + ic('refresh') + '<span class="sr">Riavvia</span></button> <button class="btn btn-ghost btn-sm" data-az="ferma" data-nome="' + esc(m.nome) + '" title="Ferma">' + ic('x') + '<span class="sr">Ferma</span></button></span>' : '') +
            '</div>';
        }).join('') + '</div>';
      }
      out += blocco('Stato dei sistemi', 'Semaforo complessivo e azioni sui servizi.', corpoSis +
        (may('uptime') ? '<div style="padding:0 18px 12px"><a class="btn btn-ghost btn-sm" href="#/esterno/uptime">' + ic('ext') + 'Monitoraggio sistemi</a></div>' : ''));
      if (p.fonti) {
        var f = p.fonti.filter(function (x) { return inAzienda(x.aziende); });
        out += blocco('Fonti', 'In attesa di approvazione o senza responsabile.', f.length
          ? '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Fonte</th><th scope="col">Stato</th><th scope="col">Aziende</th><th scope="col">Da fare</th></tr></thead><tbody>' + f.map(function (x) {
              var senza = !x.owner || x.owner === 'da-assegnare';
              return '<tr data-fonte="' + esc(x.id) + '" data-apri="' + esc(x.id) + '"><td class="prim">' + esc(x.descrizione) + '</td><td>' + badge(STATO_FONTE[x.stato]) + '</td><td>' + chipAz(x.aziende) + '</td><td style="color:var(--testo-tenue)">' + (senza ? 'Senza responsabile' : 'In attesa di approvazione') + '</td></tr>';
            }).join('') + '</tbody></table></div>'
          : '<div class="notice notice-ok" style="margin:14px 18px">' + ic('ok') + '<span>Tutte le fonti hanno un responsabile e sono approvate.</span></div>');
      }
      if (may('utenti')) out += blocco('Accessi', 'Persone, gruppi e aziende abilitate.',
        '<div style="padding:14px 18px;display:flex;gap:8px;flex-wrap:wrap"><a class="btn btn-secondary btn-sm" href="#/utenti">' + ic('user') + 'Utenti</a>' + (may('profili') ? '<a class="btn btn-ghost btn-sm" href="#/profili">' + ic('shield') + 'Profili amministrativi</a>' : '') + '</div>');
      out += blocco('Ultime modifiche', 'Le cinque più recenti dal registro.', p.modifiche.length
        ? '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Quando</th><th scope="col">Chi</th><th scope="col">Azione</th></tr></thead><tbody>' +
          p.modifiche.map(function (r) { return '<tr data-reg="' + r.id + '" data-apri="' + r.id + '"><td>' + quando(r.quando) + '</td><td>' + esc(r.chi_nome || r.chi) + '</td><td>' + esc(r.azione) + '</td></tr>'; }).join('') + '</tbody></table></div>'
        : '<div class="state-box" style="padding:28px 24px"><p>Nessuna modifica registrata.</p></div>');
      c.innerHTML = schermata('Panoramica', 'Va tutto bene? Se no, dove guardo? ' + (AZ ? 'Solo ' + esc(nomeAz(AZ)) + '.' : 'Tutte le tue aziende.'), '', out);
      $$('tr[data-apri]', c).forEach(function (tr) {
        if (tr.dataset.fonte) righeAttive(tr.parentNode, function (id) { apriFonte(id); });
        else if (tr.dataset.reg) righeAttive(tr.parentNode, function (id) { apriRegistro(id); });
        else righeAttive(tr.parentNode, function (id) { apriAnomalia(id); });
      });
      $$('#content [data-az]').forEach(function (b) {
        b.onclick = function () {
          var nome = b.dataset.nome, az = b.dataset.az;
          var riavvio = az === 'riavvia';
          dialogo((riavvio ? 'Riavviare' : 'Fermare') + ' «' + nome + '»?',
            '<p style="margin-top:0;font-size:13.5px">' + (riavvio
              ? 'Il servizio verrà riavviato: chi lo usa può notare una breve interruzione. L\'operazione viene registrata.'
              : 'Il servizio verrà fermato: non sarà raggiungibile finché non viene riavviato. L\'operazione viene registrata.') + '</p>',
            function () {
              return api('POST', '/sistemi/' + encodeURIComponent(nome) + '/azione', { azione: az })
                .then(function () { toast(riavvio ? 'Riavvio richiesto' : 'Arresto richiesto'); })
                .catch(function (e) { toast(e.message, 'err'); return false; });
            }, riavvio ? 'Riavvia' : 'Ferma');
        };
      });
    }).catch(function (e) { errore(c, e, renderPanoramica); });
  }

  /* ============================== FONTI (§7) ============================== */
  var FF = { q: '', tipo: '', res: '', stato: '', senza: false }, FS = { k: 'descrizione', asc: true }, FP = { n: 1, per: 12 }, FONTI = [];
  function renderFonti() {
    var c = $('#content'); caricamento(c);
    api('GET', '/fonti').then(function (d) { FONTI = d; disegnaFonti(); }).catch(function (e) { errore(c, e, renderFonti); });
  }
  function disegnaFonti() {
    var c = $('#content');
    if (!FONTI.length) { c.innerHTML = schermata('Fonti', '', '', vuoto('Nessuna fonte registrata', 'Le fonti le aggiunge chi installa il sistema.')); return; }
    var arr = FONTI.filter(function (f) {
      if (!inAzienda(f.aziende)) return false;
      if (FF.q && (f.descrizione + ' ' + f.percorso).toLowerCase().indexOf(FF.q.toLowerCase()) < 0) return false;
      if (FF.tipo && f.tipo !== FF.tipo) return false;
      if (FF.res && f.residency !== FF.res) return false;
      if (FF.stato && f.stato !== FF.stato) return false;
      if (FF.senza && f.owner && f.owner !== 'da-assegnare') return false;
      return true;
    });
    arr = ordina(arr, FS, function (f, k) { return String(f[k] == null ? '' : f[k]).toLowerCase(); });
    var tot = arr.length, pag = arr.slice((FP.n - 1) * FP.per, FP.n * FP.per);
    var chips = [];
    if (FF.tipo) chips.push(['tipo', FF.tipo === 'documenti' ? 'Documenti' : 'Dati gestionali']);
    if (FF.res) chips.push(['res', FF.res === 'interno' ? 'Resta in azienda' : 'Può usare servizi esterni']);
    if (FF.stato) chips.push(['stato', STATO_FONTE[FF.stato][1]]);
    if (FF.senza) chips.push(['senza', 'Senza responsabile']);
    c.innerHTML = schermata('Fonti', 'Le raccolte di documenti e di dati gestionali che l\'assistente può usare: chi le vede e se possono usare servizi esterni.',
      can('fonti') ? '<button class="btn btn-primary" id="f-nuova">' + ic('plus') + 'Nuova fonte</button>' : (may('fonti') ? roNota('Gestione fonti') : ''),
      '<div class="card"><div class="filters">' +
      '<div class="field search-box grow"><label class="sr" for="f-q">Cerca</label>' + ic('search') + '<input class="input" id="f-q" placeholder="Cerca nome o percorso…" value="' + esc(FF.q) + '"></div>' +
      '<div class="field"><label for="f-tipo">Tipo</label><select class="select" id="f-tipo"><option value="">Tutti</option><option value="documenti">Documenti</option><option value="gestionale">Dati gestionali</option></select></div>' +
      '<div class="field"><label for="f-res">Uso di servizi esterni</label><select class="select" id="f-res"><option value="">Tutti</option><option value="interno">Resta in azienda</option><option value="cloud_ok">Può usare servizi esterni</option></select></div>' +
      '<div class="field"><label for="f-stato">Stato</label><select class="select" id="f-stato"><option value="">Tutti</option><option value="attiva">Attiva</option><option value="attesa">In attesa di approvazione</option><option value="sospesa">Sospesa</option></select></div>' +
      '<div class="field"><label class="check-row" style="font-weight:400"><input type="checkbox" id="f-senza" ' + (FF.senza ? 'checked' : '') + '/> Solo senza responsabile</label></div>' +
      '</div><div class="active-filters" id="f-chips">' + chips.map(function (x) { return '<span class="chip">' + esc(x[1]) + '<button data-k="' + x[0] + '" aria-label="Togli il filtro ' + esc(x[1]) + '">' + ic('x') + '</button></span>'; }).join('') + '</div>' +
      '<div class="res-meta" aria-live="polite">' + tot + (tot === 1 ? ' fonte' : ' fonti') + '</div>' +
      '<div class="table-box"><table class="tbl"><thead><tr>' +
      '<th scope="col" class="sortable" data-k="descrizione">Nome' + freccia(FS, 'descrizione') + '</th><th scope="col">Tipo</th><th scope="col">Provenienza</th><th scope="col">Chi può vederla</th><th scope="col">Aziende</th><th scope="col">Uso di servizi esterni</th><th scope="col">Responsabile</th><th scope="col" class="sortable" data-k="aggiornata">Aggiornata' + freccia(FS, 'aggiornata') + '</th><th scope="col" class="sortable" data-k="stato">Stato' + freccia(FS, 'stato') + '</th></tr></thead><tbody>' +
      pag.map(function (f) {
        return '<tr data-apri="' + esc(f.id) + '"><td class="prim">' + esc(f.descrizione) +
          (f.in_lettura ? ' ' + badge(['info', 'In importazione…', 'b-stato-info']) : '') + '</td><td>' + (f.tipo === 'documenti' ? 'Documenti' : 'Dati gestionali') + '</td>' +
          '<td style="color:var(--testo-tenue)">' + esc(PROVENIENZA[f.provenienza] || f.provenienza) + '<span class="sub">' + esc(f.percorso) + '</span></td>' +
          '<td>' + esc(f.acl_groups.join(', ')) + '</td><td>' + chipAz(f.aziende) + '</td><td>' + resBadge(f.residency) + '</td>' +
          '<td>' + (f.owner && f.owner !== 'da-assegnare' ? esc(f.owner) : '<span style="color:var(--stato-attenzione)">' + ic('warn') + ' Nessuno</span>') + '</td>' +
          '<td>' + quando(f.aggiornata) + '</td><td>' + badge(STATO_FONTE[f.stato]) + '</td></tr>';
      }).join('') + '</tbody></table>' + (tot ? '' : '<div class="empty-note">Nessuna fonte corrisponde ai filtri.</div>') + '</div>' +
      '<div class="pager" id="f-pager"></div></div>');
    $('#f-tipo').value = FF.tipo; $('#f-res').value = FF.res; $('#f-stato').value = FF.stato;
    var t; $('#f-q').oninput = function () { clearTimeout(t); var v = this.value; t = setTimeout(function () { FF.q = v; FP.n = 1; disegnaFonti(); $('#f-q').focus(); var i = $('#f-q'); i.setSelectionRange(v.length, v.length); }, 250); };
    $('#f-tipo').onchange = function () { FF.tipo = this.value; FP.n = 1; disegnaFonti(); $('#f-tipo').focus(); };
    $('#f-res').onchange = function () { FF.res = this.value; FP.n = 1; disegnaFonti(); $('#f-res').focus(); };
    $('#f-stato').onchange = function () { FF.stato = this.value; FP.n = 1; disegnaFonti(); $('#f-stato').focus(); };
    $('#f-senza').onchange = function () { FF.senza = this.checked; FP.n = 1; disegnaFonti(); $('#f-senza').focus(); };
    $$('#f-chips button').forEach(function (b) { b.onclick = function () { FF[b.dataset.k] = b.dataset.k === 'senza' ? false : ''; FP.n = 1; disegnaFonti(); }; });
    ordinabili(c, FS, disegnaFonti);
    paginatore($('#f-pager'), FP, tot, disegnaFonti);
    righeAttive(c, apriFonte);
    var bn = $('#f-nuova'); if (bn) bn.onclick = nuovaFonte;
  }
  /* Nuova fonte: prima il tipo, poi il modulo di quel tipo. Ogni tipo entra
     nella conoscenza a modo suo; i non ancora disponibili si vedono, spenti. */
  var MODULI_FONTE = { cartella: nuovaCartella };
  function nuovaFonte() {
    api('GET', '/fonti/tipi').then(function (tipi) {
      dialogo('Nuova fonte',
        '<fieldset style="border:0;padding:0;margin:0"><legend class="sr">Tipo di fonte</legend>' +
        tipi.map(function (t, i) {
          return '<label class="check-row" style="align-items:flex-start;padding:8px 0;font-weight:400' + (t.disponibile ? '' : ';color:var(--testo-debole)') + '">' +
            '<input type="radio" name="m-tipo" value="' + esc(t.id) + '"' + (t.disponibile ? '' : ' disabled') + (i === 0 ? ' checked' : '') + '> ' +
            '<span><b>' + esc(t.nome) + '</b>' + (t.disponibile ? '' : ' <span class="badge b-stato-neutro">' + ic('neut') + (t.id === 'gestionale' ? 'Da Impostazioni → Gestionali' : 'Non ancora disponibile') + '</span>') +
            '<span class="sub" style="display:block">' + esc(t.descrizione) + '</span></span></label>';
        }).join('') + '</fieldset>',
        function () {
          var t = $('input[name="m-tipo"]:checked');
          if (!t || !MODULI_FONTE[t.value]) return false;
          setTimeout(MODULI_FONTE[t.value], 0);     /* dopo la chiusura di questo dialogo */
        }, 'Avanti');
    }).catch(function (e) { toast(e.message, 'err'); });
  }
  /* Una cartella per gruppo: chi sta nel gruppo la vede (e ci deposita i
     file, sulla condivisione); se esiste <gruppo>-gestori, i gestori la vedono
     e ne rispondono. Nasce in attesa: si indicizza, poi qualcuno la attiva. */
  function nuovaCartella() {
    api('GET', '/gruppi').then(function (gruppi) {
      var operativi = gruppi.filter(function (g) { return !/-gestori$/.test(g) && g !== 'tutti'; });
      dialogo('Nuova fonte: cartella',
        '<p style="font-size:13.5px;color:var(--testo-tenue);margin-top:0">Una cartella della condivisione diventa una fonte dell\'assistente. Tutto ciò che contiene viene indicizzato, tranne le sottocartelle <span class="mono">_bozze</span> e <span class="mono">_archivio</span> e i fogli di calcolo con i prezzi.</p>' +
        '<div class="field"><label for="m-cg">Gruppo che la vede e ci deposita i file</label><select class="select" id="m-cg">' +
          operativi.map(function (g) { return '<option value="' + esc(g) + '">' + esc(g) + (gruppi.indexOf(g + '-gestori') > -1 ? ' (con gestori)' : '') + '</option>'; }).join('') + '</select>' +
          '<p class="ro-note" style="margin-top:6px">' + ic('info') + 'Il gruppo si crea in Accessi → Gruppi. Chi sta in «<i>gruppo</i>-gestori» gestisce le persone del gruppo e risponde della cartella.</p></div>' +
        '<div class="field"><label for="m-cn">Nome</label><input class="input" id="m-cn" placeholder="es. Documenti della sicurezza"></div>' +
        '<fieldset class="field" style="border:0;padding:0;margin:0 0 12px"><legend style="font-size:13px;font-weight:600;margin-bottom:6px">Aziende</legend>' +
          AZIENDE.map(function (a, i) { return '<label class="check-row" style="font-weight:400"><input type="checkbox" name="m-caz" value="' + esc(a.codice) + '"' + (AZIENDE.length === 1 || (AZ ? AZ === a.codice : i === 0) ? ' checked' : '') + '> ' + esc(a.ragione_sociale) + '</label>'; }).join('') + '</fieldset>' +
        '<div class="field"><label for="m-cp">Percorso della cartella (dalla radice delle cartelle)</label><input class="input mono" id="m-cp" placeholder="luis/sicurezza"></div>' +
        '<div class="field"><label for="m-ci">Codice della fonte</label><input class="input mono" id="m-ci" placeholder="sicurezza-luis"></div>',
        function () {
          var az = $$('input[name="m-caz"]:checked').map(function (x) { return x.value; });
          return api('POST', '/fonti', { provenienza: 'cartella', gruppo: $('#m-cg').value, descrizione: $('#m-cn').value, aziende: az,
                                         percorso: $('#m-cp').value, id: $('#m-ci').value })
            .then(function (r) { toast('Cartella collegata: in attesa di approvazione. Chi la vede: ' + r.gruppi.join(', ')); renderFonti(); })
            .catch(function (e) { toast(e.message, 'err'); return false; });
        }, 'Collega la cartella');
      /* Suggerimenti: percorso e codice dal gruppo e dall'azienda, finché non li si tocca. */
      var toccati = {};
      function suggerisci() {
        var g = $('#m-cg').value, az = $$('input[name="m-caz"]:checked').map(function (x) { return x.value; });
        if (!toccati.p) $('#m-cp').value = (az.length === 1 ? az[0] + '/' : '') + g;
        if (!toccati.i) $('#m-ci').value = g + (az.length === 1 ? '-' + az[0] : '');
        if (!toccati.n) $('#m-cn').value = g ? 'Documenti ' + g : '';
      }
      $('#m-cp').oninput = function () { toccati.p = true; };
      $('#m-ci').oninput = function () { toccati.i = true; };
      $('#m-cn').oninput = function () { toccati.n = true; };
      $('#m-cg').onchange = suggerisci;
      $$('input[name="m-caz"]').forEach(function (x) { x.onchange = suggerisci; });
      suggerisci();
    }).catch(function (e) { toast(e.message, 'err'); });
  }
  /* Scheda Documenti: cosa l'indicizzazione ha trovato, file per file, per
     controllare una fonte PRIMA di attivarla. Solo nomi e stati, mai il testo. */
  var STATO_DOC = { indicizzato: ['ok', 'Indicizzato', 'b-stato-ok'], vuoto: ['neut', 'Nessun testo', 'b-stato-neutro'],
                    errore: ['err', 'Non leggibile', 'b-stato-errore'], escluso: ['neut', 'Escluso', 'b-stato-neutro'] };
  function peso(b) { return b < 1024 ? b + ' B' : b < 1048576 ? Math.round(b / 1024) + ' KB' : (b / 1048576).toFixed(1) + ' MB'; }
  function documentiFonte(f, p) {
    p.innerHTML = '<div class="sk-row"><span class="sk" style="width:60%"></span></div>';
    api('GET', '/fonti/' + encodeURIComponent(f.id) + '/documenti').then(function (d) {
      var l = d.documenti;
      if (d.provenienza !== 'cartella') { p.innerHTML = '<div class="notice notice-info">' + ic('info') + '<span>Questa fonte non è una cartella: i suoi contenuti arrivano da ' + esc(PROVENIENZA[d.provenienza] || d.provenienza) + ', non file per file.</span></div>'; return; }
      if (!l.length) { p.innerHTML = '<div class="notice notice-info">' + ic('info') + '<span>Nessun documento ancora. La cartella si legge ogni pochi minuti: se resta vuota, controlla il percorso e le anomalie.</span></div>'; return; }
      var n = { indicizzato: 0, errore: 0, vuoto: 0, escluso: 0 }, doppi = 0, senza = 0, pezzi = 0, inLettura = 0;
      /* Un file in lettura NON si conta fra i non leggibili: prima di leggerlo
         l'indicizzazione lo segna cosi' apposta (se il servizio muore a meta',
         al riavvio risulta illeggibile invece di ripartire in ciclo), ma
         finche' sta leggendo e' «in elaborazione», non un errore. */
      l.forEach(function (x) { if (x.in_lettura) inLettura++; else n[x.stato] = (n[x.stato] || 0) + 1; if (x.doppione) doppi++; if (x.senza_vettori) senza++; pezzi += x.pezzi; });
      p.innerHTML =
        '<p style="font-size:13.5px;margin-top:0">' + l.length + (l.length === 1 ? ' file' : ' file') + ', ' + pezzi + ' pezzi di testo. ' +
          (n.indicizzato ? badge(['ok', n.indicizzato + ' indicizzati', 'b-stato-ok']) + ' ' : '') +
          (n.errore ? badge(['err', n.errore + ' non leggibili', 'b-stato-errore']) + ' ' : '') +
          (n.vuoto ? badge(['neut', n.vuoto + ' senza testo', 'b-stato-neutro']) + ' ' : '') +
          (n.escluso ? badge(['neut', n.escluso + ' esclusi', 'b-stato-neutro']) + ' ' : '') +
          (doppi ? badge(['warn', doppi + ' doppioni', 'b-stato-attenzione']) + ' ' : '') +
          (senza ? badge(['warn', senza + ' senza vettori', 'b-stato-attenzione']) + ' ' : '') +
          (inLettura ? badge(['info', 'In elaborazione…', 'b-stato-info']) : '') + '</p>' +
        '<p class="ro-note">' + ic('info') + 'Non compaiono, di proposito, le sottocartelle <span class="mono">_bozze</span> e <span class="mono">_archivio</span>. I fogli di calcolo con i prezzi restano fuori («Escluso», con il motivo): i prezzi vengono dal gestionale.' +
          (senza ? ' «Senza vettori»: il servizio dei modelli non rispondeva; si completano da soli al giro dopo, intanto la ricerca testuale li trova.' : '') + '</p>' +
        '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">File</th><th scope="col">Stato</th><th scope="col" class="num-col">Pezzi</th><th scope="col">Elaborato</th>' +
        (can('fonti') ? '<th scope="col"></th>' : '') + '</tr></thead><tbody>' +
        l.map(function (x) {
          var el = '';
          if (x.in_lettura) {
            el = badge(['info', 'In elaborazione', 'b-stato-info']);
            if (x.pagine_totali) {
              var pc = Math.round(100 * x.pagine_fatte / x.pagine_totali);
              el += '<div style="margin-top:5px;width:120px;height:6px;background:var(--campo);border-radius:3px;overflow:hidden"><div style="width:' + pc + '%;height:100%;background:var(--marchio)"></div></div>' +
                    '<span class="sub" style="margin-top:2px">' + pc + '% · pagina ' + x.pagine_fatte + ' di ' + x.pagine_totali + '</span>';
            }
          }
          return '<tr><td class="mono nome-file">' + esc(x.documento) + '<span class="sub">' + peso(x.dimensione) + ' · file del ' + esc(dataOra(x.modificato_il)) + '</span>' +
            (x.errore && !x.in_lettura ? '<span class="sub"' + (x.stato === 'errore' ? ' style="color:var(--stato-critico)"' : '') + '>' + esc(x.errore) + '</span>' : '') + '</td>' +
            '<td>' + (el || badge(STATO_DOC[x.stato]) + (x.doppione ? ' ' + badge(['warn', 'Doppione', 'b-stato-attenzione']) : '') +
              (x.senza_vettori ? ' ' + badge(['warn', 'Senza vettori', 'b-stato-attenzione']) : '')) + '</td>' +
            '<td class="num-col">' + x.pezzi + '</td><td>' + esc(dataOra(x.indicizzato_il)) + '</td>' +
            (can('fonti') ? '<td><button class="btn btn-secondary btn-sm btn-rielabora" data-doc="' + esc(x.documento) + '"' +
              (x.in_lettura ? ' disabled' : '') + '>Rielabora</button></td>' : '') + '</tr>';
        }).join('') + '</tbody></table></div>';
      $$('#f-panel .btn-rielabora').forEach(function (b) { b.onclick = function () { rielaboraDocumento(f, this.dataset.doc, p); }; });
      if (inLettura) setTimeout(function () { if (document.body.contains(p)) documentiFonte(f, p); }, 4000);
    }).catch(function (e) { p.innerHTML = '<div class="notice notice-crit">' + ic('err') + '<span>' + esc(e.message) + '</span></div>'; });
  }
  function rielaboraDocumento(f, documento, p) {
    api('POST', '/fonti/' + encodeURIComponent(f.id) + '/documenti/rielabora', { documento: documento })
      .then(function () { toast('Da rielaborare al prossimo giro.'); documentiFonte(f, p); })
      .catch(function (e) { toast(e.message, 'err'); });
  }
  function apriFonte(id) {
    var mostra = function (f) {
      var ro = !f.modificabile;
      var corpo = '<div class="tabs" role="tablist">' + [['info', 'Informazioni'], ['doc', 'Documenti'], ['carica', 'Carica documenti']].map(function (t, i) {
        return '<button class="tab' + (i ? '' : ' active') + '" role="tab" aria-selected="' + (i ? 'false' : 'true') + '" data-t="' + t[0] + '">' + t[1] + '</button>'; }).join('') + '</div><div id="f-panel"></div>';
      apriPannello('<div><h2 id="drawer-titolo">' + esc(f.descrizione) + '</h2><div class="sub">' + (f.tipo === 'documenti' ? 'Documenti' : 'Dati gestionali') + ' · ' + esc(PROVENIENZA[f.provenienza] || f.provenienza) + '</div></div>', corpo);
      function tab(t) {
        var p = $('#f-panel');
        if (t === 'info') {
          p.innerHTML = (ro ? '' : '<div><button class="btn btn-secondary btn-sm" id="btn-modifica">Modifica gruppi, aziende, responsabile e stato</button></div>') +
            '<dl class="dl">' +
            '<dt>Chi può vederla</dt><dd>' + esc(f.acl_groups.join(', ')) + '</dd>' +
            '<dt>Aziende</dt><dd>' + chipAz(f.aziende) + '</dd>' +
            '<dt>Responsabile</dt><dd>' + (f.owner && f.owner !== 'da-assegnare' ? esc(f.owner) : '<span style="color:var(--stato-attenzione)">' + ic('warn') + ' Nessuno: la fonte non può essere attivata</span>') + '</dd>' +
            '<dt>Versione di riferimento</dt><dd>' + esc(f.versione_autoritativa || '—') + '</dd>' +
            '<dt>Uso di servizi esterni</dt><dd>' + resBadge(f.residency) +
              (f.residency === 'cloud_ok' && f.approvato_da ? '<div class="ro-note" style="margin-top:6px">Approvato da ' + esc(f.approvato_da) + ' il ' + esc(dataOra(f.approvato_il)) + '</div>' : '') +
              '<div style="margin-top:8px">' + (ro
                ? (may('fonti') ? '<span class="ro-note">' + ic('lock') + (can('fonti') ? 'Riguarda aziende a cui non sei abilitato' : 'Modifica solo con il ruolo Gestione fonti') + '</span>' : '')
                : '<button class="btn btn-secondary btn-sm" id="btn-res">' + (f.residency === 'interno' ? 'Rendi utilizzabile da servizi esterni' : 'Riporta a «Resta in azienda»') + '</button>') + '</div></dd>' +
            '<dt>Provenienza</dt><dd>' + esc(PROVENIENZA[f.provenienza] || f.provenienza) + '<div class="mono" style="font-size:12.5px;margin-top:3px">' + esc(f.percorso) + '</div></dd>' +
            '<dt>Contenuto</dt><dd>' + (f.tipo === 'documenti' ? (f.documenti ? f.documenti + ' documenti · ' + f.pagine + ' pagine' : 'Nessun documento indicizzato') : 'Dati del gestionale') + '</dd>' +
            '<dt>Ultimo aggiornamento</dt><dd>' + quando(f.aggiornata) + '</dd>' +
            '<dt>Stato</dt><dd>' + badge(STATO_FONTE[f.stato]) + '</dd></dl>';
          var b = $('#btn-res'); if (b) b.onclick = function () { cambiaResidenza(f); };
          var bm = $('#btn-modifica'); if (bm) bm.onclick = function () { modificaFonte(f); };
        } else if (t === 'doc') {
          documentiFonte(f, p);
        } else {
          p.innerHTML = '<div class="notice notice-info">' + ic('info') + '<span><b>Seconda fase.</b> Il caricamento manuale serve alle fonti senza una cartella collegata. Oggi i contenuti arrivano solo dalle cartelle collegate.</span></div>';
        }
      }
      tab('info');
      $$('#drawer .tab').forEach(function (b) { b.onclick = function () { $$('#drawer .tab').forEach(function (x) { x.classList.toggle('active', x === b); x.setAttribute('aria-selected', x === b ? 'true' : 'false'); }); tab(b.dataset.t); }; });
    };
    var f = FONTI.filter(function (x) { return x.id === id; })[0];
    if (f) mostra(f); else api('GET', '/fonti').then(function (d) { FONTI = d; var g = d.filter(function (x) { return x.id === id; })[0]; if (g) mostra(g); });
  }
  function modificaFonte(f) {
    api('GET', '/gruppi').then(function (gruppi) {
      function caselle(nome, opzioni, scelti, etichetta) {
        return '<fieldset class="field" style="border:0;padding:0;margin:0 0 12px"><legend style="font-size:12px;font-weight:600;color:var(--testo-tenue);margin-bottom:6px">' + etichetta + '</legend>' +
          '<div style="display:flex;flex-wrap:wrap;gap:8px 16px">' + opzioni.map(function (o) {
            return '<label class="check-row"><input type="checkbox" name="' + nome + '" value="' + esc(o[0]) + '"' + (scelti.indexOf(o[0]) > -1 ? ' checked' : '') + '> ' + esc(o[1]) + '</label>'; }).join('') + '</div></fieldset>';
      }
      var mieAz = AZIENDE.map(function (a) { return [a.codice, a.ragione_sociale]; });
      dialogo('Modifica «' + f.descrizione + '»',
        caselle('g', gruppi.map(function (g) { return [g, g]; }), f.acl_groups, 'Chi può vederla (almeno un gruppo)') +
        caselle('a', mieAz, f.aziende, 'Aziende (almeno una)') +
        '<div class="field" style="margin-bottom:12px"><label for="m-owner">Responsabile</label><input class="input" id="m-owner" value="' + esc(f.owner === 'da-assegnare' ? '' : f.owner) + '" placeholder="Es. Ufficio tecnico"></div>' +
        '<div class="field" style="margin-bottom:12px"><label for="m-vers">Versione di riferimento</label><input class="input" id="m-vers" value="' + esc(f.versione_autoritativa || '') + '" aria-describedby="m-vers-h" placeholder="Es. listino 2026, cartella corrente"><span class="hint" id="m-vers-h">Quale versione vale: senza, la fonte non si attiva.</span></div>' +
        '<div class="field" style="margin-bottom:12px"><label for="m-stato">Stato</label><select class="select" id="m-stato"><option value="attiva">Attiva</option><option value="attesa">In attesa di approvazione</option><option value="sospesa">Sospesa</option></select></div>' +
        '<div class="field"><label for="m-mot">Motivo (obbligatorio per sospendere)</label><textarea class="textarea" id="m-mot"></textarea></div>',
        function () {
          var val = function (n) { return $$('#modal-body input[name=' + n + ']:checked').map(function (x) { return x.value; }); };
          return api('PATCH', '/fonti/' + encodeURIComponent(f.id), { gruppi: val('g'), aziende: val('a'), owner: $('#m-owner').value, versione: $('#m-vers').value, stato: $('#m-stato').value, motivo: $('#m-mot').value })
            .then(function () { toast('Fonte aggiornata'); return api('GET', '/fonti'); })
            .then(function (d) { FONTI = d; if (location.hash.indexOf('fonti') > -1) disegnaFonti(); setTimeout(function () { apriFonte(f.id); }, 0); })
            .catch(function (e) { toast(e.message, 'err'); return false; });
        }, 'Salva');
      $('#m-stato').value = f.stato;
    }).catch(function (e) { toast(e.message, 'err'); });
  }
  function cambiaResidenza(f) {
    if (f.residency === 'cloud_ok') {
      api('POST', '/fonti/' + encodeURIComponent(f.id) + '/residenza', { residenza: 'interno' })
        .then(function () { toast('Fonte riportata a «Resta in azienda»'); return api('GET', '/fonti'); })
        .then(function (d) { FONTI = d; if (location.hash.indexOf('fonti') > -1) disegnaFonti(); apriFonte(f.id); })
        .catch(function (e) { toast(e.message, 'err'); });
      return;
    }
    dialogo('Rendere la fonte utilizzabile da servizi esterni',
      '<div class="notice notice-warn">' + ic('warn') + '<span><b>Cosa succede.</b> I contenuti di «' + esc(f.descrizione) + '» potranno essere inviati a un servizio di intelligenza artificiale esterno all\'azienda per elaborare le risposte.</span></div>' +
      '<div class="field" style="margin-top:14px"><label for="m-motivo">Motivo (obbligatorio)</label><textarea class="textarea" id="m-motivo" aria-describedby="m-motivo-h" placeholder="Es. accordo con il fornitore: i listini possono uscire."></textarea><span class="hint" id="m-motivo-h">Il motivo, il tuo nome e la data restano sulla fonte e nel registro modifiche.</span></div>' +
      '<div class="field" style="margin-top:10px"><span style="font-size:12px;font-weight:600;color:var(--testo-tenue)">Approvato da</span><div class="mono" style="font-size:13px">' + esc(IO.nome) + ' · ' + esc(new Date().toLocaleDateString('it-IT')) + '</div></div>',
      function () {
        var m = $('#m-motivo').value.trim();
        if (!m) { toast('Il motivo è obbligatorio.', 'err'); $('#m-motivo').focus(); return false; }
        return api('POST', '/fonti/' + encodeURIComponent(f.id) + '/residenza', { residenza: 'cloud_ok', motivo: m })
          .then(function () { toast('Fonte approvata per i servizi esterni'); return api('GET', '/fonti'); })
          .then(function (d) { FONTI = d; if (location.hash.indexOf('fonti') > -1) disegnaFonti(); setTimeout(function () { apriFonte(f.id); }, 0); })
          .catch(function (e) { toast(e.message, 'err'); return false; });
      }, 'Approva');
  }

  /* ============================== VEDI COME (§8) ============================== */
  function renderVediCome() {
    var c = $('#content');
    c.innerHTML = schermata('Vedi come', 'Che cosa vede una persona, senza entrare con le sue credenziali.', '',
      '<div class="notice notice-info" style="margin-bottom:14px;max-width:760px">' + ic('info') + '<span>Questa consultazione viene registrata nel registro modifiche.</span></div>' +
      '<div class="card pad-4"><div class="field search-box" style="max-width:520px"><label class="sr" for="vc-q">Cerca una persona</label>' + ic('search') + '<input class="input" id="vc-q" placeholder="Cerca per nome, utente o email…"></div></div>' +
      '<div id="vc-list" style="margin-top:16px"></div><div id="vc-detail"></div>');
    var t; function cerca(q) {
      var l = $('#vc-list'); caricamento(l);
      api('GET', '/vedi-come/utenti?q=' + encodeURIComponent(q)).then(function (u) {
        if (!u.length) { l.innerHTML = vuoto('Nessuna persona trovata', 'Prova con un altro nome o con l\'email.'); return; }
        l.innerHTML = '<div class="card"><div class="table-box"><table class="tbl"><thead><tr><th scope="col">Persona</th><th scope="col">Utente</th><th scope="col">Email</th><th scope="col">Stato</th></tr></thead><tbody>' +
          u.map(function (x) { return '<tr data-apri="' + esc(x.id) + '"><td class="prim">' + esc(x.nome || x.username) + '</td><td class="mono">' + esc(x.username) + '</td><td class="mono" style="color:var(--testo-tenue)">' + esc(x.email) + '</td><td>' + badge(x.attivo ? ['ok', 'Attivo', 'b-stato-ok'] : ['neut', 'Disattivato', 'b-stato-neutro']) + '</td></tr>'; }).join('') +
          '</tbody></table></div></div>';
        righeAttive(l, dettaglio);
      }).catch(function (e) { errore(l, e, function () { cerca(q); }); });
    }
    function dettaglio(uid) {
      var d = $('#vc-detail'); caricamento(d);
      api('GET', '/vedi-come/utenti/' + encodeURIComponent(uid)).then(function (r) {
        var u = r.utente, intro = '';
        if (!u.attivo) intro = '<div class="notice notice-crit">' + ic('crit') + '<span><b>Non vede nessun dato:</b> la persona è disattivata.</span></div>';
        else if (!r.aziende.length) intro = '<div class="notice notice-crit">' + ic('crit') + '<span><b>Nessuna azienda abilitata:</b> non vede nessun dato.</span></div>';
        else if (!r.gruppi.filter(function (g) { return g !== 'tutti'; }).length) intro = '<div class="notice notice-warn">' + ic('warn') + '<span><b>Vede solo le fonti aperte a tutti:</b> nessun gruppo operativo.</span></div>';
        var avvisi = r.visibili.some(function (f) { return f.avvisi.length; });
        d.innerHTML = '<div class="card pad-5" style="margin-top:16px"><div class="page-head" style="margin-bottom:14px"><div><h2 style="font-size:15px;font-weight:600;margin:0">' + esc(u.nome) + ' <span class="chipacloud">' + esc(u.email || u.username) + '</span></h2>' +
          '<p class="page-sub">Gruppi: ' + esc(r.gruppi.join(', ') || 'nessuno') + ' · Aziende: ' + (r.aziende.length ? r.aziende.map(nomeAz).map(esc).join(', ') : 'nessuna') + '</p></div>' +
          (may('utenti') ? '<a class="btn btn-secondary btn-sm" href="#/utenti">' + ic('user') + 'Apri in Utenti</a>' : '') + '</div>' + intro +
          '<h3 style="font-size:12.5px;color:var(--testo-debole);text-transform:uppercase;letter-spacing:.05em;margin:18px 0 8px">Fonti visibili (' + r.visibili.length + ')</h3>' +
          '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Fonte</th><th scope="col">Tipo</th><th scope="col">Aziende</th><th scope="col">Uso di servizi esterni</th></tr></thead><tbody>' +
          (r.visibili.map(function (f) { return '<tr><td class="prim">' + esc(f.descrizione) + f.avvisi.map(function (a) { return '<div class="avviso-riga">' + ic('warn') + '<span>' + esc(a) + '</span></div>'; }).join('') + '</td><td>' + (f.tipo === 'documenti' ? 'Documenti' : 'Dati gestionali') + '</td><td>' + chipAz(f.aziende) + '</td><td>' + resBadge(f.residency) + '</td></tr>'; }).join('') || '<tr><td colspan="4" class="empty-note">Nessuna fonte visibile.</td></tr>') +
          '</tbody></table></div>' +
          '<h3 style="font-size:12.5px;color:var(--testo-debole);text-transform:uppercase;letter-spacing:.05em;margin:18px 0 8px">Fonti NON visibili (' + r.non_visibili.length + ')</h3>' +
          '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Fonte</th><th scope="col">Perché non la vede</th></tr></thead><tbody>' +
          (r.non_visibili.map(function (f) { return '<tr><td class="prim">' + esc(f.descrizione) + '</td><td style="color:var(--testo-tenue)">' + esc(f.motivo) + '</td></tr>'; }).join('') || '<tr><td colspan="2" class="empty-note">Nessuna.</td></tr>') +
          '</tbody></table></div><p class="ro-note" style="margin-top:12px">' + ic('info') + 'Consultazione registrata nel registro modifiche.</p></div>';
        d.querySelector('h2').setAttribute('tabindex', '-1'); d.querySelector('h2').focus();
      }).catch(function (e) { errore(d, e, function () { dettaglio(uid); }); });
    }
    $('#vc-q').oninput = function () { clearTimeout(t); var v = this.value; t = setTimeout(function () { cerca(v); }, 250); };
    if (VC_PRESELEZIONE) { $('#vc-q').value = VC_PRESELEZIONE; cerca(VC_PRESELEZIONE); VC_PRESELEZIONE = ''; } else cerca('');
  }

  /* ============================== ANOMALIE (§9) ============================== */
  var AF = { q: '', grav: '', sist: '', stato: 'attive', me: false }, AS = { k: 'gravita', asc: true }, AP = { n: 1, per: 12 }, ANOM = [];
  var RANGO = { critico: 0, errore: 1, attenzione: 2, info: 3 };
  function renderAnomalie() {
    var c = $('#content'); caricamento(c);
    api('GET', '/anomalie').then(function (d) { ANOM = d; contatore(); disegnaAnomalie(); }).catch(function (e) { errore(c, e, renderAnomalie); });
  }
  function contatore() {
    var n = ANOM.filter(function (a) { return a.stato === 'aperta' || a.stato === 'presa'; }).length, el = $('#cnt-anomalie');
    el.textContent = n; el.className = 'cnt' + (n ? '' : ' zero'); el.setAttribute('aria-label', n + ' anomalie aperte');
  }
  function disegnaAnomalie() {
    var c = $('#content');
    var arr = ANOM.filter(function (a) {
      if (!inAzienda(a.azienda)) return false;
      if (AF.q && a.titolo.toLowerCase().indexOf(AF.q.toLowerCase()) < 0) return false;
      if (AF.grav && a.gravita !== AF.grav) return false;
      if (AF.sist && a.sistema !== AF.sist) return false;
      if (AF.stato === 'attive' && a.stato !== 'aperta' && a.stato !== 'presa') return false;
      if (AF.stato && AF.stato !== 'attive' && a.stato !== AF.stato) return false;
      if (AF.me && a.assegnata_a !== IO.username) return false;
      return true;
    });
    arr = ordina(arr, AS, function (a, k) { return k === 'gravita' ? RANGO[a.gravita] * 1e13 - new Date(a.ultima).getTime() / 1000 : k === 'occorrenze' ? a.occorrenze : k === 'ultima' ? a.ultima : String(a[k]); });
    var tot = arr.length, pag = arr.slice((AP.n - 1) * AP.per, AP.n * AP.per);
    c.innerHTML = schermata('Anomalie', 'Ogni riga è un problema, non un evento: se si ripete 200 volte resta una riga con 200 occorrenze.',
      can('anomalie') ? '' : roNota('Gestione anomalie'),
      '<div class="card"><div class="filters">' +
      '<div class="field search-box grow"><label class="sr" for="a-q">Cerca</label>' + ic('search') + '<input class="input" id="a-q" placeholder="Cerca nel titolo…" value="' + esc(AF.q) + '"></div>' +
      '<div class="field"><label for="a-grav">Gravità</label><select class="select" id="a-grav"><option value="">Tutte</option><option value="critico">Critiche</option><option value="errore">Errori</option><option value="attenzione">Avvisi</option><option value="info">Informative</option></select></div>' +
      '<div class="field"><label for="a-sist">Sistema</label><select class="select" id="a-sist"><option value="">Tutti</option>' + Object.keys(SISTEMI).map(function (k) { return '<option value="' + k + '">' + SISTEMI[k] + '</option>'; }).join('') + '</select></div>' +
      '<div class="field"><label for="a-stato">Stato</label><select class="select" id="a-stato"><option value="attive">Aperte e prese in carico</option><option value="">Tutte</option><option value="aperta">Aperte</option><option value="presa">Prese in carico</option><option value="risolta">Risolte</option><option value="ignorata">Ignorate</option></select></div>' +
      '<div class="field"><label class="check-row" style="font-weight:400"><input type="checkbox" id="a-me" ' + (AF.me ? 'checked' : '') + '/> Assegnate a me</label></div>' +
      '</div><div class="res-meta" aria-live="polite">' + tot + (tot === 1 ? ' anomalia' : ' anomalie') + '</div>' +
      (ANOM.length ? '' : '<div class="notice notice-ok" style="margin:10px 18px 0">' + ic('ok') + '<span>Nessuna anomalia registrata nelle tue aree.</span></div>') +
      '<div class="table-box"><table class="tbl"><thead><tr>' +
      '<th scope="col" class="sortable" data-k="gravita">Gravità' + freccia(AS, 'gravita') + '</th><th scope="col">Titolo</th><th scope="col">Sistema</th><th scope="col">Azienda</th>' +
      '<th scope="col" class="num-col sortable" data-k="occorrenze">Occorrenze' + freccia(AS, 'occorrenze') + '</th><th scope="col">Prima volta</th><th scope="col" class="sortable" data-k="ultima">Ultima volta' + freccia(AS, 'ultima') + '</th><th scope="col">Stato</th><th scope="col">Assegnata a</th></tr></thead><tbody>' +
      pag.map(function (a) {
        return '<tr data-apri="' + a.id + '"><td>' + badge(GRAVITA[a.gravita]) + '</td><td class="prim">' + esc(a.titolo) + '</td><td>' + esc(SISTEMI[a.sistema]) + '</td><td>' + chipAz(a.azienda) + '</td>' +
          '<td class="num-col">' + a.occorrenze + '</td><td>' + quando(a.prima) + '</td><td>' + quando(a.ultima) + '</td><td>' + bAnom(a) + '</td><td>' + esc(a.assegnata_a || '—') + '</td></tr>';
      }).join('') + '</tbody></table>' + (tot || !ANOM.length ? '' : '<div class="empty-note">Nessun problema corrisponde ai filtri.</div>') + '</div><div class="pager" id="a-pager"></div></div>');
    $('#a-grav').value = AF.grav; $('#a-sist').value = AF.sist; $('#a-stato').value = AF.stato;
    var t; $('#a-q').oninput = function () { clearTimeout(t); var v = this.value; t = setTimeout(function () { AF.q = v; AP.n = 1; disegnaAnomalie(); var i = $('#a-q'); i.focus(); i.setSelectionRange(v.length, v.length); }, 250); };
    ['grav', 'sist', 'stato'].forEach(function (k) { $('#a-' + k).onchange = function () { AF[k] = this.value; AP.n = 1; disegnaAnomalie(); $('#a-' + k).focus(); }; });
    $('#a-me').onchange = function () { AF.me = this.checked; AP.n = 1; disegnaAnomalie(); $('#a-me').focus(); };
    ordinabili(c, AS, disegnaAnomalie);
    paginatore($('#a-pager'), AP, tot, disegnaAnomalie);
    righeAttive(c, apriAnomalia);
  }
  var EVENTO = { occorrenza: 'nuova occorrenza', presa: 'presa in carico', assegnata: 'assegnata a', commento: 'commento', risolta: 'risolta',
                 ignorata: 'ignorata', riaperta: 'riaperta: il problema si è ripresentato', risolta_auto: 'risolta automaticamente: il controllo è tornato a passare' };
  function apriAnomalia(id) {
    api('GET', '/anomalie/' + id).then(function (a) {
      var mod = can('anomalie'), attiva = a.stato === 'aperta' || a.stato === 'presa';
      var coll = '';
      if (a.oggetto && a.oggetto.indexOf('fonte:') === 0 && may('fonti')) coll = '<button class="btn btn-ghost btn-sm" id="da-fonte">Vedi la fonte</button>';
      else if (a.oggetto && a.oggetto.indexOf('importazione:') === 0 && may('dagster')) coll = '<a class="btn btn-ghost btn-sm" href="#/esterno/dagster">' + ic('ext') + 'Gestione importazioni</a>';
      else if (a.oggetto && a.oggetto.indexOf('servizio:') === 0 && may('uptime')) coll = '<a class="btn btn-ghost btn-sm" href="#/esterno/uptime">' + ic('ext') + 'Monitoraggio sistemi</a>';
      else if (a.sistema === 'accessi' && may('utenti')) coll = '<a class="btn btn-ghost btn-sm" href="#/utenti">' + ic('user') + 'Utenti</a>';
      apriPannello('<div><h2 id="drawer-titolo">' + esc(a.titolo) + '</h2><div class="sub">' + esc(SISTEMI[a.sistema]) + ' · ' + (a.azienda ? esc(nomeAz(a.azienda)) : 'nessuna azienda') + ' · ' + a.occorrenze + (a.occorrenze === 1 ? ' occorrenza' : ' occorrenze') + '</div></div>',
        '<dl class="dl"><dt>Gravità e stato</dt><dd>' + badge(GRAVITA[a.gravita]) + ' ' + bAnom(a) + (a.stato === 'ignorata' ? ' <span class="ro-note">' + (a.ignorata_fino ? 'fino al ' + esc(dataOra(a.ignorata_fino)) : 'per sempre') + '</span>' : '') + '</dd>' +
        '<dt>Cosa significa e cosa fare</dt><dd>' + esc(a.cosa_fare || 'Nessuna indicazione disponibile.') + '</dd>' +
        '<dt>Collegamenti</dt><dd>' + (coll || '<span class="ro-note">Nessun collegamento diretto</span>') + '</dd>' +
        '<dt>Prima e ultima volta</dt><dd>' + esc(dataOra(a.prima)) + ' · ' + esc(dataOra(a.ultima)) + '</dd>' +
        '<dt>Dettaglio tecnico</dt><dd><details><summary style="cursor:pointer;font-size:12.5px;color:var(--testo-tenue)">Mostra i dati tecnici</summary><pre class="mono" style="font-size:11.5px;background:var(--superficie-alt);padding:10px;border-radius:var(--raggio);overflow:auto;white-space:pre-wrap">' + esc(JSON.stringify(a.dettaglio, null, 2)) + '</pre></details></dd>' +
        (mod ? '<dt>Azioni</dt><dd><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">' +
          (attiva && a.assegnata_a !== IO.username ? '<button class="btn btn-primary btn-sm" data-az="prendi">Prendi in carico</button>' : '') +
          '<button class="btn btn-secondary btn-sm" data-az="assegna">Assegna…</button><button class="btn btn-secondary btn-sm" data-az="commenta">Commenta</button>' +
          (a.stato !== 'risolta' ? '<button class="btn btn-secondary btn-sm" data-az="risolvi">Risolvi</button>' : '') +
          (a.stato !== 'ignorata' ? '<button class="btn btn-ghost btn-sm" data-az="ignora">Ignora…</button>' : '') + '</div></dd>' : '') +
        '<dt>Cronologia</dt><dd><ul class="storia">' + a.eventi.map(function (e) {
          return '<li><b>' + esc(dataOra(e.quando)) + '</b> · ' + esc(e.chi) + ' — ' + esc(EVENTO[e.tipo] || e.tipo) + (e.testo ? ': ' + esc(e.testo) : '') + '</li>'; }).join('') + '</ul></dd></dl>');
      var bf = $('#da-fonte'); if (bf) bf.onclick = function () { chiudiPannello(); apriFonte(a.oggetto.slice(6)); };
      function fai(corpo, messaggio) {
        return api('POST', '/anomalie/' + a.id + '/azione', corpo).then(function () { toast(messaggio); apriAnomalia(a.id); if (location.hash.indexOf('anomalie') > -1) renderAnomalie(); else api('GET', '/anomalie').then(function (d) { ANOM = d; contatore(); }); })
          .catch(function (e) { toast(e.message, 'err'); return false; });
      }
      $$('#drawer [data-az]').forEach(function (b) {
        b.onclick = function () {
          var az = b.dataset.az;
          if (az === 'prendi') fai({ azione: 'prendi' }, 'Anomalia presa in carico');
          else if (az === 'assegna') api('GET', '/assegnabili').then(function (lista) {
            dialogo('Assegna l\'anomalia', '<div class="field"><label for="m-a">A chi (utente)</label><input class="input" id="m-a" list="m-a-lista" autocomplete="off"><datalist id="m-a-lista">' + lista.map(function (x) { return '<option value="' + esc(x) + '">'; }).join('') + '</datalist></div>',
              function () { var v = $('#m-a').value.trim(); if (!v) { toast('Indica a chi assegnarla.', 'err'); return false; } return fai({ azione: 'assegna', a: v }, 'Anomalia assegnata a ' + v); }, 'Assegna');
          });
          else if (az === 'commenta') dialogo('Commenta', '<div class="field"><label for="m-c">Commento</label><textarea class="textarea" id="m-c"></textarea></div>',
            function () { var v = $('#m-c').value.trim(); if (!v) { toast('Il commento è vuoto.', 'err'); return false; } return fai({ azione: 'commenta', testo: v }, 'Commento registrato'); }, 'Pubblica');
          else if (az === 'risolvi') dialogo('Risolvi l\'anomalia', '<div class="field"><label for="m-n">Nota (facoltativa)</label><textarea class="textarea" id="m-n" placeholder="Es. connettore riavviato, prossima importazione attesa alle 14."></textarea></div>',
            function () { return fai({ azione: 'risolvi', testo: $('#m-n').value.trim() }, 'Anomalia risolta'); }, 'Risolvi');
          else dialogo('Ignorare l\'anomalia', '<div class="notice notice-warn">' + ic('warn') + '<span>Smette di comparire tra le aperte. Se si ripresenta dopo la scadenza, torna aperta.</span></div>' +
            '<div class="field" style="margin-top:12px"><label for="m-f">Fino a quando</label><select class="select" id="m-f"><option value="1g">Per 1 giorno</option><option value="7g" selected>Per 7 giorni</option><option value="30g">Per 30 giorni</option><option value="sempre">Per sempre</option></select></div>' +
            '<div class="field" style="margin-top:10px"><label for="m-m">Motivo (obbligatorio)</label><textarea class="textarea" id="m-m" placeholder="Es. differenza nota con il fornitore, non bloccante."></textarea></div>',
            function () { var m = $('#m-m').value.trim(); if (!m) { toast('Il motivo è obbligatorio.', 'err'); $('#m-m').focus(); return false; } return fai({ azione: 'ignora', fino: $('#m-f').value, testo: m }, 'Anomalia ignorata'); }, 'Ignora');
        };
      });
    }).catch(function (e) { toast(e.message, 'err'); });
  }

  /* ============================== REGISTRO (§10) ============================== */
  var RF = { q: '', area: '' }, RP = { n: 1, per: 15 }, REG = [];
  var AREE = { fonti: 'Fonti', anomalie: 'Anomalie', aspetto: 'Aspetto', 'vedi-come': 'Vedi come', accessi: 'Accessi', gestionali: 'Gestionali' };
  function renderRegistro() {
    var c = $('#content'); caricamento(c);
    api('GET', '/registro').then(function (d) { REG = d; disegnaRegistro(); }).catch(function (e) { errore(c, e, renderRegistro); });
  }
  function disegnaRegistro() {
    var c = $('#content');
    var arr = REG.filter(function (r) {
      if (!inAzienda(r.azienda)) return false;
      if (RF.area && r.area !== RF.area) return false;
      if (RF.q && (r.azione + ' ' + (r.chi_nome || r.chi) + ' ' + (r.oggetto || '')).toLowerCase().indexOf(RF.q.toLowerCase()) < 0) return false;
      return true;
    });
    var tot = arr.length, pag = arr.slice((RP.n - 1) * RP.per, RP.n * RP.per);
    c.innerHTML = schermata('Registro modifiche', 'Chi ha cambiato cosa, quando e perché. Nessuno può modificarlo o cancellarlo.',
      '<a class="btn btn-secondary" href="' + BASE + '/api/registro.csv" download>' + ic('csv') + 'Esporta CSV</a>',
      (REG.length ? '' : vuoto('Nessuna modifica registrata', 'Qui compariranno le modifiche fatte in amministrazione.')) +
      '<div class="card"' + (REG.length ? '' : ' hidden') + '><div class="filters">' +
      '<div class="field search-box grow"><label class="sr" for="r-q">Cerca</label>' + ic('search') + '<input class="input" id="r-q" placeholder="Cerca persona, oggetto, azione…" value="' + esc(RF.q) + '"></div>' +
      '<div class="field"><label for="r-area">Area</label><select class="select" id="r-area"><option value="">Tutte</option>' + Object.keys(AREE).map(function (k) { return '<option value="' + k + '">' + AREE[k] + '</option>'; }).join('') + '</select></div>' +
      '</div><div class="res-meta">' + tot + (tot === 1 ? ' modifica' : ' modifiche') + '' + '</div>' +
      '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Quando</th><th scope="col">Chi</th><th scope="col">Area</th><th scope="col">Azione</th><th scope="col">Oggetto</th><th scope="col">Azienda</th><th scope="col">Motivo</th></tr></thead><tbody>' +
      pag.map(function (r) {
        return '<tr data-apri="' + r.id + '"><td class="mono" style="color:var(--testo-tenue);white-space:nowrap">' + esc(dataOra(r.quando)) + '</td><td>' + esc(r.chi_nome || r.chi) + '</td><td>' + esc(AREE[r.area]) + '</td><td>' + esc(r.azione) + '</td><td>' + esc(r.oggetto || '—') + '</td><td>' + chipAz(r.azienda) + '</td><td style="color:var(--testo-debole)">' + esc(r.motivo ? (r.motivo.length > 50 ? r.motivo.slice(0, 50) + '…' : r.motivo) : '—') + '</td></tr>';
      }).join('') + '</tbody></table>' + (tot ? '' : '<div class="empty-note">Nessuna modifica corrisponde ai filtri.</div>') + '</div><div class="pager" id="r-pager"></div></div>');
    if (!REG.length) return;
    $('#r-area').value = RF.area;
    var t; $('#r-q').oninput = function () { clearTimeout(t); var v = this.value; t = setTimeout(function () { RF.q = v; RP.n = 1; disegnaRegistro(); var i = $('#r-q'); i.focus(); i.setSelectionRange(v.length, v.length); }, 250); };
    $('#r-area').onchange = function () { RF.area = this.value; RP.n = 1; disegnaRegistro(); $('#r-area').focus(); };
    paginatore($('#r-pager'), RP, tot, disegnaRegistro);
    righeAttive(c, apriRegistro);
  }
  function apriRegistro(id) {
    var mostra = function (r) {
      function blocco(o) { return o == null ? '—' : esc(JSON.stringify(o, null, 2)); }
      apriPannello('<div><h2 id="drawer-titolo">Dettaglio della modifica</h2><div class="sub">' + esc(AREE[r.area]) + '</div></div>',
        '<dl class="dl"><dt>Chi e quando</dt><dd>' + esc(r.chi_nome || r.chi) + ' (' + esc(r.chi) + ') · ' + esc(dataOra(r.quando)) + '</dd>' +
        '<dt>Azione</dt><dd>' + esc(r.azione) + '</dd>' + (r.oggetto ? '<dt>Oggetto</dt><dd>' + esc(r.oggetto) + '</dd>' : '') +
        '<dt>Motivo</dt><dd>' + esc(r.motivo || '—') + '</dd>' +
        (r.prima != null || r.dopo != null ? '<dt>Prima e dopo</dt><dd><div class="diff"><div><div class="ro-note">Prima</div><pre class="prima">' + blocco(r.prima) + '</pre></div><div><div class="ro-note">Dopo</div><pre class="dopo">' + blocco(r.dopo) + '</pre></div></div></dd>' : '') + '</dl>');
    };
    var r = REG.filter(function (x) { return String(x.id) === String(id); })[0];
    if (r) mostra(r); else api('GET', '/registro').then(function (d) { REG = d; var g = d.filter(function (x) { return String(x.id) === String(id); })[0]; if (g) mostra(g); });
  }

  /* ============================== ACCESSI (decisione 68) ==============================
     Interfaccia nostra, funzioni di Keycloak: il server chiama Keycloak con il
     token di chi lavora, quindi un pulsante mostrato per errore non concede
     nulla (Keycloak risponde 403 e lo si dice). */
  var UF = { q: '', gruppo: '', azienda: '', stato: '', senza: false }, UP = { n: 1, per: 15 }, UT = [], GRUPPI_OP = [];
  function passwordMostrata(pw, chi) {
    dialogo('Password temporanea',
      '<div class="notice notice-warn">' + ic('warn') + '<span>Comunicala a <b>' + esc(chi) + '</b> di persona o per telefono. Si vede <b>solo ora</b>; al primo accesso verrà chiesto di cambiarla.</span></div>' +
      '<div class="field" style="margin-top:14px"><label for="m-pw">Password</label><input class="input mono" id="m-pw" readonly value="' + esc(pw) + '"></div>',
      function () {}, 'Ho preso nota');
    $('#modal-cancel').hidden = true;
    var i = $('#m-pw'); i.focus(); i.select();
    var chiudi = $('#modal-ok').onclick; $('#modal-ok').onclick = function () { $('#modal-cancel').hidden = false; chiudi(); };
  }
  function renderUtenti() {
    var c = $('#content'); caricamento(c);
    Promise.all([api('GET', '/utenti'), may('gruppi') ? api('GET', '/gruppi-operativi') : Promise.resolve([])])
      .then(function (r) { UT = r[0]; GRUPPI_OP = r[1]; disegnaUtenti(); }).catch(function (e) { errore(c, e, renderUtenti); });
  }
  function disegnaUtenti() {
    var c = $('#content');
    var arr = UT.filter(function (u) {
      if (AZ && u.aziende.indexOf(AZ) < 0 && u.aziende.length) return false;
      if (UF.q && (u.nome + ' ' + u.username + ' ' + u.email).toLowerCase().indexOf(UF.q.toLowerCase()) < 0) return false;
      if (UF.gruppo && u.gruppi.indexOf(UF.gruppo) < 0) return false;
      if (UF.stato === 'attivo' && !u.attivo) return false;
      if (UF.stato === 'disattivato' && u.attivo) return false;
      if (UF.stato === 'admin' && !u.profili.length) return false;
      if (UF.senza && u.aziende.length) return false;
      return true;
    });
    var tot = arr.length, pag = arr.slice((UP.n - 1) * UP.per, UP.n * UP.per);
    c.innerHTML = schermata('Utenti', 'Le persone che accedono all\'assistente: gruppi, aziende abilitate e profili di amministrazione.',
      can('utenti') ? '<button class="btn btn-primary" id="u-nuovo">' + ic('plus') + 'Nuovo utente</button>' : roNota('Gestione accessi'),
      '<div class="card"><div class="filters">' +
      '<div class="field search-box grow"><label class="sr" for="u-q">Cerca</label>' + ic('search') + '<input class="input" id="u-q" placeholder="Cerca nome, utente o email…" value="' + esc(UF.q) + '"></div>' +
      '<div class="field"><label for="u-g">Gruppo</label><select class="select" id="u-g"><option value="">Tutti</option>' + GRUPPI_OP.map(function (g) { return '<option>' + esc(g.nome) + '</option>'; }).join('') + '</select></div>' +
      '<div class="field"><label for="u-s">Stato</label><select class="select" id="u-s"><option value="">Tutti</option><option value="attivo">Attivi</option><option value="disattivato">Disattivati</option><option value="admin">Con profili di amministrazione</option></select></div>' +
      '<div class="field"><label class="check-row" style="font-weight:400"><input type="checkbox" id="u-senza" ' + (UF.senza ? 'checked' : '') + '/> Solo senza azienda</label></div>' +
      '</div><div class="res-meta" aria-live="polite">' + tot + (tot === 1 ? ' persona' : ' persone') + '</div>' +
      '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Persona</th><th scope="col">Utente</th><th scope="col">Gruppi</th><th scope="col">Aziende</th><th scope="col">Profili di amministrazione</th><th scope="col">Stato</th></tr></thead><tbody>' +
      pag.map(function (u) {
        return '<tr data-apri="' + esc(u.id) + '"><td class="prim">' + esc(u.nome) + '<span class="sub">' + esc(u.email) + '</span></td><td class="mono">' + esc(u.username) + '</td>' +
          '<td>' + (u.gruppi.filter(function (g) { return g !== 'tutti'; }).map(esc).join(', ') || '<span style="color:var(--testo-debole)">nessuno</span>') + '</td>' +
          '<td>' + (u.aziende.length ? chipAz(u.aziende) : '<span style="color:var(--stato-attenzione)">' + ic('warn') + ' Nessuna</span>') + '</td>' +
          '<td>' + (u.profili.map(function (p) { return '<span class="chip">' + esc(p) + '</span>'; }).join(' ') || '—') + '</td>' +
          '<td>' + badge(u.attivo ? ['ok', 'Attivo', 'b-stato-ok'] : ['neut', 'Disattivato', 'b-stato-neutro']) + '</td></tr>';
      }).join('') + '</tbody></table>' + (tot ? '' : '<div class="empty-note">Nessuna persona corrisponde ai filtri.</div>') + '</div><div class="pager" id="u-pager"></div></div>');
    $('#u-g').value = UF.gruppo; $('#u-s').value = UF.stato;
    var t; $('#u-q').oninput = function () { clearTimeout(t); var v = this.value; t = setTimeout(function () { UF.q = v; UP.n = 1; disegnaUtenti(); var i = $('#u-q'); i.focus(); i.setSelectionRange(v.length, v.length); }, 250); };
    $('#u-g').onchange = function () { UF.gruppo = this.value; UP.n = 1; disegnaUtenti(); $('#u-g').focus(); };
    $('#u-s').onchange = function () { UF.stato = this.value; UP.n = 1; disegnaUtenti(); $('#u-s').focus(); };
    $('#u-senza').onchange = function () { UF.senza = this.checked; UP.n = 1; disegnaUtenti(); $('#u-senza').focus(); };
    var bn = $('#u-nuovo'); if (bn) bn.onclick = nuovoUtente;
    paginatore($('#u-pager'), UP, tot, disegnaUtenti);
    righeAttive(c, apriUtente);
  }
  function caselleGruppi(nome, opzioni, scelti, etichetta) {
    return '<fieldset class="field" style="border:0;padding:0;margin:0 0 12px"><legend style="font-size:12px;font-weight:600;color:var(--testo-tenue);margin-bottom:6px">' + etichetta + '</legend>' +
      '<div style="display:flex;flex-wrap:wrap;gap:8px 16px">' + opzioni.map(function (o) {
        return '<label class="check-row"><input type="checkbox" name="' + nome + '" value="' + esc(o[0]) + '"' + (scelti.indexOf(o[0]) > -1 ? ' checked' : '') + '> ' + esc(o[1]) + '</label>'; }).join('') + '</div></fieldset>';
  }
  function spuntati(nome) { return $$('#modal-body input[name=' + nome + ']:checked').map(function (x) { return x.value; }); }
  function nuovoUtente() {
    var gr = GRUPPI_OP.filter(function (g) { return g.nome !== 'tutti'; }).map(function (g) { return [g.nome, g.nome]; });
    dialogo('Nuovo utente',
      '<div class="field" style="margin-bottom:10px"><label for="m-un">Nome utente</label><input class="input mono" id="m-un" autocomplete="off" placeholder="nome.cognome"></div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px"><div class="field"><label for="m-no">Nome</label><input class="input" id="m-no"></div><div class="field"><label for="m-co">Cognome</label><input class="input" id="m-co"></div></div>' +
      '<div class="field" style="margin-bottom:12px"><label for="m-em">Email (facoltativa)</label><input class="input" id="m-em" type="email"></div>' +
      caselleGruppi('g', gr, [], 'Gruppi operativi') +
      caselleGruppi('a', AZIENDE.map(function (a) { return [a.codice, a.ragione_sociale]; }), AZIENDE.length === 1 ? [AZIENDE[0].codice] : [], 'Aziende abilitate (almeno una)') +
      '<p class="ro-note">' + ic('info') + 'Riceverà una password temporanea da cambiare al primo accesso.</p>',
      function () {
        return api('POST', '/utenti', { username: $('#m-un').value, nome_proprio: $('#m-no').value, cognome: $('#m-co').value, email: $('#m-em').value, gruppi: spuntati('g'), aziende: spuntati('a') })
          .then(function (r) { toast('Utente creato'); var chi = $('#m-un').value; setTimeout(function () { passwordMostrata(r.password_temporanea, chi); }, 0); renderUtenti(); })
          .catch(function (e) { toast(e.message, 'err'); return false; });
      }, 'Crea');
  }
  function apriUtente(id) {
    Promise.all([api('GET', '/utenti/' + encodeURIComponent(id)), may('profili') ? api('GET', '/profili') : Promise.resolve(null)]).then(function (r) {
      var u = r[0], prof = r[1], modU = can('utenti'), modP = can('profili');
      var gr = GRUPPI_OP.filter(function (g) { return g.nome !== 'tutti'; });
      apriPannello('<div><h2 id="drawer-titolo">' + esc(u.nome) + '</h2><div class="sub mono">' + esc(u.username) + (u.email ? ' · ' + esc(u.email) : '') + '</div></div>',
        (u.federato ? '<div class="notice notice-info">' + ic('info') + '<span>Gestito dalla directory aziendale: nome, email e password si cambiano lì.</span></div>' : '') +
        (!u.aziende.length ? '<div class="notice notice-crit">' + ic('crit') + '<span><b>Nessuna azienda abilitata:</b> non vedrà nessun dato.</span></div>' : '') +
        '<dl class="dl"><dt>Stato</dt><dd>' + badge(u.attivo ? ['ok', 'Attivo', 'b-stato-ok'] : ['neut', 'Disattivato', 'b-stato-neutro']) + '</dd>' +
        '<dt>Gruppi operativi</dt><dd>' + (u.gruppi.filter(function (g) { return g !== 'tutti'; }).map(esc).join(', ') || 'nessuno') + '</dd>' +
        '<dt>Aziende abilitate</dt><dd>' + (u.aziende.length ? chipAz(u.aziende) : 'nessuna') + '</dd>' +
        '<dt>Profili di amministrazione</dt><dd>' + (u.profili.map(function (p) { return '<span class="chip">' + esc(p) + '</span>'; }).join(' ') || 'nessuno: è un operatore') + '</dd></dl>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">' +
        (modU ? '<button class="btn btn-secondary btn-sm" data-az="gruppi">Gruppi e aziende…</button>' : '') +
        (modP ? '<button class="btn btn-secondary btn-sm" data-az="profili">Profili di amministrazione…</button>' : '') +
        (modU && !u.federato ? '<button class="btn btn-secondary btn-sm" data-az="anagrafica">Nome ed email…</button><button class="btn btn-secondary btn-sm" data-az="password">Reimposta password</button>' : '') +
        (modU ? '<button class="btn btn-ghost btn-sm" data-az="attivo">' + (u.attivo ? 'Disattiva…' : 'Riattiva') + '</button>' : '') +
        (may('vedicome') ? '<a class="btn btn-ghost btn-sm" href="#/vedi-come" data-vedi="1">' + ic('eye') + 'Vedi come</a>' : '') + '</div>');
      function aggiorna(msg) { toast(msg); api('GET', '/utenti').then(function (d) { UT = d; if (location.hash.indexOf('utenti') > -1) disegnaUtenti(); }); setTimeout(function () { apriUtente(id); }, 0); }
      function fallito(e) { toast(e.status === 403 ? 'Keycloak non te lo consente: ' + e.message : e.message, 'err'); return false; }
      $$('#drawer [data-az]').forEach(function (b) {
        b.onclick = function () {
          var az = b.dataset.az;
          if (az === 'gruppi') dialogo('Gruppi e aziende di ' + u.nome,
            caselleGruppi('g', gr.map(function (g) { return [g.nome, g.nome]; }), u.gruppi, 'Gruppi operativi') +
            caselleGruppi('a', AZIENDE.map(function (a) { return [a.codice, a.ragione_sociale]; }), u.aziende, 'Aziende abilitate (solo quelle a cui sei abilitato tu)'),
            function () {
              var g = spuntati('g'), a = spuntati('a'), agg = [], tog = [];
              gr.forEach(function (x) { var s = g.indexOf(x.nome) > -1, era = u.gruppi.indexOf(x.nome) > -1; if (s && !era) agg.push('/' + x.nome); if (!s && era) tog.push('/' + x.nome); });
              AZIENDE.forEach(function (x) { var s = a.indexOf(x.codice) > -1, era = u.aziende.indexOf(x.codice) > -1; if (s && !era) agg.push('/azienda-' + x.codice); if (!s && era) tog.push('/azienda-' + x.codice); });
              if (!agg.length && !tog.length) return true;
              return api('POST', '/utenti/' + id + '/gruppi', { aggiungi: agg, togli: tog }).then(function () { aggiorna('Gruppi aggiornati'); }).catch(fallito);
            }, 'Salva');
          else if (az === 'profili') dialogo('Profili di amministrazione di ' + u.nome,
            caselleGruppi('p', prof.profili.map(function (p) { return [p.percorso, p.nome + ' — ' + p.ruoli.map(function (r) { var x = prof.ruoli.filter(function (y) { return y.id === r; })[0]; return x ? x.nome : r; }).join(', ')]; }),
              prof.profili.filter(function (p) { return u.profili.indexOf(p.nome) > -1; }).map(function (p) { return p.percorso; }), 'Profili') +
            '<p class="ro-note">' + ic('lock') + 'Superutente lo assegna solo un altro Superutente: Keycloak rifiuta gli altri.</p>',
            function () {
              var s = spuntati('p'), agg = [], tog = [];
              prof.profili.forEach(function (p) { var on = s.indexOf(p.percorso) > -1, era = u.profili.indexOf(p.nome) > -1; if (on && !era) agg.push(p.percorso); if (!on && era) tog.push(p.percorso); });
              if (!agg.length && !tog.length) return true;
              return api('POST', '/utenti/' + id + '/gruppi', { aggiungi: agg, togli: tog }).then(function () { aggiorna('Profili aggiornati'); }).catch(fallito);
            }, 'Salva');
          else if (az === 'anagrafica') dialogo('Nome ed email',
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px"><div class="field"><label for="m-no">Nome</label><input class="input" id="m-no" value="' + esc(u.nome_proprio) + '"></div><div class="field"><label for="m-co">Cognome</label><input class="input" id="m-co" value="' + esc(u.cognome) + '"></div></div>' +
            '<div class="field"><label for="m-em">Email</label><input class="input" id="m-em" type="email" value="' + esc(u.email) + '"></div>',
            function () { return api('PUT', '/utenti/' + id, { nome_proprio: $('#m-no').value, cognome: $('#m-co').value, email: $('#m-em').value }).then(function () { aggiorna('Dati aggiornati'); }).catch(fallito); }, 'Salva');
          else if (az === 'password') dialogo('Reimpostare la password di ' + u.nome + '?',
            '<p style="margin-top:0;font-size:13.5px">Verrà creata una password temporanea da comunicare alla persona; la vecchia smette di funzionare subito.</p>',
            function () { return api('POST', '/utenti/' + id + '/password').then(function (r) { setTimeout(function () { passwordMostrata(r.password_temporanea, u.nome); }, 0); }).catch(fallito); }, 'Reimposta');
          else if (u.attivo) dialogo('Disattivare ' + u.nome + '?',
            '<div class="notice notice-warn">' + ic('warn') + '<span>Non potrà più accedere né alla chat né all\'amministrazione. Si può riattivare.</span></div>' +
            '<div class="field" style="margin-top:12px"><label for="m-mot">Motivo (obbligatorio)</label><textarea class="textarea" id="m-mot"></textarea></div>',
            function () { var m = $('#m-mot').value.trim(); if (!m) { toast('Il motivo è obbligatorio.', 'err'); return false; } return api('PUT', '/utenti/' + id, { attivo: false, motivo: m }).then(function () { aggiorna('Utente disattivato'); }).catch(fallito); }, 'Disattiva');
          else api('PUT', '/utenti/' + id, { attivo: true }).then(function () { aggiorna('Utente riattivato'); }).catch(fallito);
        };
      });
      var bv = $('#drawer [data-vedi]'); if (bv) bv.onclick = function (e) { e.preventDefault(); e.stopPropagation(); chiudiPannello(); VC_PRESELEZIONE = u.username; location.hash = '#/vedi-come'; };
    }).catch(function (e) { toast(e.message, 'err'); });
  }
  var VC_PRESELEZIONE = '';

  function renderGruppi() {
    var c = $('#content'); caricamento(c);
    api('GET', '/gruppi-operativi').then(function (g) {
      GRUPPI_OP = g;
      c.innerHTML = schermata('Gruppi', 'I gruppi operativi decidono quali fonti una persona può vedere. Le aziende e i profili di amministrazione si gestiscono dalla scheda della persona.',
        can('struttura') ? '<div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-secondary" id="g-allinea" title="Da fare dopo aver creato persone nuove: senza, i gestori non le possono aggiungere">' + ic('refresh') + 'Riallinea i gestori</button><button class="btn btn-primary" id="g-nuovo">' + ic('plus') + 'Nuovo gruppo</button></div>' : '<span class="ro-note">' + ic('lock') + 'Creare e rinominare gruppi: solo Superutente</span>',
        '<div class="card"><div class="table-box"><table class="tbl"><thead><tr><th scope="col">Gruppo</th><th scope="col" class="num-col">Persone</th><th scope="col">Fonti che vede</th></tr></thead><tbody>' +
        g.map(function (x) { return '<tr data-apri="' + esc(x.id) + '"><td class="prim">' + esc(x.nome) + (x.nome === 'tutti' ? '<span class="sub">Assegnato a tutti automaticamente</span>' : '') + '</td><td class="num-col">' + x.membri + '</td><td>' + (x.fonti.map(esc).join(', ') || '<span style="color:var(--testo-debole)">nessuna</span>') + '</td></tr>'; }).join('') +
        '</tbody></table></div></div>');
      var ba = $('#g-allinea'); if (ba) ba.onclick = function () {
        ba.disabled = true;
        api('POST', '/gestori/allinea').then(function (r) { toast('Gestori riallineati' + (r.gruppi.length ? ': ' + r.gruppi.join(', ') : ' (nessun gruppo con gestori)')); })
          .catch(function (e) { toast(e.message, 'err'); }).then(function () { ba.disabled = false; });
      };
      var bn = $('#g-nuovo'); if (bn) bn.onclick = function () {
        dialogo('Nuovo gruppo operativo', '<div class="field"><label for="m-gn">Nome (minuscolo, es. «qualita»)</label><input class="input" id="m-gn"><p class="ro-note" style="margin-top:6px">' + ic('info') + 'Per dare a qualcuno la gestione delle persone di un gruppo, crea anche «<i>nome</i>-gestori» e mettici quelle persone.</p></div>',
          function () { return api('POST', '/gruppi-operativi', { nome: $('#m-gn').value }).then(function () { toast('Gruppo creato'); renderGruppi(); }).catch(function (e) { toast(e.message, 'err'); return false; }); }, 'Crea');
      };
      righeAttive(c, function (gid) {
        var gr = g.filter(function (x) { return x.id === gid; })[0];
        api('GET', '/gruppi-operativi/' + gid + '/membri').then(function (m) {
          apriPannello('<div><h2 id="drawer-titolo">' + esc(gr.nome) + '</h2><div class="sub">' + m.length + (m.length === 1 ? ' persona' : ' persone') + '</div></div>',
            '<dl class="dl"><dt>Fonti che questo gruppo vede</dt><dd>' + (gr.fonti.map(esc).join(', ') || 'nessuna') + '</dd></dl>' +
            (can('struttura') && gr.nome !== 'tutti' ? '<div style="display:flex;gap:8px;margin:12px 0"><button class="btn btn-secondary btn-sm" id="g-rin">Rinomina…</button><button class="btn btn-ghost btn-sm" id="g-del">Elimina</button></div>' : '') +
            '<h3 style="font-size:12.5px;color:var(--testo-debole);text-transform:uppercase;letter-spacing:.05em;margin:16px 0 8px">Persone</h3>' +
            '<table class="tbl"><thead><tr><th scope="col">Persona</th><th scope="col">Utente</th><th scope="col">Stato</th></tr></thead><tbody>' +
            (m.map(function (u) { return '<tr><td class="prim">' + esc(u.nome) + '</td><td class="mono">' + esc(u.username) + '</td><td>' + badge(u.attivo ? ['ok', 'Attivo', 'b-stato-ok'] : ['neut', 'Disattivato', 'b-stato-neutro']) + '</td></tr>'; }).join('') || '<tr><td colspan="3" class="empty-note">Nessuno.</td></tr>') +
            '</tbody></table><p class="ro-note" style="margin-top:10px">' + ic('info') + 'Si aggiungono e tolgono persone dalla loro scheda, in Utenti.</p>');
          var br = $('#g-rin'); if (br) br.onclick = function () {
            dialogo('Rinomina «' + gr.nome + '»', '<div class="field"><label for="m-gn">Nuovo nome</label><input class="input" id="m-gn" value="' + esc(gr.nome) + '"></div><p class="ro-note">' + ic('info') + 'Le fonti che usano questo gruppo vengono aggiornate insieme.</p>',
              function () { return api('PUT', '/gruppi-operativi/' + gid, { nome: $('#m-gn').value }).then(function () { toast('Gruppo rinominato'); chiudiPannello(); renderGruppi(); }).catch(function (e) { toast(e.message, 'err'); return false; }); }, 'Rinomina');
          };
          var bd = $('#g-del'); if (bd) bd.onclick = function () {
            dialogo('Eliminare «' + gr.nome + '»?', '<p style="margin-top:0;font-size:13.5px">Si può eliminare solo un gruppo senza persone e non usato da nessuna fonte.</p>',
              function () { return api('DELETE', '/gruppi-operativi/' + gid).then(function () { toast('Gruppo eliminato'); chiudiPannello(); renderGruppi(); }).catch(function (e) { toast(e.message, 'err'); return false; }); }, 'Elimina');
          };
        }).catch(function (e) { toast(e.message, 'err'); });
      });
    }).catch(function (e) { errore(c, e, renderGruppi); });
  }

  /* I miei gruppi: chi sta in <gruppo>-gestori aggiunge e toglie i colleghi
     del suo gruppo. Keycloak lo limita a quel gruppo (gestori.py): qui si
     mostra solo cio' che il server restituisce. */
  function renderGestiti() {
    var c = $('#content'); caricamento(c);
    api('GET', '/gestiti').then(function (d) {
      if (!d.length) { c.innerHTML = schermata('I miei gruppi', '', '', vuoto('Nessun gruppo da gestire', 'Non sei nei gestori di nessun gruppo.')); return; }
      c.innerHTML = schermata('I miei gruppi', 'Le persone dei gruppi che gestisci, e le cartelle che vedono. Chi è nel gruppo vede i documenti della cartella nell\'assistente.', '',
        d.map(function (g) {
          return '<div class="card"><div class="c-head"><div><h2>' + esc(g.nome) + '</h2><p class="h-intro">' + g.membri.length + (g.membri.length === 1 ? ' persona' : ' persone') + '</p></div>' +
            '<button class="btn btn-primary btn-sm" data-aggiungi="' + esc(g.nome) + '">' + ic('plus') + 'Aggiungi una persona</button></div>' +
            (g.cartelle.length ? '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Cartella</th><th scope="col">Percorso</th><th scope="col" class="num-col">Documenti</th><th scope="col">Aggiornata</th><th scope="col">Stato</th></tr></thead><tbody>' +
              g.cartelle.map(function (f) {
                return '<tr><td class="prim">' + esc(f.descrizione) + '</td><td class="mono">' + esc(f.percorso) + '</td><td class="num-col">' + f.documenti +
                  (f.errori ? ' <span class="badge b-stato-attenzione">' + ic('warn') + f.errori + ' non leggibili</span>' : '') + '</td><td>' + quando(f.aggiornata) + '</td><td>' + badge(STATO_FONTE[f.stato]) + '</td></tr>';
              }).join('') + '</tbody></table></div>'
              : '<p class="ro-note" style="padding:0 18px">' + ic('info') + 'Nessuna cartella collegata a questo gruppo: la collega chi gestisce le fonti.</p>') +
            '<div class="table-box"><table class="tbl"><thead><tr><th scope="col">Persona</th><th scope="col">Utente</th><th scope="col">Email</th><th scope="col"><span class="sr">Azioni</span></th></tr></thead><tbody>' +
            (g.membri.map(function (u) {
              return '<tr><td class="prim">' + esc(u.nome) + '</td><td class="mono">' + esc(u.username) + '</td><td>' + esc(u.email) + '</td>' +
                '<td style="text-align:right"><button class="btn btn-ghost btn-sm" data-togli="' + esc(g.nome) + '" data-uid="' + esc(u.id) + '" data-nome="' + esc(u.nome) + '">Togli</button></td></tr>';
            }).join('') || '<tr><td colspan="4" class="empty-note">Nessuno.</td></tr>') + '</tbody></table></div></div>';
        }).join(''));
      $$('[data-togli]', c).forEach(function (b) {
        b.onclick = function () {
          dialogo('Togliere ' + b.dataset.nome + ' da «' + b.dataset.togli + '»?',
            '<p style="margin-top:0;font-size:13.5px">Non vedrà più i documenti delle cartelle di questo gruppo.</p>',
            function () { return api('DELETE', '/gestiti/' + encodeURIComponent(b.dataset.togli) + '/membri/' + b.dataset.uid).then(function () { toast('Persona tolta'); renderGestiti(); }).catch(function (e) { toast(e.message, 'err'); return false; }); }, 'Togli');
        };
      });
      $$('[data-aggiungi]', c).forEach(function (b) {
        b.onclick = function () {
          var gruppo = b.dataset.aggiungi;
          dialogo('Aggiungi a «' + gruppo + '»',
            '<div class="field search-box"><label for="m-ag">Cerca per nome, utente o email</label>' + ic('search') + '<input class="input" id="m-ag" autocomplete="off"></div><div id="m-ag-ris" aria-live="polite"></div>',
            function () { return true; }, 'Chiudi');
          var t;
          function cerca() {
            api('GET', '/gestiti/' + encodeURIComponent(gruppo) + '/candidati?q=' + encodeURIComponent($('#m-ag').value)).then(function (l) {
              $('#m-ag-ris').innerHTML = l.length ? '<table class="tbl"><tbody>' + l.map(function (u) {
                return '<tr><td class="prim">' + esc(u.nome) + '<span class="sub">' + esc(u.email || u.username) + '</span></td><td style="text-align:right"><button class="btn btn-secondary btn-sm" data-uid="' + esc(u.id) + '">Aggiungi</button></td></tr>';
              }).join('') + '</tbody></table>' : '<p class="empty-note">Nessuno da aggiungere con questo nome.</p>';
              $$('#m-ag-ris [data-uid]').forEach(function (x) {
                x.onclick = function () {
                  x.disabled = true;
                  api('POST', '/gestiti/' + encodeURIComponent(gruppo) + '/membri/' + x.dataset.uid).then(function () { toast('Persona aggiunta'); x.closest('tr').remove(); renderGestiti(); })
                    .catch(function (e) { toast(e.message, 'err'); x.disabled = false; });
                };
              });
            }).catch(function (e) { $('#m-ag-ris').innerHTML = '<p class="empty-note">' + esc(e.message) + '</p>'; });
          }
          $('#m-ag').oninput = function () { clearTimeout(t); t = setTimeout(cerca, 250); };
          cerca();
        };
      });
    }).catch(function (e) { errore(c, e, renderGestiti); });
  }

  function renderProfili() {
    var c = $('#content'); caricamento(c);
    api('GET', '/profili').then(function (d) {
      var struttura = can('struttura');
      c.innerHTML = schermata('Profili amministrativi', 'Un profilo è un insieme di ruoli con un nome. Si assegna alle persone dalla loro scheda, in Utenti.',
        struttura ? '<button class="btn btn-primary" id="p-nuovo">' + ic('plus') + 'Nuovo profilo</button>' : '<span class="ro-note">' + ic('lock') + 'Comporre i profili: solo Superutente</span>',
        '<div class="card"><div class="c-head"><div><h2>Ruoli per profilo</h2><p class="h-intro">' + (struttura ? 'Spunta i ruoli e salva il profilo.' : 'Sola lettura.') + '</p></div></div><div class="table-box"><table class="tbl"><thead><tr><th scope="col">Profilo</th>' +
        d.ruoli.map(function (r) { return '<th scope="col" style="text-align:center">' + esc(r.nome) + '</th>'; }).join('') + '<th scope="col" class="num-col">Persone</th>' + (struttura ? '<th scope="col"><span class="sr">Azioni</span></th>' : '') + '</tr></thead><tbody>' +
        d.profili.map(function (p) {
          return '<tr><th scope="row" class="prim" style="text-align:left;font-size:13px;text-transform:none;letter-spacing:0;background:none;color:var(--testo)">' + esc(p.nome) + '</th>' + d.ruoli.map(function (r) {
            var on = p.ruoli.indexOf(r.id) > -1;
            return '<td style="text-align:center">' + (struttura ? '<input type="checkbox" data-p="' + esc(p.id) + '" value="' + r.id + '"' + (on ? ' checked' : '') + ' aria-label="' + esc(r.nome) + ' nel profilo ' + esc(p.nome) + '">' : (on ? ic('check') + '<span class="sr">sì</span>' : '<span class="sr">no</span>')) + '</td>';
          }).join('') + '<td class="num-col">' + p.membri.length + '</td>' + (struttura ? '<td><button class="btn btn-secondary btn-sm" data-salva="' + esc(p.id) + '">Salva</button></td>' : '') + '</tr>';
        }).join('') + '</tbody></table></div></div>' +
        '<div class="card"><div class="c-head"><div><h2>Chi ha quale profilo</h2><p class="h-intro">Per le revisioni periodiche degli accessi.</p></div></div><div class="table-box"><table class="tbl"><thead><tr><th scope="col">Profilo</th><th scope="col">Persone</th></tr></thead><tbody>' +
        d.profili.map(function (p) { return '<tr><td class="prim">' + esc(p.nome) + '</td><td>' + (p.membri.map(function (u) { return esc(u.nome) + ' <span class="mono" style="color:var(--testo-debole)">(' + esc(u.username) + ')</span>'; }).join(', ') || '—') + '</td></tr>'; }).join('') +
        '</tbody></table></div></div>');
      $$('[data-salva]').forEach(function (b) {
        b.onclick = function () {
          var ruoli = $$('input[data-p="' + b.dataset.salva + '"]:checked').map(function (x) { return x.value; });
          api('PUT', '/profili/' + b.dataset.salva + '/ruoli', { ruoli: ruoli }).then(function () { toast('Profilo aggiornato'); renderProfili(); }).catch(function (e) { toast(e.message, 'err'); });
        };
      });
      var bn = $('#p-nuovo'); if (bn) bn.onclick = function () {
        dialogo('Nuovo profilo', '<div class="field"><label for="m-pn">Nome (es. «Controllo di gestione»)</label><input class="input" id="m-pn"></div>',
          function () { return api('POST', '/profili', { nome: $('#m-pn').value }).then(function () { toast('Profilo creato: ora scegli i ruoli'); renderProfili(); }).catch(function (e) { toast(e.message, 'err'); return false; }); }, 'Crea');
      };
    }).catch(function (e) { errore(c, e, renderProfili); });
  }

  /* ============================== AZIENDE (decisione 69) ==============================
     Ogni azienda si abbina a un collegamento al gestionale (decisione 70). Si
     crea insieme al gruppo Keycloak 'azienda-<codice>'; non si elimina, si
     disattiva. Il codice nel gestionale si scopre dal collegamento. */
  function renderAziende() {
    var c = $('#content'); caricamento(c);
    Promise.all([api('GET', '/aziende-gestione'), api('GET', '/collegamenti').catch(function () { return []; })]).then(function (r) {
      var d = r[0], COLLEGAMENTI = r[1];
      var nomiColl = {}; COLLEGAMENTI.forEach(function (x) { nomiColl[x.id] = x.nome; });
      var mod = can('aziende');
      c.innerHTML = schermata('Aziende', 'Le aziende gestite dall\'assistente. Ognuna importa i dati dal proprio collegamento al gestionale; le persone si abilitano a un\'azienda dalla loro scheda in Utenti.',
        mod ? '<button class="btn btn-primary" id="az-nuova">' + ic('plus') + 'Nuova azienda</button>' : roNota('Superutente'),
        (d.aziende.length ? '' : vuoto('Nessuna azienda', 'Crea la prima azienda: senza, nessuno vede dati gestionali.')) +
        '<div class="card"' + (d.aziende.length ? '' : ' hidden') + '><div class="table-box"><table class="tbl"><thead><tr><th scope="col">Azienda</th><th scope="col">Codice</th><th scope="col">Partita IVA</th><th scope="col">Collegamento</th><th scope="col">Codice nel gestionale</th><th scope="col" class="num-col">Persone abilitate</th><th scope="col">Ultima importazione</th><th scope="col">Stato</th></tr></thead><tbody>' +
        d.aziende.map(function (a) {
          return '<tr data-apri="' + esc(a.codice) + '"><td class="prim">' + esc(a.ragione_sociale) + '</td><td class="mono">' + esc(a.codice) + '</td><td class="mono">' + esc(a.partita_iva || '—') + '</td>' +
            '<td>' + (a.collegamento ? esc(nomiColl[a.collegamento] || a.collegamento) : '<span style="color:var(--stato-attenzione)">' + ic('warn') + ' Non collegata</span>') + '</td><td class="mono">' + esc(a.codice_origine || '—') + '</td>' +
            '<td class="num-col">' + (a.gruppo_presente ? a.persone : '<span style="color:var(--stato-critico)" title="Manca il gruppo di abilitazione">' + ic('crit') + '<span class="sr">Manca il gruppo di abilitazione</span></span>') + '</td>' +
            '<td>' + quando(a.ultima_importazione) + '</td><td>' + badge(a.attiva ? ['ok', 'Attiva', 'b-stato-ok'] : ['neut', 'Disattivata', 'b-stato-neutro']) + '</td></tr>';
        }).join('') + '</tbody></table></div></div>' +
        '<div class="notice notice-info" style="margin-top:16px">' + ic('info') + '<span>I collegamenti (con le credenziali) si gestiscono in <a href="#/collegamenti">Gestionali</a>. Qui si sceglie quale collegamento usa ogni azienda e con quale codice.</span></div>');
      function origineCampo(valore) {
        return '<div class="field"><label for="m-orig">Codice nel gestionale</label>' +
          '<input class="input mono" id="m-orig" value="' + esc(valore || '') + '" aria-describedby="m-orig-h">' +
          '<span class="hint" id="m-orig-h">Se il gestionale contiene più aziende (in Integra: il codice azienda, es. 001). Scegli un collegamento per compilarlo dall\'elenco.</span></div>';
      }
      function popolaOrigine(collId, valore) {
        if (!collId) return;
        api('GET', '/collegamenti/' + encodeURIComponent(collId) + '/aziende').then(function (az) {
          var o = $('#m-orig'); if (!o) return;
          if (!az.length) { $('#m-orig-h').textContent = 'Il gestionale non ha risposto: inserisci il codice a mano.'; return; }
          var sel = document.createElement('select');
          sel.className = 'select'; sel.id = 'm-orig';
          sel.innerHTML = az.map(function (x) { return '<option value="' + esc(x.codice) + '"' + (String(x.codice) === String(valore || o.value) ? ' selected' : '') + '>' + esc(x.codice) + ' — ' + esc(x.ragione_sociale) + '</option>'; }).join('');
          o.replaceWith(sel);
          $('#m-orig-h').textContent = 'Dal collegamento: ' + az.length + (az.length === 1 ? ' azienda' : ' aziende') + '.';
        }).catch(function () { var h = $('#m-orig-h'); if (h) h.textContent = 'Collegamento non raggiungibile: inserisci il codice a mano.'; });
      }
      function modulo(a) {
        a = a || {};
        var coll = a.collegamento || '';
        return (a.codice ? '' : '<div class="field" style="margin-bottom:10px"><label for="m-cod">Codice</label><input class="input mono" id="m-cod" aria-describedby="m-cod-h" placeholder="es. luis"><span class="hint" id="m-cod-h">Minuscole, cifre e trattini. Non si cambia più: è nei dati importati e nel gruppo di abilitazione.</span></div>') +
          '<div class="field" style="margin-bottom:10px"><label for="m-rs">Ragione sociale</label><input class="input" id="m-rs" value="' + esc(a.ragione_sociale || '') + '"></div>' +
          '<div class="field" style="margin-bottom:10px"><label for="m-piva">Partita IVA</label><input class="input mono" id="m-piva" value="' + esc(a.partita_iva || '') + '"></div>' +
          '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px"><div class="field"><label for="m-coll">Collegamento</label><select class="select" id="m-coll"><option value="">Non ancora collegata</option>' +
          COLLEGAMENTI.map(function (x) { return '<option value="' + esc(x.id) + '"' + (x.id === coll ? ' selected' : '') + '>' + esc(x.nome) + (x.attivo ? '' : ' (disattivato)') + '</option>'; }).join('') + '</select></div>' +
          origineCampo(a.codice_origine) + '</div>' +
          '<div class="field"><label for="m-note">Note</label><textarea class="textarea" id="m-note">' + esc(a.note || '') + '</textarea></div>';
      }
      function valori(a) {
        var o = $('#m-orig');
        return { ragione_sociale: $('#m-rs').value, partita_iva: $('#m-piva').value, connettore: (a && a.connettore) || '',
                 collegamento: $('#m-coll').value || null, codice_origine: o ? o.value.trim() : '', note: $('#m-note').value };
      }
      function collega() {
        var sel = $('#m-coll'); if (!sel) return;
        sel.onchange = function () { popolaOrigine(this.value, ''); };
        if (sel.value) popolaOrigine(sel.value, $('#m-orig') ? $('#m-orig').value : '');
      }
      var bn = $('#az-nuova'); if (bn) bn.onclick = function () {
        dialogo('Nuova azienda', modulo(null), function () {
          var v = valori(null); v.codice = $('#m-cod').value;
          return api('POST', '/aziende-gestione', v).then(function () { toast('Azienda creata: ora abilita le persone da Utenti'); return api('GET', '/aziende'); })
            .then(function (az) { AZIENDE = az; renderAziende(); }).catch(function (e) { toast(e.message, 'err'); return false; });
        }, 'Crea');
        collega();
      };
      righeAttive(c, function (cod) {
        var a = d.aziende.filter(function (x) { return x.codice === cod; })[0];
        apriPannello('<div><h2 id="drawer-titolo">' + esc(a.ragione_sociale) + '</h2><div class="sub mono">' + esc(a.codice) + '</div></div>',
          (a.gruppo_presente ? '' : '<div class="notice notice-crit">' + ic('crit') + '<span><b>Manca il gruppo di abilitazione</b> «azienda-' + esc(a.codice) + '»: nessuno può essere abilitato a questa azienda.</span></div>') +
          '<dl class="dl"><dt>Partita IVA</dt><dd class="mono">' + esc(a.partita_iva || '—') + '</dd><dt>Collegamento</dt><dd>' + (a.collegamento ? esc(nomiColl[a.collegamento] || a.collegamento) : '<span style="color:var(--stato-attenzione)">' + ic('warn') + ' Non collegata</span>') + (a.codice_origine ? ' · codice <span class="mono">' + esc(a.codice_origine) + '</span>' : '') + '</dd>' +
          '<dt>Persone abilitate</dt><dd>' + (a.gruppo_presente ? a.persone : '—') + '</dd><dt>Ultima importazione riuscita</dt><dd>' + quando(a.ultima_importazione) + '</dd>' +
          '<dt>Stato</dt><dd>' + badge(a.attiva ? ['ok', 'Attiva', 'b-stato-ok'] : ['neut', 'Disattivata', 'b-stato-neutro']) + '</dd><dt>Note</dt><dd>' + esc(a.note || '—') + '</dd></dl>' +
          (mod ? '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px"><button class="btn btn-secondary btn-sm" id="az-mod">Modifica…</button><button class="btn btn-ghost btn-sm" id="az-att">' + (a.attiva ? 'Disattiva…' : 'Riattiva') + '</button></div>' : ''));
        var base = { ragione_sociale: a.ragione_sociale, partita_iva: a.partita_iva, connettore: a.connettore, collegamento: a.collegamento, codice_origine: a.codice_origine, note: a.note };
        var bm = $('#az-mod'); if (bm) bm.onclick = function () {
          dialogo('Modifica «' + a.ragione_sociale + '»', modulo(a), function () {
            return api('PUT', '/aziende-gestione/' + encodeURIComponent(cod), Object.assign(valori(a), { attiva: a.attiva }))
              .then(function () { toast('Azienda aggiornata'); chiudiPannello(); renderAziende(); }).catch(function (e) { toast(e.message, 'err'); return false; });
          }, 'Salva');
          collega();
        };
        var ba = $('#az-att'); if (ba) ba.onclick = function () {
          if (!a.attiva) {
            api('PUT', '/aziende-gestione/' + encodeURIComponent(cod), Object.assign(base, { attiva: true }))
              .then(function () { toast('Azienda riattivata'); chiudiPannello(); renderAziende(); }).catch(function (e) { toast(e.message, 'err'); });
            return;
          }
          dialogo('Disattivare «' + a.ragione_sociale + '»?',
            '<div class="notice notice-warn">' + ic('warn') + '<span>Le importazioni si fermano e l\'azienda non compare più nel pannello. I dati già importati e il registro restano. Si può riattivare.</span></div>' +
            '<div class="field" style="margin-top:12px"><label for="m-mot">Motivo (obbligatorio)</label><textarea class="textarea" id="m-mot"></textarea></div>',
            function () {
              var m = $('#m-mot').value.trim(); if (!m) { toast('Il motivo è obbligatorio.', 'err'); return false; }
              return api('PUT', '/aziende-gestione/' + encodeURIComponent(cod), Object.assign(base, { attiva: false, motivo: m }))
                .then(function () { toast('Azienda disattivata'); chiudiPannello(); renderAziende(); }).catch(function (e) { toast(e.message, 'err'); return false; });
            }, 'Disattiva');
        };
      });
    }).catch(function (e) { errore(c, e, renderAziende); });
  }

  /* ============================== GESTIONALI (SPECIFICA-CONNETTORI §8) ==============================
     I collegamenti ai gestionali: li crea il Superutente. Le credenziali sono
     cifrate nel database; il pannello le inoltra al servizio `connettori` e non
     le vede mai (i segreti non tornano indietro, nemmeno cifrati). */
  var COL = [], CATALOGO = null;
  function renderCollegamenti() {
    var c = $('#content'); caricamento(c);
    Promise.all([api('GET', '/collegamenti'), api('GET', '/connettori')]).then(function (r) {
      COL = r[0]; CATALOGO = r[1]; disegnaCollegamenti();
    }).catch(function (e) { errore(c, e, renderCollegamenti); });
  }
  function disegnaCollegamenti() {
    var c = $('#content'), mod = can('collegamenti');
    var nomi = {}; CATALOGO.forEach(function (x) { nomi[x.tipo] = x.nome; });
    c.innerHTML = schermata('Gestionali', 'I collegamenti ai gestionali da cui le aziende importano i dati. Le credenziali sono cifrate nel database e non vengono mai mostrate.',
      mod ? '<button class="btn btn-primary" id="co-nuovo">' + ic('plus') + 'Nuovo collegamento</button>' : roNota('Superutente'),
      (COL.length ? '' : vuoto('Nessun collegamento', 'Crea il primo collegamento al gestionale, poi abbinalo alle aziende dalla loro scheda.')) +
      '<div class="card"' + (COL.length ? '' : ' hidden') + '><div class="table-box"><table class="tbl"><thead><tr>' +
      '<th scope="col">Collegamento</th><th scope="col">Tipo</th><th scope="col">Server</th><th scope="col">Stato</th><th scope="col">Aziende abbinate</th><th scope="col">Ultima prova</th></tr></thead><tbody>' +
      COL.map(function (x) {
        return '<tr data-apri="' + esc(x.id) + '"><td class="prim">' + esc(x.nome) + (x.attivo ? '' : '<span class="sub">disattivato</span>') + '</td>' +
          '<td>' + esc(nomi[x.tipo] || x.tipo) + '</td>' +
          '<td class="mono">' + esc((x.parametri && x.parametri.host) || '—') + '</td>' +
          '<td>' + badge(STATO_COLL[x.stato]) + '</td>' +
          '<td>' + (x.aziende.length ? x.aziende.map(function (a) { return '<span class="chipacloud">' + esc(a.ragione_sociale) + '</span>'; }).join(' ') : '<span style="color:var(--testo-debole)">nessuna</span>') + '</td>' +
          '<td>' + quando(x.ultima_prova && x.ultima_prova.quando) + '</td></tr>';
      }).join('') + '</tbody></table></div></div>');
    var bn = $('#co-nuovo'); if (bn) bn.onclick = nuovoCollegamento;
    righeAttive(c, apriCollegamento);
  }
  function campiCatalogo(tipo) {
    var m = CATALOGO.filter(function (x) { return x.tipo === tipo; })[0];
    return m ? m.parametri : [];
  }
  function campoHtml(p, valore) {
    var v = (valore == null || valore === '') ? (p.predefinito || '') : valore;
    var hq = p.descrizione ? '<span class="hq" data-tip="' + esc(p.descrizione) + '" tabindex="0">?</span>' : '';
    var lab = '<div class="field" style="margin-bottom:10px"><label for="co-' + esc(p.chiave) + '">' + esc(p.etichetta) + hq + (p.obbligatorio ? '' : ' <span style="font-weight:400;color:var(--testo-debole)">(facoltativo)</span>') + '</label>';
    if (p.tipo === 'scelta') {
      return lab + '<select class="select" id="co-' + esc(p.chiave) + '">' + p.valori.map(function (x) { return '<option value="' + esc(x) + '"' + (x === v ? ' selected' : '') + '>' + esc(x) + '</option>'; }).join('') + '</select></div>';
    }
    var tipo = p.tipo === 'numero' ? ' type="number" inputmode="numeric"' : '';
    return lab + '<input class="input' + (p.tipo === 'numero' ? '' : ' mono') + '" id="co-' + esc(p.chiave) + '"' + tipo + ' value="' + esc(v) + '" autocomplete="off"></div>';
  }
  function campiForm(tipo, parametri) {
    parametri = parametri || {};
    var h = '';
    campiCatalogo(tipo).filter(function (p) { return p.tipo !== 'segreto'; }).forEach(function (p) { h += campoHtml(p, parametri[p.chiave]); });
    return h;
  }
  function campiSegreti(tipo, conNota) {
    var h = '';
    campiCatalogo(tipo).filter(function (p) { return p.tipo === 'segreto'; }).forEach(function (p) {
      var hq = p.descrizione ? '<span class="hq" data-tip="' + esc(p.descrizione) + '" tabindex="0">?</span>' : '';
      h += '<div class="field" style="margin-bottom:10px"><label for="co-' + esc(p.chiave) + '">' + esc(p.etichetta) + hq + '</label><input class="input mono" type="password" id="co-' + esc(p.chiave) + '" autocomplete="new-password"></div>';
    });
    return h;
  }
  function raccogliCampi(tipo) {
    var parametri = {}, segreti = {};
    campiCatalogo(tipo).forEach(function (p) {
      var el = $('#co-' + p.chiave); var v = el ? el.value : '';
      if (p.tipo === 'segreto') { if (v) segreti[p.chiave] = v; } else parametri[p.chiave] = v;
    });
    return { parametri: parametri, segreti: segreti };
  }
  function nuovoCollegamento() {
    function corpo(tipo) { return campiForm(tipo) + campiSegreti(tipo); }
    function provaPrima() {
      var tipo = $('#co-tipo').value;
      var campi = raccogliCampi(tipo);
      var esito = $('#co-prova-esito'), b = $('#co-prova-nuovo');
      b.disabled = true; b.innerHTML = ic('refresh') + ' Prova in corso…';
      api('POST', '/connettori/prova', { tipo: tipo, parametri: campi.parametri, segreti: campi.segreti })
        .then(function (r) {
          esito.innerHTML = r.ok
            ? '<div class="notice notice-ok">' + ic('ok') + '<span><b>Collegamento funzionante.</b>' + (r.versione ? ' Versione: ' + esc(r.versione) + '.' : '') + '</span></div>'
            : '<div class="notice notice-warn">' + ic('warn') + '<span><b>Non funziona.</b> ' + esc(r.motivo || 'verifica i parametri.') + '</span></div>';
        })
        .catch(function (e) { esito.innerHTML = '<div class="notice notice-warn">' + ic('warn') + '<span>' + esc(e.message) + '</span></div>'; })
        .then(function () { b.disabled = false; b.innerHTML = ic('refresh') + ' Prova collegamento'; });
    }
    dialogo('Nuovo collegamento',
      '<div class="field" style="margin-bottom:10px"><label for="co-tipo">Tipo di gestionale</label><select class="select" id="co-tipo">' + CATALOGO.map(function (x) { return '<option value="' + esc(x.tipo) + '">' + esc(x.nome) + '</option>'; }).join('') + '</select></div>' +
      '<div class="field" style="margin-bottom:10px"><label for="co-nome">Nome</label><input class="input" id="co-nome" placeholder="es. SAP produzione"></div>' +
      '<div id="co-campi">' + corpo(CATALOGO[0].tipo) + '</div>' +
      '<div style="display:flex;align-items:center;gap:10px;margin:2px 0 12px"><button class="btn btn-secondary btn-sm" type="button" id="co-prova-nuovo">' + ic('refresh') + ' Prova collegamento</button><span style="font-size:12px;color:var(--testo-debole)">controlla che funzioni, senza salvare</span></div>' +
      '<div id="co-prova-esito" aria-live="polite"></div>' +
      '<p class="ro-note">' + ic('shield') + 'La password è cifrata e non viene mai mostrata. Prova il collegamento qui prima di salvarlo.</p>',
      function () {
        var tipo = $('#co-tipo').value, nome = $('#co-nome').value.trim();
        if (!nome) { toast('Il nome è obbligatorio.', 'err'); $('#co-nome').focus(); return false; }
        var campi = raccogliCampi(tipo);
        return api('POST', '/collegamenti', { nome: nome, tipo: tipo, parametri: campi.parametri, segreti: campi.segreti })
          .then(function (r) { toast('Collegamento creato'); renderCollegamenti(); setTimeout(function () { apriCollegamento(r.id); }, 0); })
          .catch(function (e) { toast(e.message, 'err'); return false; });
      }, 'Crea');
    $('#co-tipo').onchange = function () { $('#co-campi').innerHTML = corpo(this.value); $('#co-prova-esito').innerHTML = ''; };
    $('#co-prova-nuovo').onclick = provaPrima;
  }
  function apriCollegamento(id) {
    var mostra = function (x) {
      var mod = can('collegamenti'), nomi = {}; CATALOGO.forEach(function (m) { nomi[m.tipo] = m.nome; });
      var ultima = x.ultima_prova || {};
      var righePar = campiCatalogo(x.tipo).filter(function (p) { return p.tipo !== 'segreto'; }).map(function (p) {
        return '<div><dt>' + esc(p.etichetta) + '</dt><dd class="mono">' + esc(x.parametri[p.chiave] == null ? '—' : x.parametri[p.chiave]) + '</dd></div>';
      }).join('');
      apriPannello('<div><h2 id="drawer-titolo">' + esc(x.nome) + '</h2><div class="sub">' + esc(nomi[x.tipo] || x.tipo) + '</div></div>',
        '<dl class="dl"><dt>Stato</dt><dd>' + badge(STATO_COLL[x.stato]) + '</dd>' +
        '<dt>Parametri</dt><dd style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0 16px">' + (righePar || '<span class="ro-note">Nessun parametro.</span>') + '</dd>' +
        '<dt>Credenziali</dt><dd>' + (x.segreti_impostati
          ? 'Impostate' + (x.segreti_cambiati_il ? ' il ' + esc(dataOra(x.segreti_cambiati_il)) + (x.segreti_cambiati_da ? ' da ' + esc(x.segreti_cambiati_da) : '') : '') + ' — non si leggono né si mostrano.'
          : '<span style="color:var(--stato-attenzione)">' + ic('warn') + ' Mancano: il collegamento non può connettersi.</span>') + '</dd>' +
        (ultima.versione || ultima.motivo || ultima.quando ? '<dt>Ultima prova</dt><dd>' + quando(ultima.quando) + (ultima.versione || ultima.motivo ? ' · ' + esc(ultima.ok ? 'Riuscita' + (ultima.versione ? ' (' + ultima.versione + ')' : '') : ultima.motivo) : '') + '</dd>' : '') +
        '<dt>Aziende abbinate</dt><dd>' + (x.aziende.length ? x.aziende.map(function (a) { return '<span class="chipacloud">' + esc(a.ragione_sociale) + (a.codice_origine ? ' <span class="mono">(' + esc(a.codice_origine) + ')</span>' : '') + '</span>'; }).join(' ') : '<span class="ro-note">Nessuna: abbina dalla scheda dell\'azienda.</span>') + '</dd></dl>' +
        (mod ? '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">' +
          '<button class="btn btn-primary btn-sm" id="co-prova">' + ic('refresh') + 'Prova collegamento</button>' +
          '<button class="btn btn-secondary btn-sm" id="co-mod">Modifica…</button>' +
          '<button class="btn btn-secondary btn-sm" id="co-pw">Cambia password</button>' +
          '<button class="btn btn-ghost btn-sm" id="co-att">' + (x.attivo ? 'Disattiva' : 'Riattiva') + '</button></div>' : ''));
      var bp = $('#co-prova'); if (bp) bp.onclick = function () {
        bp.disabled = true; bp.innerHTML = ic('refresh') + ' Prova in corso…';
        api('POST', '/collegamenti/' + encodeURIComponent(x.id) + '/prova').then(function (r) {
          toast('Prova: ' + (r.prova.ok ? 'collegamento funzionante' : 'collegamento non funzionante'));
          return api('GET', '/collegamenti');
        }).then(function (d) { COL = d; apriCollegamento(x.id); })
          .catch(function (e) { toast(e.message, 'err'); bp.disabled = false; });
      };
      var bm = $('#co-mod'); if (bm) bm.onclick = function () { modificaCollegamento(x); };
      var bw = $('#co-pw'); if (bw) bw.onclick = function () { cambiaPassword(x); };
      var ba = $('#co-att'); if (ba) ba.onclick = function () {
        var attiva = !x.attivo;
        api('PUT', '/collegamenti/' + encodeURIComponent(x.id), { attivo: attiva })
          .then(function () { toast(attiva ? 'Collegamento riattivato' : 'Collegamento disattivato'); return api('GET', '/collegamenti'); })
          .then(function (d) { COL = d; apriCollegamento(x.id); }).catch(function (e) { toast(e.message, 'err'); });
      };
    };
    var x = COL.filter(function (k) { return k.id === id; })[0];
    if (x) mostra(x); else api('GET', '/collegamenti').then(function (d) { COL = d; var g = d.filter(function (k) { return k.id === id; })[0]; if (g) mostra(g); });
  }
  function modificaCollegamento(x) {
    dialogo('Modifica «' + x.nome + '»',
      '<div class="field" style="margin-bottom:10px"><label for="co-nome">Nome</label><input class="input" id="co-nome" value="' + esc(x.nome) + '"></div>' + campiForm(x.tipo, x.parametri),
      function () {
        var campi = raccogliCampi(x.tipo);
        return api('PUT', '/collegamenti/' + encodeURIComponent(x.id), { nome: $('#co-nome').value.trim(), parametri: campi.parametri })
          .then(function () { toast('Collegamento aggiornato'); return api('GET', '/collegamenti'); })
          .then(function (d) { COL = d; if (location.hash.indexOf('collegamenti') > -1) disegnaCollegamenti(); apriCollegamento(x.id); })
          .catch(function (e) { toast(e.message, 'err'); return false; });
      }, 'Salva');
  }
  function cambiaPassword(x) {
    var campi = campiCatalogo(x.tipo).filter(function (p) { return p.tipo === 'segreto'; });
    dialogo('Cambiare la password di «' + x.nome + '»?',
      '<p style="margin-top:0;font-size:13.5px">La nuova password sostituisce quella salvata; non viene mostrata da nessuna parte.</p>' + campiSegreti(x.tipo),
      function () {
        var segreti = {}; campi.forEach(function (p) { var v = $('#co-' + p.chiave).value; if (v) segreti[p.chiave] = v; });
        if (!Object.keys(segreti).length) { toast('Inserisci la nuova password.', 'err'); return false; }
        return api('PUT', '/collegamenti/' + encodeURIComponent(x.id), { segreti: segreti })
          .then(function () { toast('Password cambiata'); apriCollegamento(x.id); })
          .catch(function (e) { toast(e.message, 'err'); return false; });
      }, 'Cambia');
  }

  /* ============================== IMPORTAZIONI (§8.4) ============================== */
  /* Griglia azienda x tipo di dato: lo stato lo scrive il motore in
     erp.sincronizzazioni (sia lo scheduler che Dagster), qui si legge e si puo'
     forzare un'importazione. */
  var ETICHETTE = { soggetti: 'Clienti', articoli: 'Articoli', listini: 'Listini', codici: 'Tabelle codici',
                    documenti_vendita: 'Documenti vendita', giacenze: 'Giacenze' };
  function renderImportazioni() {
    var c = $('#content'); caricamento(c);
    Promise.all([api('GET', '/importazioni'), api('GET', '/connettori').catch(function () { return []; })]).then(function (r) {
      var d = r[0], CAT = r[1];
      var entita = [];
      CAT.forEach(function (x) { Object.keys(x.entita || {}).forEach(function (e) { if (entita.indexOf(e) < 0) entita.push(e); }); });
      var sync = {}; d.sincronizzazioni.forEach(function (s) { sync[s.azienda + '|' + s.entita] = s; });
      var mod = can('importazioni');
      function cella(az, ent) {
        var s = sync[az + '|' + ent];
        var btn = mod ? '<button class="btn btn-ghost btn-sm" data-avvia="' + esc(az) + '|' + esc(ent) + '" title="Avvia ora">' + ic('refresh') + '<span class="sr">Avvia ora</span></button>' : '';
        if (!s) return '<div class="icell" role="cell"><div class="t t-na">' + ic('neut') + 'Mai importata</div><div class="s">' + btn + '</div></div>';
        var m = s.esito === 'ok' ? ['t-ok', 'ok', 'Riuscita'] : s.esito === 'errore' ? ['t-err', 'err', 'Fallita'] : ['t-warn', 'clock', 'In corso'];
        return '<div class="icell" role="cell"><div class="t ' + m[0] + '">' + ic(m[1]) + m[2] + '</div>' +
          '<div class="s">' + quando(s.completata_il || s.iniziata_il) + (s.righe_lette != null ? ' · ' + s.righe_lette + ' righe' : '') +
          (s.esito === 'errore' && s.errore ? '<div class="avviso-riga" title="' + esc(s.errore) + '">' + ic('warn') + '<span>' + esc(String(s.errore).slice(0, 60)) + '</span></div>' : '') + btn + '</div></div>';
      }
      c.innerHTML = schermata('Importazioni', 'Esito dell\'ultima importazione per azienda e tipo di dato. Il programma parte da solo secondo le frequenze; qui puoi forzare un\'importazione.',
        may('dagster') ? '<a class="btn btn-secondary" href="#/esterno/dagster">' + ic('ext') + 'Gestione importazioni (Dagster)</a>' : '',
        (d.aziende.length ? '' : vuoto('Nessuna azienda', 'Le importazioni partono quando un\'azienda è collegata a un gestionale.')) +
        '<div class="card"' + (d.aziende.length ? '' : ' hidden') + '><div style="overflow:auto"><div class="imp" style="grid-template-columns:150px repeat(' + entita.length + ',minmax(96px,1fr))" role="table" aria-label="Importazioni per azienda e tipo di dato">' +
        '<div class="ih" role="columnheader">Azienda</div>' + entita.map(function (e) { return '<div class="ih" role="columnheader">' + esc(ETICHETTE[e] || e) + '</div>'; }).join('') +
        d.aziende.map(function (a) {
          return '<div class="ih" role="rowheader" style="font-weight:400;text-transform:none">' + esc(a.ragione_sociale) + (a.collegamento ? '' : '<span class="sub">non collegata</span>') + '</div>' +
            entita.map(function (e) { return cella(a.codice, e); }).join('');
        }).join('') + '</div></div></div>' +
        '<div class="notice notice-info" style="margin-top:16px">' + ic('info') + '<span>Le frequenze predefinite sono in <span class="mono">connettori/scheda.py</span> (documenti ogni 15 min, anagrafiche ogni ora, listini e giacenze ogni notte). Il piano lo esegue Dagster.</span></div>');
      $$('#content [data-avvia]').forEach(function (b) {
        b.onclick = function () {
          var p = b.dataset.avvia.split('|'); var az = p[0], ent = p[1];
          b.disabled = true;
          api('POST', '/importazioni/avvia', { azienda: az, entita: ent })
            .then(function () { toast('Importazione avviata: ' + (ETICHETTE[ent] || ent)); renderImportazioni(); })
            .catch(function (e) { toast(e.message, 'err'); b.disabled = false; });
        };
      });
    }).catch(function (e) { errore(c, e, renderImportazioni); });
  }

  /* ============================== ASPETTO (§11) ============================== */
  var PRESET = { Decobrands: { 'marchio': '#0d6efd', 'marchio-secondario': '#475a6b', 'marchio-testo': '#ffffff' },
                 Verde: { 'marchio': '#2e7d32', 'marchio-secondario': '#5a6b57', 'marchio-testo': '#ffffff' },
                 Rossa: { 'marchio': '#c62828', 'marchio-secondario': '#7a4f4f', 'marchio-testo': '#ffffff' } };
  var CAMPI = [['marchio', 'Colore di marca', 'Pulsante principale, voce attiva, focus'], ['marchio-testo', 'Testo sul colore di marca', 'Il testo dei pulsanti principali'],
               ['marchio-secondario', 'Secondario', 'Accento neutro dei dettagli'], ['sfondo-pagina', 'Sfondo pagina', 'Lo sfondo di tutto il portale (tema chiaro)'],
               ['superficie', 'Superficie', 'Card, tabelle e pannelli (tema chiaro)']];
  function lum(h) { var v = [1, 3, 5].map(function (i) { var c = parseInt(h.substr(i, 2), 16) / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; }
  function contrasto(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  /* Stessa correzione di logica.correggi: il colore piu' vicino che raggiunge AA. */
  function correggi(sf, tx) {
    var nero = lum(tx) > 0.5, r = parseInt(sf.substr(1, 2), 16), g = parseInt(sf.substr(3, 2), 16), b = parseInt(sf.substr(5, 2), 16);
    for (var p = 0; p <= 100; p++) {
      var f = p / 100, c = nero ? [r * (1 - f), g * (1 - f), b * (1 - f)] : [r + (255 - r) * f, g + (255 - g) * f, b + (255 - b) * f];
      var h = '#' + c.map(function (x) { var s = Math.round(x).toString(16); return s.length < 2 ? '0' + s : s; }).join('');
      if (contrasto(h, tx) >= 4.5) return h;
    }
    return nero ? '#000000' : '#ffffff';
  }
  function renderAspetto() {
    var c = $('#content'); caricamento(c);
    api('GET', '/aspetto').then(function (d) {
      var mod = can('aspetto'), pal = JSON.parse(JSON.stringify(d.palette));
      c.innerHTML = schermata('Aspetto', 'Logo, testi e colori. Si configurano solo i colori indicati: tutto il resto ne deriva.', mod ? '' : roNota('Gestione sistemi'),
        '<div class="card"><div class="c-head"><div><h2>Identità <span class="fase2">Seconda fase</span></h2><p class="h-intro">Oggi si configurano da file (branding/ e .env): qui sono in sola lettura.</p></div></div>' +
        '<dl class="dl" style="padding:4px 18px 16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0 18px">' +
        [['Nome dell\'azienda', d.identita.azienda], ['Nome dell\'assistente', d.identita.assistente], ['Messaggio di benvenuto', d.identita.benvenuto], ['Piè di pagina', d.identita.pie]]
          .map(function (x) { return '<div><dt>' + x[0] + '</dt><dd>' + esc(x[1] || '—') + '</dd></div>'; }).join('') + '</dl></div>' +
        '<div class="card"><div class="c-head"><div><h2>Colori configurabili</h2><p class="h-intro">' + (d.aggiornato_da ? 'Ultima modifica: ' + esc(d.aggiornato_da) + ', ' + esc(dataOra(d.aggiornato_il)) : 'Palette predefinita') + '</p></div></div>' +
        (mod ? '<div class="preset" style="padding-top:14px"><span class="ro-note">Parti da:</span>' + Object.keys(PRESET).map(function (k) { return '<button class="btn btn-secondary btn-sm" data-preset="' + k + '">' + k + '</button>'; }).join('') + '</div>' : '') +
        '<div style="padding:0 18px 16px" class="asp-grid">' + CAMPI.map(function (x) {
          return '<div class="asp-swatch"><div><label for="c-' + x[0] + '">' + x[1] + '</label><div style="font-size:11px;color:var(--testo-debole)">' + x[2] + '</div></div>' +
            '<div style="display:flex;align-items:center;gap:8px"><span class="hex" id="h-' + x[0] + '"></span><input type="color" id="c-' + x[0] + '" data-k="' + x[0] + '"' + (mod ? '' : ' disabled') + '></div></div>'; }).join('') + '</div>' +
        '<div id="asp-contrasto" aria-live="polite"></div>' +
        (mod ? '<div style="padding:12px 18px;border-top:1px solid var(--bordo);display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap">' +
          '<button class="btn btn-secondary btn-sm" id="asp-reset">Ripristina predefiniti</button><button class="btn btn-primary btn-sm" id="asp-salva">' + ic('save') + 'Salva</button></div>' : '') + '</div>' +
        '<div class="card"><div class="c-head"><div><h2>Anteprima dal vivo</h2><p class="h-intro">Tema chiaro e scuro con i colori scelti, prima di salvare.</p></div></div><div style="padding:16px 18px" class="asp-grid" id="asp-anteprima"></div></div>' +
        '<div class="card"><div class="c-head"><div><h2>Il tuo tema</h2><p class="h-intro">Vale solo per questo browser.</p></div></div><div class="tema-sel" style="padding:14px 18px;max-width:420px" id="asp-tema">' +
        '<button data-tema="" aria-pressed="false">Automatico</button><button data-tema="chiaro" aria-pressed="false">Chiaro</button><button data-tema="scuro" aria-pressed="false">Scuro</button></div></div>');
      function aggiorna() {
        CAMPI.forEach(function (x) { $('#c-' + x[0]).value = pal[x[0]]; $('#h-' + x[0]).textContent = pal[x[0]]; });
        var r = contrasto(pal.marchio, pal['marchio-testo']), ok = r >= 4.5, prop = ok ? '' : correggi(pal.marchio, pal['marchio-testo']);
        $('#asp-contrasto').innerHTML = '<div class="notice ' + (ok ? 'notice-ok' : 'notice-warn') + '" style="margin:0 18px 14px">' + ic(ok ? 'ok' : 'warn') +
          '<span><b>Contrasto del pulsante principale: ' + r.toFixed(1).replace('.', ',') + ':1</b> — ' + (ok ? 'leggibile, rispetta lo standard WCAG AA.' :
          'il testo sul pulsante sarà poco leggibile (serve almeno 4,5:1). Correzione proposta: <span class="mono">' + prop + '</span>.' + (mod ? ' <button class="btn btn-secondary btn-sm" id="asp-correggi" style="margin-left:6px">Applica la correzione</button>' : '')) + '</span></div>';
        var bc = $('#asp-correggi'); if (bc) bc.onclick = function () { pal.marchio = prop; aggiorna(); $('#c-marchio').focus(); };
        var bs = $('#asp-salva'); if (bs) { bs.disabled = !ok; bs.title = ok ? '' : 'Correggi il contrasto prima di salvare'; }
        $('#asp-anteprima').innerHTML = ['chiaro', 'scuro'].map(function (t) {
          var sc = t === 'scuro', v = function (n) { return 'var(--prev-' + n + '-' + (sc ? 'scuro' : 'chiaro') + ')'; };
          var sfondo = sc ? v('sfondo') : pal['sfondo-pagina'], sup = sc ? v('sup') : pal.superficie;
          return '<div class="preview-mini"><div class="pv-head">' + ic('spark') + '<span>Login e amministrazione · ' + t + '</span></div><div class="pv-body" style="background:' + sfondo + '">' +
            '<div style="background:' + sup + ';border:1px solid var(--prev-bordo);border-radius:10px;padding:12px;max-width:260px;margin:0 auto"><div style="text-align:center;margin-bottom:8px"><b style="font-size:12px;color:' + v('testo') + '">Accedi all\'assistente</b></div>' +
            '<div style="height:20px;background:' + v('campo') + ';border-radius:5px;margin-bottom:6px"></div><div style="height:20px;background:' + v('campo') + ';border-radius:5px;margin-bottom:8px"></div>' +
            '<div style="height:28px;background:' + pal.marchio + ';border-radius:6px;display:grid;place-items:center"><span style="font-size:11px;color:' + pal['marchio-testo'] + ';font-weight:600">Accedi</span></div>' +
            '<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap"><span class="badge b-stato-critico">' + ic('crit') + 'Critica</span><span class="badge b-stato-ok">' + ic('ok') + 'Riuscita</span></div></div></div></div>';
        }).join('');
      }
      CAMPI.forEach(function (x) { $('#c-' + x[0]).oninput = function () { pal[x[0]] = this.value; aggiorna(); }; });
      $$('#content [data-preset]').forEach(function (b) { b.onclick = function () { var p = PRESET[b.dataset.preset]; Object.keys(p).forEach(function (k) { pal[k] = p[k]; }); aggiorna(); toast('Palette ' + b.dataset.preset + ': non ancora salvata'); }; });
      var br = $('#asp-reset'); if (br) br.onclick = function () { pal = JSON.parse(JSON.stringify(d.predefinita)); aggiorna(); toast('Predefiniti ripristinati: non ancora salvati'); };
      var bs = $('#asp-salva'); if (bs) bs.onclick = function () {
        api('PUT', '/aspetto', { palette: pal }).then(function () {
          toast('Aspetto salvato');
          var l = $('link[href^="/amministrazione/tema.css"]'); l.href = '/amministrazione/tema.css?v=' + Date.now();
          d.palette = JSON.parse(JSON.stringify(pal));
        }).catch(function (e) { toast(e.message, 'err'); });
      };
      $$('#asp-tema button').forEach(function (b) { b.onclick = function () { tema(b.dataset.tema); $$('#asp-tema button').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); }); }; b.setAttribute('aria-pressed', (document.documentElement.getAttribute('data-tema') || '') === b.dataset.tema ? 'true' : 'false'); });
      aggiorna();
    }).catch(function (e) { errore(c, e, renderAspetto); });
  }

  /* ============================== tooltip ("?" dei campi) ============================== */
  /* Un solo elemento fisso, sopra tutto (anche la modale): il testo arriva dal
     data-tip delle icone ".hq". Cosi' il tooltip non viene tagliato dall'overflow
     della modale e appare sempre sopra. */
  function mostraTip(el) {
    var t = $('#tip'), testo = el.getAttribute('data-tip') || '';
    if (!testo) return;
    t.textContent = testo;
    t.hidden = false; t.style.left = '-9999px'; t.style.top = '-9999px';
    var r = el.getBoundingClientRect(), w = t.offsetWidth, h = t.offsetHeight;
    var x = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
    var y = (r.top - h - 8 >= 8) ? r.top - h - 8 : r.bottom + 8;
    t.style.left = x + 'px'; t.style.top = y + 'px';
  }
  function nascondiTip() { var t = $('#tip'); if (t) t.hidden = true; }
  document.addEventListener('mouseover', function (e) { var q = e.target.closest ? e.target.closest('.hq') : null; if (q) mostraTip(q); });
  document.addEventListener('mouseout', function (e) { if (e.target.closest && e.target.closest('.hq')) nascondiTip(); });
  document.addEventListener('focusin', function (e) { if (e.target.classList && e.target.classList.contains('hq')) mostraTip(e.target); });
  document.addEventListener('focusout', function (e) { if (e.target.classList && e.target.classList.contains('hq')) nascondiTip(); });
  window.addEventListener('scroll', nascondiTip, true);
  window.addEventListener('resize', nascondiTip);

  /* ============================== router ============================== */
  var SCHERMATE = { panoramica: ['panoramica', 'Panoramica', renderPanoramica], fonti: ['fonti', 'Fonti', renderFonti],
                    'vedi-come': ['vedicome', 'Vedi come', renderVediCome], anomalie: ['anomalie', 'Anomalie', renderAnomalie],
                    registro: ['registro', 'Registro modifiche', renderRegistro], aspetto: ['aspetto', 'Aspetto', renderAspetto],
                    utenti: ['utenti', 'Utenti', renderUtenti], gruppi: ['gruppi', 'Gruppi', renderGruppi], profili: ['profili', 'Profili amministrativi', renderProfili], aziende: ['aziende', 'Aziende', renderAziende],
                    collegamenti: ['collegamenti', 'Gestionali', renderCollegamenti], importazioni: ['importazioni', 'Importazioni', renderImportazioni],
                    gestiti: ['gestiti', 'I miei gruppi', renderGestiti] };
  function instrada() {
    chiudiPannello();
    var h = (location.hash || '#/scelta').replace(/^#\//, ''), seg = h.split('/')[0];
    if (seg === '' || seg === 'scelta') { mostraScelta(); return; }
    $('#view-scelta').hidden = true; $('#view-app').hidden = false;
    if (seg === 'esterno') {
      var k = h.split('/')[1];
      $$('.side a').forEach(function (a) { var on = a.dataset.area === 'esterno-' + k; a.classList.toggle('active', on); if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
      renderEsterno(k); return;
    }
    var s = SCHERMATE[seg];
    if (!s || !may(s[0])) { if (seg !== IO.home) location.replace('#/' + IO.home); return; }
    $$('.side a').forEach(function (a) { var on = a.dataset.area === seg; a.classList.toggle('active', on); if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    document.title = s[1] + ' · Amministrazione';
    s[2]();
  }
  window.addEventListener('hashchange', function () { laterale(false); instrada(); var c = $('#content'); if (!$('#view-app').hidden) c.focus({ preventScroll: true }); });

  /* ============================== avvio ============================== */
  Promise.all([api('GET', '/io'), api('GET', '/aziende')]).then(function (r) {
    IO = r[0]; AZIENDE = r[1];
    $('#nome-assistente').textContent = IO.assistente;
    $('#p-nome').textContent = IO.nome; $('#p-ruolo').textContent = IO.ruoli[0] || '';
    $('#p-ini').textContent = IO.nome.split(' ').map(function (x) { return x.charAt(0); }).join('').slice(0, 2).toUpperCase();
    $('#p-ruoli').textContent = 'Ruoli: ' + IO.ruoli.join(' · ');
    $$('#vai-chat, #card-chat').forEach(function (a) { a.href = IO.chat; });
    $$('#card-admin, .brand').forEach(function (a) { a.href = '#/' + IO.home; });
    /* Voci senza permesso: NON mostrate (spec §3.4), non solo disattivate. */
    $$('.side a[data-voce]').forEach(function (a) { a.hidden = !may(a.dataset.voce); });
    $$('.side .gl[data-gruppo]').forEach(function (g) { g.hidden = !g.dataset.gruppo.split(' ').some(may); });
    var sel = $('#azienda-sel');
    if (AZIENDE.length === 1) { sel.innerHTML = '<option value="' + esc(AZIENDE[0].codice) + '">' + esc(AZIENDE[0].ragione_sociale) + '</option>'; sel.disabled = true; AZ = AZIENDE[0].codice; }
    else sel.innerHTML = '<option value="">Tutte le aziende</option>' + AZIENDE.map(function (a) { return '<option value="' + esc(a.codice) + '">' + esc(a.ragione_sociale) + '</option>'; }).join('');
    if (!AZIENDE.length) { sel.innerHTML = '<option>Nessuna azienda abilitata</option>'; sel.disabled = true; }
    tema(document.documentElement.getAttribute('data-tema') || '');
    if (may('anomalie')) api('GET', '/anomalie').then(function (d) { ANOM = d; contatore(); }).catch(function () {});
    instrada();
  }).catch(function (e) {
    document.body.innerHTML = '<div class="scelta"><div class="inner"><div class="card pad-5" role="alert"><h1 class="page-title">Amministrazione non disponibile</h1><p class="page-sub">' + esc(e.message) + '</p><p><a class="btn btn-primary" href="/c/new">Vai alla chat</a></p></div></div></div>';
  });
})();
