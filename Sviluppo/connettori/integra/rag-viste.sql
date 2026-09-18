-- Viste rag_* del database parallelo "rag" (postgres_fdw verso il gestionale
-- Integra). Sono la fonte che legge il connettore (SPECIFICA-CONNETTORI.md §10).
-- Le viste sono hardcoded su azi_cdazi = '001': per la multi-azienda vanno
-- parametrizzate o duplicate per azienda (v. README.md).

-- ============ rag_prodotti ============
CREATE OR REPLACE VIEW public.rag_prodotti AS
SELECT t0.azi_cdazi, t0.pro_id, t0.pro_cod, t0.pro_descr,
    t0.pro_clvcod01 AS cod_gruppo_merceologico, c1.clv_descr AS descr_gruppo_merceologico,
    t0.pro_clvcod02 AS cod_gruppo_statistico, c2.clv_descr AS descr_gruppo_statistico,
    t0.pro_clvcod03 AS cod_clv_tipoarticolo, c3.clv_descr AS descr_clv_tipoarticolo,
    t0.pro_cod1 AS codice_alternativo, t0.pro_cod2 AS codice_esterno,
    t4.ele_valoreb AS incluso_b2b,
    COALESCE(t5.ele_valorec, '') AS ubicazione, COALESCE(t6.ele_valorec, '') AS cod_famiglia, COALESCE(t6.clv_descr, '') AS descr_famiglia,
    COALESCE(t7.ele_valorec, '') AS cod_linea, COALESCE(t7.clv_descr, '') AS descr_linea,
    COALESCE(t8.ele_valorec, '') AS cod_diametro_esterno, COALESCE(t8.clv_descr, '') AS descr_diametro_esterno,
    COALESCE(t9.ele_valorec, '') AS cod_altezza, COALESCE(t9.clv_descr, '') AS descr_altezza,
    t0.pro_obsoleto AS prodotto_obsoleto,
    to_timestamp(extract(epoch from t0.pro_dtins) + t0.pro_orains * 3600 + t0.pro_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from t0.pro_dtvar) + t0.pro_oravar * 3600 + t0.pro_minvar * 60) AS data_ultmod
FROM integra.prodotti t0
    LEFT JOIN integra.classivoci c1 ON t0.azi_cdazi = c1.azi_cdazi AND t0.pro_cldcod01 = c1.cld_cod AND t0.pro_clvcod01 = c1.clv_cod
    LEFT JOIN integra.classivoci c2 ON t0.azi_cdazi = c2.azi_cdazi AND t0.pro_cldcod02 = c2.cld_cod AND t0.pro_clvcod02 = c2.clv_cod
    LEFT JOIN integra.classivoci c3 ON t0.azi_cdazi = c3.azi_cdazi AND t0.pro_cldcod03 = c3.cld_cod AND t0.pro_clvcod03 = c3.clv_cod
    LEFT JOIN (SELECT azi_cdazi, ele_valoreb, ele_key1p FROM integra.entleg WHERE ele_tipkeyp = 'ART' AND ele_tipkeys = 'EFU' AND ele_key1s = 'WEB') t4 ON t0.azi_cdazi = t4.azi_cdazi AND btrim(t4.ele_key1p)::integer = t0.pro_id
    LEFT JOIN (SELECT azi_cdazi, ele_valorec, ele_key1p FROM integra.entleg WHERE ele_tipkeyp = 'ART' AND ele_tipkeys = 'EFU' AND ele_key1s = 'UBICA') t5 ON t0.azi_cdazi = t5.azi_cdazi AND btrim(t5.ele_key1p)::integer = t0.pro_id
    LEFT JOIN (SELECT e.azi_cdazi, e.ele_valorec, e.ele_key1p, cv.clv_descr FROM integra.entleg e JOIN integra.classivoci cv ON e.azi_cdazi = cv.azi_cdazi AND cv.cld_cod = 'FAM' AND e.ele_valorec = cv.clv_cod WHERE e.ele_tipkeyp = 'ART' AND e.ele_tipkeys = 'EFU' AND e.ele_key1s = 'FAM') t6 ON t0.azi_cdazi = t6.azi_cdazi AND btrim(t6.ele_key1p)::integer = t0.pro_id
    LEFT JOIN (SELECT e.azi_cdazi, e.ele_valorec, e.ele_key1p, cv.clv_descr FROM integra.entleg e JOIN integra.classivoci cv ON e.azi_cdazi = cv.azi_cdazi AND cv.cld_cod = 'LIN' AND e.ele_valorec = cv.clv_cod WHERE e.ele_tipkeyp = 'ART' AND e.ele_tipkeys = 'EFU' AND e.ele_key1s = 'LIN') t7 ON t0.azi_cdazi = t7.azi_cdazi AND btrim(t7.ele_key1p)::integer = t0.pro_id
    LEFT JOIN (SELECT e.azi_cdazi, e.ele_valorec, e.ele_key1p, cv.clv_descr FROM integra.entleg e JOIN integra.classivoci cv ON e.azi_cdazi = cv.azi_cdazi AND cv.cld_cod = 'DIE' AND e.ele_valorec = cv.clv_cod WHERE e.ele_tipkeyp = 'ART' AND e.ele_tipkeys = 'EFU' AND e.ele_key1s = 'DIE') t8 ON t0.azi_cdazi = t8.azi_cdazi AND btrim(t8.ele_key1p)::integer = t0.pro_id
    LEFT JOIN (SELECT e.azi_cdazi, e.ele_valorec, e.ele_key1p, cv.clv_descr FROM integra.entleg e JOIN integra.classivoci cv ON e.azi_cdazi = cv.azi_cdazi AND cv.cld_cod = 'H' AND e.ele_valorec = cv.clv_cod WHERE e.ele_tipkeyp = 'ART' AND e.ele_tipkeys = 'EFU' AND e.ele_key1s = 'H') t9 ON t0.azi_cdazi = t9.azi_cdazi AND btrim(t9.ele_key1p)::integer = t0.pro_id
