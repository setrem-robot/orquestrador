/* Console de análise da Atlas. Usa a API COM TOKEN — o token fica só neste
 * navegador (localStorage/sessionStorage) e vai apenas para a API da Atlas.
 * Nenhuma credencial é embutida aqui. Gráficos e sparklines são SVG desenhados
 * à mão a partir dos eventos crus, então qualquer campo (inclusive aninhado,
 * como cpu.por_nucleo) vira histórico sem depender de agregação no servidor. */
(function () {
  'use strict';

  const cfg = window.ATLAS_API || {};
  const base = String(cfg.base || '/api').replace(/\/+$/, '');
  const intervalo = Math.max(15, Number(cfg.intervaloSegundos) || 30) * 1000;
  const CHAVE = 'atlas_token';
  const HIST = 500; // eventos por tipo (teto da API)
  const SVGNS = 'http://www.w3.org/2000/svg';
  const CORES = ['#45edb5', '#7bb8ff', '#ffcf6b', '#ff9f9f', '#c9a3ff', '#8bd3c7'];

  const el = id => document.getElementById(id);
  const NOMES = {sistema: 'Saúde do Pi', gps: 'GPS', motores: 'Motores', bateria: 'Bateria', wifi: 'Wi-Fi'};
  let token = '';
  let mapa = null, camada = null;
  let consultando = false;
  let logCorte = null;
  let ultItens = {}, ultTrajeto = null; // guardados para redesenhar o mapa ao abrir a aba

  // ---- token / sessão --------------------------------------------------
  function lerToken() { try { return localStorage.getItem(CHAVE) || sessionStorage.getItem(CHAVE) || ''; } catch (e) { return ''; } }
  function guardarToken(v, lembrar) {
    try { (lembrar ? localStorage : sessionStorage).setItem(CHAVE, v); (lembrar ? sessionStorage : localStorage).removeItem(CHAVE); } catch (e) { /* privado */ }
  }
  function esquecerToken() { try { localStorage.removeItem(CHAVE); sessionStorage.removeItem(CHAVE); } catch (e) { /* nada */ } }
  function mostrarPortao(erro) {
    el('conteudo').hidden = true; el('portao').hidden = false;
    el('portao-erro').hidden = !erro; if (erro) el('portao-erro').textContent = erro;
    el('campo-token').focus();
  }
  function entrar() {
    const v = el('campo-token').value.trim();
    if (!v) { el('portao-erro').hidden = false; el('portao-erro').textContent = 'Cole o token para entrar.'; return; }
    token = v; guardarToken(v, el('lembrar').checked);
    el('campo-token').value = '';
    el('portao').hidden = true; el('conteudo').hidden = false;
    carregarTudo(); carregarLog(true);
  }
  function sair() {
    esquecerToken(); token = '';
    if (mapa) { mapa.remove(); mapa = null; camada = null; }
    mostrarPortao('');
  }

  // ---- rede ------------------------------------------------------------
  async function buscar(caminhoUrl) {
    const controle = new AbortController();
    const prazo = setTimeout(() => controle.abort(), 12000);
    try {
      const r = await fetch(base + caminhoUrl, {
        cache: 'no-store', signal: controle.signal,
        headers: {Authorization: 'Bearer ' + token, Accept: 'application/json'},
      });
      if (r.status === 401 || r.status === 403) { const e = new Error('nao-autorizado'); e.auth = true; throw e; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } finally { clearTimeout(prazo); }
  }

  // ---- utilidades ------------------------------------------------------
  const nofim = v => Number.isFinite(Number(v)) ? Number(v) : null;
  const fixo = (v, c = 0) => nofim(v) === null ? null : Number(v).toFixed(c);
  const caminho = (obj, rota) => rota.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
  function idade(seg) {
    if (!Number.isFinite(seg)) return 'sem data';
    if (seg < 0) return 'adiantado';
    if (seg < 60) return 'há ' + Math.round(seg) + ' s';
    if (seg < 3600) return 'há ' + Math.floor(seg / 60) + ' min';
    if (seg < 86400) return 'há ' + Math.floor(seg / 3600) + ' h';
    return 'há ' + Math.floor(seg / 86400) + ' dias';
  }
  function tempoLigado(s) {
    if (!Number.isFinite(s) || s < 0) return '—';
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d) return d + 'd ' + h + 'h';
    if (h) return h + 'h ' + m + 'm';
    return m + ' min';
  }
  function horaCurta(ts) { const d = new Date(ts); return Number.isFinite(d.getTime()) ? d.toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'}) : ''; }
  function texto(pai, tag, classe, conteudo) {
    const e = document.createElement(tag);
    if (classe) e.className = classe;
    if (conteudo != null) e.textContent = conteudo;
    pai.append(e); return e;
  }
  function idadeDe(item) { const t = Date.parse(item && item.ts); return Number.isFinite(t) ? (Date.now() - t) / 1000 : (item && typeof item.idade_s === 'number' ? item.idade_s : NaN); }
  const evOf = r => (r.status === 'fulfilled' && r.value && Array.isArray(r.value.eventos)) ? r.value.eventos : [];
  function serie(eventos, rota) {
    return eventos.slice().reverse().map(ev => [Date.parse(ev.ts), nofim(caminho(ev.dados, rota))]).filter(p => p[1] !== null && Number.isFinite(p[0]));
  }

  // ---- sparkline (mini gráfico sem eixos) ------------------------------
  function sparkline(pontos) {
    const pts = pontos.filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1]));
    if (pts.length < 2) return null;
    const W = 120, H = 34;
    const xs = pts.map(p => p[0]), vs = pts.map(p => p[1]);
    const tmin = Math.min(...xs), tmax = Math.max(...xs);
    let vmin = Math.min(...vs), vmax = Math.max(...vs);
    if (vmax === vmin) { vmax += 1; vmin -= 1; }
    const x = t => (tmax === tmin ? 0 : (t - tmin) / (tmax - tmin) * W);
    const y = v => H - 3 - (v - vmin) / (vmax - vmin) * (H - 6);
    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('class', 'spark'); svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('preserveAspectRatio', 'none');
    const d = pts.map((p, i) => (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1)).join(' ');
    const area = document.createElementNS(SVGNS, 'path');
    area.setAttribute('d', d + ' L' + W + ' ' + H + ' L0 ' + H + ' Z');
    area.setAttribute('class', 'spark-area');
    const linha = document.createElementNS(SVGNS, 'path');
    linha.setAttribute('d', d); linha.setAttribute('class', 'spark-linha');
    svg.append(area, linha);
    return svg;
  }

  // ---- gráfico SVG (linha com eixos) -----------------------------------
  function grafico(titulo, unidade, series, opcoes) {
    opcoes = opcoes || {};
    const fig = document.createElement('figure');
    fig.className = 'grafico';
    texto(fig, 'figcaption', null, titulo);
    const validas = series.map(s => ({nome: s.nome, pontos: s.pontos.filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1]))})).filter(s => s.pontos.length);
    if (!validas.length) { texto(fig, 'p', 'sem-registro', 'Sem histórico ainda.'); return fig; }
    const W = 600, H = 210, pl = 46, pr = 14, pt = 16, pb = 26;
    const todos = validas.flatMap(s => s.pontos);
    let vmin = Math.min(...todos.map(p => p[1])), vmax = Math.max(...todos.map(p => p[1]));
    const tmin = Math.min(...todos.map(p => p[0])), tmax = Math.max(...todos.map(p => p[0]));
    if (opcoes.mine != null) vmin = Math.min(vmin, opcoes.mine);
    if (opcoes.maxe != null) vmax = Math.max(vmax, opcoes.maxe);
    if (vmax === vmin) { vmax += 1; vmin -= 1; }
    const m = (vmax - vmin) * 0.08; vmin -= m; vmax += m;
    const x = t => tmax === tmin ? pl : pl + (t - tmin) / (tmax - tmin) * (W - pl - pr);
    const y = v => pt + (1 - (v - vmin) / (vmax - vmin)) * (H - pt - pb);
    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', titulo);
    const linhaEixo = (x1, y1, x2, y2, cls) => { const l = document.createElementNS(SVGNS, 'line'); l.setAttribute('x1', x1); l.setAttribute('y1', y1); l.setAttribute('x2', x2); l.setAttribute('y2', y2); l.setAttribute('class', cls); svg.append(l); };
    const rotulo = (tx, ty, cls, txt, anchor) => { const t = document.createElementNS(SVGNS, 'text'); t.setAttribute('x', tx); t.setAttribute('y', ty); t.setAttribute('class', cls); if (anchor) t.setAttribute('text-anchor', anchor); t.textContent = txt; svg.append(t); };
    [vmax, (vmax + vmin) / 2, vmin].forEach(v => { linhaEixo(pl, y(v), W - pr, y(v), 'g-grade'); rotulo(pl - 6, y(v) + 3, 'g-rot', formatarValor(v, unidade), 'end'); });
    validas.forEach((s, i) => { const p = document.createElementNS(SVGNS, 'polyline'); p.setAttribute('fill', 'none'); p.setAttribute('stroke', CORES[i % CORES.length]); p.setAttribute('stroke-width', '2'); p.setAttribute('stroke-linejoin', 'round'); p.setAttribute('points', s.pontos.map(q => x(q[0]).toFixed(1) + ',' + y(q[1]).toFixed(1)).join(' ')); svg.append(p); });
    rotulo(pl, H - 7, 'g-rot', horaCurta(tmin), 'start'); rotulo(W - pr, H - 7, 'g-rot', horaCurta(tmax), 'end');
    fig.append(svg);
    const rod = texto(fig, 'div', 'g-rodape');
    validas.forEach((s, i) => { const span = texto(rod, 'span', 'g-legenda'); texto(span, 'i').style.background = CORES[i % CORES.length]; span.append(document.createTextNode(' ' + (validas.length > 1 ? s.nome + ' ' : '') + formatarValor(s.pontos[s.pontos.length - 1][1], unidade))); });
    return fig;
  }
  function formatarValor(v, unidade) { const c = Math.abs(v) >= 100 ? 0 : (Math.abs(v) >= 10 ? 1 : 2); return Number(v).toFixed(c) + (unidade ? ' ' + unidade : ''); }

  // ---- sinais vitais ---------------------------------------------------
  function estadoConexao(classe, txt) { el('estado-conexao').className = 'estado ' + classe; el('estado-conexao').textContent = txt; }

  function tileVital(t) {
    const li = document.createElement('div');
    li.className = 'vital vital--' + (t.estado || 'neutro') + (t.geral ? ' vital-geral' : '');
    const topo = texto(li, 'div', 'vital-topo');
    texto(topo, 'span', 'vital-rot', t.rotulo);
    if (t.demo) texto(topo, 'span', 'demo-tag', 'demo');
    const num = texto(li, 'strong', 'vital-num');
    if (t.geral) texto(num, 'span', 'ponto');
    num.append(document.createTextNode(t.valor));
    if (t.unidade) texto(num, 'span', 'vital-un', ' ' + t.unidade);
    if (t.spark) { const s = sparkline(t.spark); if (s) li.append(s); }
    if (t.nota) texto(li, 'span', 'vital-nota', t.nota);
    return li;
  }

  function desenharVitais(itens, evSis, evBat) {
    const cont = el('vitais'); cont.replaceChildren();
    const sis = itens.sistema, d = (sis && sis.dados) || {};
    const cpu = d.cpu || {}, mem = d.memoria || {}, thr = d.throttled;
    const ig = idadeDe(sis);
    const bat = (itens.bateria && itens.bateria.dados) || {};

    // veredito geral
    let ge = 'bom', gtxt = 'Tudo bem';
    if (!sis || !Number.isFinite(ig) || ig > 120) { ge = 'sem'; gtxt = 'Sem sinal recente'; }
    else if (thr && !thr.ok) { ge = 'critico'; gtxt = 'Energia/temperatura'; }
    else if (nofim(d.temperatura_c) > 78) { ge = 'atencao'; gtxt = 'Temperatura alta'; }
    const nota = Number.isFinite(ig) ? 'saúde ' + idade(ig) : 'sem leitura';

    const temp = nofim(d.temperatura_c);
    const uso = nofim(cpu.uso_pct);
    const memp = nofim(mem.uso_pct);
    const pct = nofim(bat.percentual);

    cont.append(tileVital({geral: true, rotulo: 'Estado geral', valor: gtxt, estado: ge, nota}));
    cont.append(tileVital({rotulo: 'Temperatura', valor: fixo(temp, 1) ?? '—', unidade: '°C', estado: temp == null ? 'neutro' : temp > 78 ? 'critico' : temp > 70 ? 'atencao' : 'bom', spark: serie(evSis, 'temperatura_c'), nota: thr ? (thr.ok ? 'sem throttling' : 'throttling!') : null}));
    cont.append(tileVital({rotulo: 'CPU', valor: fixo(uso) ?? '—', unidade: '%', estado: uso == null ? 'neutro' : uso > 90 ? 'atencao' : 'bom', spark: serie(evSis, 'cpu.uso_pct'), nota: cpu.freq_mhz ? cpu.freq_mhz + ' MHz' : null}));
    cont.append(tileVital({rotulo: 'Memória', valor: fixo(memp) ?? '—', unidade: '%', estado: memp == null ? 'neutro' : memp > 90 ? 'atencao' : 'bom', spark: serie(evSis, 'memoria.uso_pct'), nota: Number.isFinite(mem.disponivel_mb) ? mem.disponivel_mb.toLocaleString('pt-BR') + ' MB livres' : null}));
    cont.append(tileVital({rotulo: 'Bateria', valor: fixo(pct, 0) ?? '—', unidade: '%', estado: pct == null ? 'neutro' : pct < 15 ? 'critico' : pct < 30 ? 'atencao' : 'bom', spark: serie(evBat, 'percentual'), demo: !!bat.demo, nota: fixo(bat.tensao_v, 2) !== null ? fixo(bat.tensao_v, 2) + ' V' : null}));
    cont.append(tileVital({rotulo: 'Ligado', valor: tempoLigado(d.uptime_s), estado: 'neutro', nota: cpu.governor || null}));

    const ids = Object.values(itens).map(idadeDe).filter(Number.isFinite);
    const novo = ids.length ? Math.min(...ids) : NaN;
    estadoConexao(Number.isFinite(novo) && novo < 120 ? 'vivo' : 'parado', Number.isFinite(novo) ? 'telemetria ' + idade(novo) : 'sem horário');
  }

  // ---- aba: saúde do Pi ------------------------------------------------
  function desenharPi(item) {
    const d = (item && item.dados) || {};
    const cpu = d.cpu || {}, mem = d.memoria || {}, disco = d.disco || {}, thr = d.throttled, proc = d.processos;
    const det = el('pi-detalhe'); det.replaceChildren();
    const par = (rot, val) => { const li = texto(det, 'li'); texto(li, 'span', 'rotulo', rot); texto(li, 'strong', null, val); };
    par('Frequência', cpu.freq_mhz ? cpu.freq_mhz + ' MHz' : '—');
    par('Governor', cpu.governor || '—');
    par('Voltagem', fixo(cpu.voltagem_v, 2) !== null ? fixo(cpu.voltagem_v, 2) + ' V' : '—');
    par('Disco', fixo(disco.uso_pct) !== null ? fixo(disco.uso_pct) + ' %' + (Number.isFinite(disco.livre_gb) ? ' · ' + disco.livre_gb + ' GB livres' : '') : '—');
    par('Swap', Number.isFinite(mem.swap_total_mb) ? (mem.swap_usado_mb || 0) + ' / ' + mem.swap_total_mb + ' MB' : '—');
    par('Processos', proc ? proc.rodando + ' ativos / ' + proc.total : '—');

    const ln = el('s-nucleos'); ln.replaceChildren();
    const nucleos = Array.isArray(cpu.por_nucleo) ? cpu.por_nucleo : [];
    if (!nucleos.length) texto(ln, 'li', 'sem-registro', 'Uso por núcleo aparece na próxima leitura.');
    nucleos.forEach((v, i) => {
      const p = Math.max(0, Math.min(100, Number(v) || 0));
      const li = texto(ln, 'li');
      texto(li, 'span', 'n-nome', 'Núcleo ' + i);
      const barra = texto(li, 'div', 'barra');
      const it = texto(barra, 'i'); it.style.width = p + '%'; if (p >= 85) it.className = 'quente';
      texto(li, 'span', 'n-val', Math.round(p) + ' %');
    });

    const lf = el('s-flags'); lf.replaceChildren();
    if (thr) {
      const rots = {subtensao_agora: 'Subtensão agora', frequencia_limitada_agora: 'Freq. limitada agora', throttling_agora: 'Throttling agora', limite_termico_agora: 'Limite térmico agora', subtensao_ja_ocorreu: 'Subtensão já ocorreu', frequencia_limitada_ja_ocorreu: 'Freq. limitada já ocorreu', throttling_ja_ocorreu: 'Throttling já ocorreu', limite_termico_ja_ocorreu: 'Limite térmico já ocorreu'};
      for (const [k, rot] of Object.entries(rots)) { const li = texto(lf, 'li'); texto(li, 'span', null, rot); texto(li, 'span', 'flag ' + (thr[k] ? 'flag-sim' : 'flag-nao'), thr[k] ? 'sim' : 'não'); }
    } else texto(lf, 'li', 'sem-registro', '—');

    const lr = el('s-rede'); lr.replaceChildren();
    const rede = d.rede && typeof d.rede === 'object' ? d.rede : {};
    const pares = Object.entries(rede);
    if (!pares.length) texto(lr, 'li', 'sem-registro', '—');
    pares.forEach(([nome, v]) => { const li = texto(lr, 'li'); texto(li, 'span', null, nome); texto(li, 'span', 'flag', '↓ ' + v.rx_mb + ' MB · ↑ ' + v.tx_mb + ' MB'); });
    if (proc) { const li = texto(lr, 'li'); texto(li, 'span', null, 'Processos'); texto(li, 'span', 'flag', proc.rodando + ' / ' + proc.total); }
  }

  function desenharGraficosSistema(ev) {
    const g = el('g-sistema'); g.replaceChildren();
    g.append(grafico('Temperatura (°C)', '°C', [{nome: 'temp', pontos: serie(ev, 'temperatura_c')}]));
    g.append(grafico('Uso de CPU (%)', '%', [{nome: 'cpu', pontos: serie(ev, 'cpu.uso_pct')}], {mine: 0, maxe: 100}));
    g.append(grafico('Uso por núcleo (%)', '%', nucleosSeries(ev), {mine: 0, maxe: 100}));
    g.append(grafico('Memória (%)', '%', [{nome: 'mem', pontos: serie(ev, 'memoria.uso_pct')}], {mine: 0, maxe: 100}));
    g.append(grafico('Frequência (MHz)', 'MHz', [{nome: 'freq', pontos: serie(ev, 'cpu.freq_mhz')}]));
  }
  function nucleosSeries(ev) {
    const antigo = ev.slice().reverse();
    let n = 0;
    for (const e of antigo) { const a = caminho(e.dados, 'cpu.por_nucleo'); if (Array.isArray(a)) n = Math.max(n, a.length); }
    const series = [];
    for (let i = 0; i < n; i++) series.push({nome: 'N' + i, pontos: antigo.map(e => { const a = caminho(e.dados, 'cpu.por_nucleo'); return [Date.parse(e.ts), Array.isArray(a) && Number.isFinite(a[i]) ? a[i] : null]; }).filter(p => p[1] !== null)});
    return series;
  }

  // ---- aba: sensores ---------------------------------------------------
  function desenharBateria(item, hist) {
    const d = (item && item.dados) || {};
    el('bat-demo').hidden = !d.demo;
    el('b-pct').textContent = fixo(d.percentual, 1) !== null ? fixo(d.percentual, 1) + ' %' : '—';
    el('b-volt').textContent = fixo(d.tensao_v, 2) ?? '—';
    const g = el('g-bateria'); g.replaceChildren();
    g.append(grafico('Carga (%)', '%', [{nome: 'carga', pontos: serie(hist, 'percentual')}], {mine: 0, maxe: 100}));
    g.append(grafico('Tensão (V)', 'V', [{nome: 'tensão', pontos: serie(hist, 'tensao_v')}]));
  }
  function desenharMotores(item, hist) {
    const d = (item && item.dados) || {};
    el('mot-demo').hidden = !d.demo;
    el('m-acao').textContent = d.acao || '—';
    el('m-vel').textContent = fixo(d.velocidade) !== null ? 'vel ' + fixo(d.velocidade) : 'ação';
    el('m-esq').textContent = fixo(d.esquerda, 2) ?? '—';
    el('m-dir').textContent = fixo(d.direita, 2) ?? '—';
    const g = el('g-motores'); g.replaceChildren();
    g.append(grafico('Velocidade dos lados', '', [{nome: 'esquerda', pontos: serie(hist, 'esquerda')}, {nome: 'direita', pontos: serie(hist, 'direita')}], {mine: -1, maxe: 1}));
  }
  function desenharWifi(item, saude) {
    const d = (item && item.dados) || {};
    el('wifi-demo').hidden = !d.demo;
    const lw = el('w-lista'); lw.replaceChildren();
    const linha = (rot, val) => { const li = texto(lw, 'li'); texto(li, 'span', null, rot); texto(li, 'span', 'flag', val); };
    linha('Conectado', d.conectado ? 'sim' : 'não');
    linha('SSID', d.ssid || '—');
    linha('IP', d.ip || '—');
    const w = saude && saude.dados && saude.dados.wifi;
    if (w) { linha('Qualidade do link', String(w.link)); linha('Sinal', w.sinal_dbm + ' dBm'); }
    else linha('Sinal medido pelo Pi', 'no cabo (sem Wi-Fi)');
  }

  // ---- aba: localização (mapa preguiçoso) ------------------------------
  function desenharGpsInfo(item) {
    const d = (item && item.dados) || {};
    el('gps-demo') && (el('gps-demo').hidden = !d.demo);
    el('g-lat').textContent = fixo(d.lat, 6) ?? '—';
    el('g-lon').textContent = fixo(d.lon, 6) ?? '—';
    el('g-fix').textContent = d.fix ? 'com sinal' : 'sem sinal';
    el('g-sat').textContent = Number.isFinite(d.satelites) ? d.satelites : '—';
    el('g-vel').textContent = fixo(d.velocidade_kmh, 1) !== null ? fixo(d.velocidade_kmh, 1) + ' km/h' : '—';
  }
  function renderMapa() {
    // Leaflet não mede um container escondido; só desenha quando a aba está visível.
    if (el('painel-local').hidden) return;
    const pontos = (ultTrajeto && Array.isArray(ultTrajeto.pontos) ? ultTrajeto.pontos : []).filter(p => Number.isFinite(p.lat) && Number.isFinite(p.lon) && Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180);
    if (!pontos.length || !window.L) {
      if (mapa) { mapa.remove(); mapa = null; camada = null; }
      el('mapa').hidden = true; el('mapa-vazio').hidden = false;
      el('g-pontos').textContent = window.L ? 'Sem trajeto disponível.' : 'Biblioteca de mapa indisponível.';
      return;
    }
    el('mapa-vazio').hidden = true; el('mapa').hidden = false;
    if (!mapa) { mapa = window.L.map('mapa', {scrollWheelZoom: false}); window.L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap'}).addTo(mapa); }
    mapa.invalidateSize();
    if (camada) mapa.removeLayer(camada);
    const ll = pontos.map(p => [p.lat, p.lon]);
    const linha = window.L.polyline(ll, {color: '#039f70', weight: 4});
    const ponta = window.L.circleMarker(ll[ll.length - 1], {radius: 7, color: '#093427', weight: 2, fillColor: '#45edb5', fillOpacity: 1});
    camada = window.L.layerGroup([linha, ponta]).addTo(mapa);
    mapa.fitBounds(linha.getBounds(), {padding: [26, 26], maxZoom: 18});
    const ult = Date.parse(pontos[pontos.length - 1].ts);
    el('g-pontos').textContent = pontos.length + ' posições · precisão total · última ' + (Number.isFinite(ult) ? new Date(ult).toLocaleString('pt-BR') : '—');
  }

  // ---- carregamento ----------------------------------------------------
  async function carregarTudo() {
    if (consultando || !token) { if (!token) mostrarPortao(''); return; }
    consultando = true; el('atualizar').disabled = true; el('ultima-consulta').textContent = '…';
    try {
      const [estado, hSis, hBat, hMot, traj] = await Promise.allSettled([
        buscar('/v1/estado'),
        buscar('/v1/eventos?tipo=sistema&limite=' + HIST),
        buscar('/v1/eventos?tipo=bateria&limite=' + HIST),
        buscar('/v1/eventos?tipo=motores&limite=' + HIST),
        buscar('/v1/trajeto?limite=1000'),
      ]);
      if ([estado, hSis, hBat, hMot, traj].some(r => r.status === 'rejected' && r.reason && r.reason.auth)) { sair(); mostrarPortao('Token inválido ou sem permissão.'); return; }

      const avisos = [];
      const evSis = evOf(hSis), evBat = evOf(hBat), evMot = evOf(hMot);
      const itens = (estado.status === 'fulfilled' && estado.value && estado.value.itens) ? estado.value.itens : {};
      ultItens = itens; ultTrajeto = traj.status === 'fulfilled' ? traj.value : null;
      if (estado.status === 'fulfilled') {
        desenharVitais(itens, evSis, evBat);
        desenharPi(itens.sistema);
        desenharBateria(itens.bateria, evBat);
        desenharMotores(itens.motores, evMot);
        desenharGpsInfo(itens.gps);
        desenharWifi(itens.wifi, itens.sistema);
        renderMapa();
      } else { estadoConexao('erro', 'sem conexão'); avisos.push('A consulta do estado falhou.'); }

      if (hSis.status === 'fulfilled') desenharGraficosSistema(evSis);
      else if (!hSis.reason || !hSis.reason.auth) avisos.push('O histórico de saúde não respondeu.');

      el('aviso').textContent = avisos.join(' '); el('aviso').hidden = !avisos.length;
      el('ultima-consulta').textContent = new Date().toLocaleTimeString('pt-BR');
    } catch (erro) {
      if (erro && erro.auth) { sair(); mostrarPortao('Token inválido ou sem permissão.'); return; }
      estadoConexao('erro', 'falha'); el('aviso').hidden = false; el('aviso').textContent = 'Ocorreu uma falha ao exibir os dados.';
      console.warn('Atlas console: falha', erro);
    } finally { consultando = false; el('atualizar').disabled = false; }
  }

  // ---- log cru ---------------------------------------------------------
  async function carregarLog(reset) {
    if (!token) return;
    if (reset) { logCorte = null; el('log-corpo').replaceChildren(); }
    const tipo = el('log-tipo').value;
    let url = '/v1/eventos?limite=50' + (tipo ? '&tipo=' + encodeURIComponent(tipo) : '');
    if (logCorte) url += '&antes_de=' + encodeURIComponent(logCorte);
    el('log-mais').disabled = true; el('log-info').textContent = 'Consultando…';
    try {
      const d = await buscar(url);
      const corpo = el('log-corpo');
      if (reset && (!d.eventos || !d.eventos.length)) { const tr = texto(corpo, 'tr'); texto(tr, 'td', 'sem-registro', 'Nenhum evento.').colSpan = 4; }
      for (const ev of (d.eventos || [])) {
        const tr = texto(corpo, 'tr');
        texto(tr, 'td', 'log-quando', new Date(ev.ts).toLocaleString('pt-BR'));
        texto(tr, 'td', null, NOMES[ev.tipo] || ev.tipo);
        texto(tr, 'td', 'log-topico', ev.topico);
        texto(tr, 'td', 'log-json', JSON.stringify(ev.dados));
      }
      logCorte = d.proximo_antes_de || null;
      el('log-mais').hidden = !logCorte;
      el('log-info').textContent = logCorte ? '' : 'Fim do histórico.';
    } catch (erro) {
      if (erro && erro.auth) { sair(); mostrarPortao('Token inválido ou sem permissão.'); return; }
      el('log-info').textContent = 'Falha ao consultar o log.';
    } finally { el('log-mais').disabled = false; }
  }

  // ---- abas ------------------------------------------------------------
  function trocarAba(alvo) {
    for (const b of document.querySelectorAll('.aba')) {
      const ativo = b.dataset.alvo === alvo;
      b.setAttribute('aria-selected', ativo ? 'true' : 'false');
      el(b.dataset.alvo).hidden = !ativo;
    }
    if (alvo === 'painel-local') renderMapa();
  }

  // ---- ligação ---------------------------------------------------------
  el('form-token').addEventListener('submit', e => { e.preventDefault(); entrar(); });
  el('sair').addEventListener('click', sair);
  el('atualizar').addEventListener('click', () => { carregarTudo(); carregarLog(true); });
  el('log-mais').addEventListener('click', () => carregarLog(false));
  el('log-tipo').addEventListener('change', () => carregarLog(true));
  for (const b of document.querySelectorAll('.aba')) b.addEventListener('click', () => trocarAba(b.dataset.alvo));
  document.addEventListener('visibilitychange', () => { if (!document.hidden && token && el('auto').checked) carregarTudo(); });
  setInterval(() => { if (!document.hidden && token && el('auto').checked) carregarTudo(); }, intervalo);

  token = lerToken();
  if (token) { el('portao').hidden = true; el('conteudo').hidden = false; carregarTudo(); carregarLog(true); }
  else mostrarPortao('');
})();