WHERE t0.pro_id > 0 AND t0.azi_cdazi = '001';
ALTER VIEW public.rag_prodotti OWNER TO postgres;

-- ============ rag_clienti ============
DROP VIEW IF EXISTS public.rag_clienti;
CREATE VIEW public.rag_clienti AS
SELECT ca.cla_cod AS id_cliente,
    ca.cla_codstr AS codice_cliente,
    c.cli_cdcli AS id_master,
    c.cli_cdclistr AS codice_master,
    c.cli_rgsoc AS ragione_sociale,
    c.cli_rgsoc2 AS ragione_sociale_2,
    c.cli_cognome AS cognome,
    c.cli_nome AS nome,
    c.cli_persgiur AS forma_giuridica,
    c.cli_indir AS indirizzo,
    c.cli_indir2 AS indirizzo_2,
    c.cli_cap AS cap,
    c.cli_citta AS citta,
    c.cli_prov AS provincia,
    c.cli_tel AS telefono,
    c.cli_fax AS fax,
    c.cli_email AS email,
    c.cli_web AS web,
    c.cli_pecdest AS pec,
    c.cli_piva AS partita_iva,
    c.cli_cfisc AS codice_fiscale,
    c.cli_regcod AS regione,
    c.cli_stacod AS stato,
    ca.cla_pagcod AS codice_pagamento,
    ca.cla_tlscod AS codice_listino,
    ca.cla_caccod AS codice_conto,
    ca.cla_porcod AS codice_porto,
    ca.cla_vetcod1 AS codice_vettore,
    ca.cla_specod AS codice_spedizione,
    ca.cla_valcod AS codice_valuta,
    ca.cla_ivacod AS codice_iva,
    ca.cla_zoncod AS codice_zona,
    ca.cla_agecod AS codice_agente,
    ca.cla_tipofatt AS tipo_fatturazione,
    ca.cla_minfat AS importo_minimo_fattura,
    ca.cla_fidotot AS fido_totale,
    ca.cla_fidocont AS fido_concessione,
    ca.cla_fidoscad AS fido_scadenze,
    ca.cla_iban AS iban,
    ca.cla_swift AS swift_bic,
    ca.cla_bic AS bic,
    ca.cla_abicod AS abi,
    ca.cla_cabcod AS cab,
    ca.cla_fatturaele AS fatturazione_elettronica,
    c.cli_obsoleto AS cli_obsoleto,
    to_timestamp(extract(epoch from c.cli_dtins) + c.cli_orains * 3600 + c.cli_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from c.cli_dtvar) + c.cli_oravar * 3600 + c.cli_minvar * 60) AS data_modifica
FROM integra.clienti c
    JOIN integra.cliazi ca ON ca.azi_cdazi = '001' AND ca.cla_tipo = 'C' AND ca.cla_clicdcli = c.cli_cdcli AND ca.cla_obsoleto = 0
WHERE c.cli_obsoleto = 0 AND c.cli_cdcli > 0;
ALTER VIEW public.rag_clienti OWNER TO postgres;

-- ============ rag_indirizzi_clienti ============
CREATE OR REPLACE VIEW public.rag_indirizzi_clienti AS
SELECT d.dst_id AS id_destinazione,
    d.dst_clicdcli AS id_cliente,
    c.cli_cdclistr AS codice_cliente,
    c.cli_rgsoc AS ragione_sociale,
    d.dst_dsfcod AS codice_tipo_destinazione,
    c.cli_indir AS indirizzo,
    c.cli_indir2 AS indirizzo_2,
    c.cli_cap AS cap,
    c.cli_citta AS citta,
    c.cli_prov AS provincia,
    d.dst_abituale AS flag_abituale,
    d.dst_spedizione AS flag_spedizione,
    d.dst_km AS km,
    d.dst_ordinamento AS ordinamento,
    d.dst_zoncod AS codice_zona,
    d.dst_agecod AS codice_agente,
    d.dst_ggprep AS giorni_preparazione,
    d.dst_linlayout AS layout_linea,
    d.dst_vetcod AS codice_vettore,
    d.dst_porcod AS codice_porto,
    d.dst_fteriftesto AS referente,
    d.dst_fterifnumero AS telefono_referente,
    d.dst_obsoleto AS stato_destinazione,
    to_timestamp(extract(epoch from d.dst_dtins) + d.dst_orains * 3600 + d.dst_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from d.dst_dtvar) + d.dst_oravar * 3600 + d.dst_minvar * 60) AS data_modifica
FROM integra.destinazioni d
    JOIN integra.clienti c ON c.cli_cdcli = d.dst_clicdcli AND c.cli_obsoleto = 0
WHERE d.dst_obsoleto = 0 AND d.dst_clicdcli > 0;
ALTER VIEW public.rag_indirizzi_clienti OWNER TO postgres;

-- ============ rag_pagamenti_clienti ============
CREATE OR REPLACE VIEW public.rag_pagamenti_clienti AS
SELECT ca.cla_clicdcli AS id_cliente,
    c.cli_cdclistr AS codice_cliente,
    c.cli_rgsoc AS ragione_sociale,
    ca.cla_pagcod AS codice_pagamento,
    ca.cla_iban AS iban,
    ca.cla_swift AS swift_bic,
    ca.cla_bic AS bic,
    ca.cla_abicod AS abi,
    ca.cla_cabcod AS cab,
    ca.cla_caccod AS codice_conto_corrente,
    ca.cla_mandato AS mandato_sdd,
    ca.cla_dtmandato AS data_mandato,
    ca.cla_schemasdd AS schema_sdd,
    ca.cla_credid AS id_credenziale,
    ca.cla_fidotot AS fido_totale,
    ca.cla_fidocont AS fido_concessione,
    ca.cla_fidoscad AS fido_scadenze,
    ca.cla_fidoordini AS fido_ordini,
    ca.cla_fidoggtoll AS fido_gg_tolleranza,
    ca.cla_ggfinemese AS gg_fine_mese,
    ca.cla_ggesclusi AS gg_esclusi,
    ca.cla_postcons AS posticipazione_consegna,
    ca.cla_dscpagam AS descrizione_pagamento,
    ca.cla_tipofatt AS tipo_fatturazione,
    ca.cla_minfat AS importo_minimo_fattura,
    ca.cla_valcod AS codice_valuta,
    ca.cla_obsoleto AS obsoleto
FROM integra.cliazi ca
    JOIN integra.clienti c ON c.cli_cdcli = ca.cla_clicdcli AND c.cli_obsoleto = 0
WHERE ca.azi_cdazi = '001' AND ca.cla_tipo = 'C' AND ca.cla_obsoleto = 0 AND ca.cla_clicdcli > 0;
ALTER VIEW public.rag_pagamenti_clienti OWNER TO postgres;

-- ============ rag_ordini_clienti ============
CREATE OR REPLACE VIEW public.rag_ordini_clienti AS
SELECT t.mvt_id AS id_ordine,
    t.mvt_num AS numero_ordine,
    t.mvt_numnum AS numero_progressivo,
    t.mvt_anno AS anno_ordine,
    t.mvt_mvnserie AS serie,
    t.mvt_clacod AS id_cliente,
    c.cli_cdclistr AS codice_cliente,
    c.cli_rgsoc AS ragione_sociale,
    t.mvt_dtmov AS data_ordine,
    t.mvt_dtreg AS data_registrazione,
    t.mvt_dtval AS data_valuta,
    t.mvt_dtcomp AS data_competenza,
    t.mvt_dttrasp AS data_trasporto,
    t.mvt_impon AS importo_imponibile,
    t.mvt_impiva AS importo_iva,
    t.mvt_baseimpo AS base_imponibile,
    t.mvt_sc1 AS sconto_1, t.mvt_sc2 AS sconto_2, t.mvt_sc3 AS sconto_3, t.mvt_sc4 AS sconto_4,
    t.mvt_scontofin AS sconto_finale,
    t.mvt_pagcod AS codice_pagamento,
    t.mvt_porcod AS codice_porto,
    t.mvt_vetcod1 AS codice_vettore,
    t.mvt_specod AS codice_spedizione,
    t.mvt_valcod AS codice_valuta,
    t.mvt_numordpa AS riferimento_ordine_cliente,
    t.mvt_dtordpa AS data_riferimento_ordine,
    t.mvt_vsrif AS riferimento_b2b,
    t.mvt_dtvsrif AS data_riferimento_b2b,
    t.mvt_dstid1 AS id_destinazione_merce,
    t.mvt_dstid2 AS id_destinazione_fattura,
    t.mvt_dstid3 AS id_destinazione_committente,
    t.mvt_saldato AS stato_saldo,
    t.mvt_fatturato AS flag_fatturato,
    t.mvt_contab AS flag_contabilizzato,
    t.mvt_check AS stato_verifica,
    t.mvt_obsoleto AS flag_obsoleto,
    t.mvt_liberoc5 AS note_ordine,
    t.mvt_userin AS utente_inserimento,
    to_timestamp(extract(epoch from t.mvt_dtins) + t.mvt_orains * 3600 + t.mvt_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from t.mvt_dtvar) + t.mvt_oravar * 3600 + t.mvt_minvar * 60) AS data_modifica
FROM integra.movtest t
    LEFT JOIN integra.clienti c ON c.cli_cdcli = t.mvt_clacod
WHERE t.azi_cdazi = '001' AND t.mvt_natmov = 'ORD' AND t.mvt_obsoleto = 0 AND t.mvt_clacod > 0;
ALTER VIEW public.rag_ordini_clienti OWNER TO postgres;

-- ============ rag_righe_ordini ============
CREATE OR REPLACE VIEW public.rag_righe_ordini AS
SELECT r.mvr_mvtid AS id_ordine,
    t.mvt_num AS numero_ordine,
    t.mvt_dtmov AS data_ordine,
    t.mvt_clacod AS id_cliente,
    r.mvr_id AS id_riga,
    r.mvr_ordinamento AS ordine_riga,
    r.mvr_proid AS id_prodotto,
    p.pro_cod AS codice_prodotto,
    r.mvr_descr AS descrizione_riga,
    p.pro_descr AS descrizione_prodotto,
    r.mvr_qta AS quantita,
    r.mvr_umicod AS unita_misura,
    r.mvr_prezzo AS prezzo_listino,
    r.mvr_prznetto AS prezzo_netto,
    r.mvr_przivato AS prezzo_ivato,
    r.mvr_importo AS importo,
    r.mvr_sconto1 AS sconto_1, r.mvr_sconto2 AS sconto_2, r.mvr_sconto3 AS sconto_3, r.mvr_sconto4 AS sconto_4,
    r.mvr_scontoval AS valore_sconto,
    r.mvr_flgsaldo AS stato_saldo,
    r.mvr_flgfat AS stato_fatturazione,
    r.mvr_qtafat AS quantita_fatturata,
    r.mvr_impfatt AS importo_fatturato,
    r.mvr_liberoc1 AS note_riga
FROM integra.movrig r
    JOIN integra.movtest t ON t.mvt_id = r.mvr_mvtid AND t.azi_cdazi = r.azi_cdazi AND t.mvt_obsoleto = 0 AND t.mvt_natmov = 'ORD'
    LEFT JOIN integra.prodotti p ON p.pro_id = r.mvr_proid AND p.azi_cdazi = r.azi_cdazi
WHERE r.azi_cdazi = '001' AND r.mvr_obsoleto = 0;
ALTER VIEW public.rag_righe_ordini OWNER TO postgres;

-- ============ rag_listini_testata ============
CREATE OR REPLACE VIEW public.rag_listini_testata AS
SELECT t.tls_cod AS codice_listino,
    t.tls_descr AS descrizione_listino,
    t.tls_tipo AS tipo_listino,
    t.tls_flgiva AS listino_con_iva,
    t.tls_valcod AS codice_valuta,
    t.tls_ndec AS n_decimali,
    t.tls_flgnetto AS prezzi_netto,
    t.tls_obsoleto AS listino_obsoleto,
    to_timestamp(extract(epoch from t.tls_dtins) + t.tls_orains * 3600 + t.tls_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from t.tls_dtvar) + t.tls_oravar * 3600 + t.tls_minvar * 60) AS data_modifica
FROM integra.listest t
WHERE t.azi_cdazi = '001';
ALTER VIEW public.rag_listini_testata OWNER TO postgres;

-- ============ rag_listini_righe ============
CREATE OR REPLACE VIEW public.rag_listini_righe AS
SELECT r.lst_id AS id_riga_listino,
    r.lst_tlscod AS codice_listino,
    r.lst_proid AS id_prodotto,
    p.pro_cod AS codice_prodotto,
    p.pro_descr AS descrizione_prodotto,
    r.lst_varid AS id_variante,
    r.lst_prezzo AS prezzo_listino,
    r.lst_sconto1 AS sconto_1, r.lst_sconto2 AS sconto_2, r.lst_sconto3 AS sconto_3, r.lst_sconto4 AS sconto_4,
    r.lst_ivacod AS codice_iva,
    r.lst_qtada AS quantita_da,
    r.lst_aqta AS quantita_a,
    r.lst_scala AS scala,
    r.lst_dtinizio AS data_inizio_validita,
    r.lst_dtfine AS data_fine_validita,
    r.lst_clatipo AS tipo_cliente,
    r.lst_clacod AS id_cliente,
    r.lst_obsoleto AS listino_obsoleto,
    to_timestamp(extract(epoch from r.lst_dtins) + r.lst_orains * 3600 + r.lst_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from r.lst_dtvar) + r.lst_oravar * 3600 + r.lst_minvar * 60) AS data_modifica
FROM integra.listini r
    LEFT JOIN integra.prodotti p ON p.azi_cdazi = r.azi_cdazi AND p.pro_id = r.lst_proid
WHERE r.azi_cdazi = '001' AND r.lst_obsoleto = 0 AND r.lst_progr = 1;
ALTER VIEW public.rag_listini_righe OWNER TO postgres;

-- ============ rag_tabpag ============
CREATE OR REPLACE VIEW public.rag_tabpag AS
SELECT t.pag_cod AS codice_pagamento,
    t.pag_descr AS descrizione_pagamento,
    t.pag_tiposcad AS tipo_scadenza,
    t.pag_tipoiva AS tipo_iva,
    t.pag_scontocassa AS sconto_cassa,
    t.pag_ggfinemese AS gg_fine_mese,
    t.pag_ggesclusi AS gg_esclusi,
    t.pag_pamodpag AS pa_mod_pagamento,
    t.pag_papagamento AS pa_pagamento,
    t.pag_obsoleto AS obsoleto,
    to_timestamp(extract(epoch from t.pag_dtins) + t.pag_orains * 3600 + t.pag_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from t.pag_dtvar) + t.pag_oravar * 3600 + t.pag_minvar * 60) AS data_modifica
FROM integra.tabpag t
WHERE t.azi_cdazi = '001';
ALTER VIEW public.rag_tabpag OWNER TO postgres;

-- ============ rag_tabpor ============
CREATE OR REPLACE VIEW public.rag_tabpor AS
SELECT t.por_cod AS codice_porto,
    t.por_descr AS descrizione_porto,
    t.por_obsoleto AS obsoleto,
    to_timestamp(extract(epoch from t.por_dtins) + t.por_orains * 3600 + t.por_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from t.por_dtvar) + t.por_oravar * 3600 + t.por_minvar * 60) AS data_modifica
FROM integra.tabpor t
WHERE t.azi_cdazi = '001';
ALTER VIEW public.rag_tabpor OWNER TO postgres;

-- ============ rag_tabspe ============
CREATE OR REPLACE VIEW public.rag_tabspe AS
SELECT t.spe_cod AS codice_spedizione,
    t.spe_descr AS descrizione_spedizione,
    t.spe_obsoleto AS obsoleto,
    to_timestamp(extract(epoch from t.spe_dtins) + t.spe_orains * 3600 + t.spe_minins * 60) AS data_inserimento,
    to_timestamp(extract(epoch from t.spe_dtvar) + t.spe_oravar * 3600 + t.spe_minvar * 60) AS data_modifica
FROM integra.tabspe t
WHERE t.azi_cdazi = '001';
ALTER VIEW public.rag_tabspe OWNER TO postgres;
