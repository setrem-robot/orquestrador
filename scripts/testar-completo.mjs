// Exercita a tela de análise completa (completo.js) sem navegador nem API:
// portão de token, montagem dos gráficos a partir dos eventos, e o corte de
// sessão quando a API responde 401. window.L fica indefinido de propósito —
// assim o caminho do mapa cai no "sem trajeto" e não exige stub do Leaflet.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';
import vm from 'node:vm';

const site = resolve(dirname(fileURLToPath(import.meta.url)), '../site');
const script = readFileSync(resolve(site, 'completo/completo.js'), 'utf8');

class Elemento {
  constructor() {
    this.children = []; this.hidden = false; this.textContent = '';
    this.className = ''; this.value = ''; this.checked = false; this.title = '';
    this.colSpan = 0; this.style = {}; this.attrs = {}; this.events = {};
  }
  append(...itens) { for (const i of itens) this.children.push(i); }
  replaceChildren(...itens) { this.children = itens; }
  addEventListener(nome, cb) { this.events[nome] = cb; }
  setAttribute(k, v) { this.attrs[k] = v; }
  focus() {}
  remove() {}
}

const iso = ms => new Date(ms).toISOString();
function respostas(agora) {
  const dadosSis = {
    temperatura_c: 66, throttled: {ok: true, subtensao_agora: false, limite_termico_ja_ocorreu: false},
    cpu: {uso_pct: 10, freq_mhz: 1500, governor: 'ondemand', voltagem_v: 0.88, por_nucleo: [10, 20, 30, 40]},
    memoria: {uso_pct: 55, disponivel_mb: 3600, swap_total_mb: 2048, swap_usado_mb: 0},
    disco: {uso_pct: 42, livre_gb: 16}, rede: {eth0: {rx_mb: 1.1, tx_mb: 0.4}}, processos: {rodando: 2, total: 200}, uptime_s: 7200,
  };
  const eventosSis = [0, 30, 60].map(s => ({
    ts: iso(agora - s * 1000), tipo: 'sistema', topico: 'robo/telemetria/sistema',
    dados: {temperatura_c: 65 + s / 60, cpu: {uso_pct: 8 + s / 30, freq_mhz: 1500, por_nucleo: [5, 6, 7, 8]}, memoria: {uso_pct: 54}},
  }));
  return {
    estado: {itens: {
      sistema: {ts: iso(agora), idade_s: 2, dados: dadosSis},
      bateria: {ts: iso(agora), dados: {demo: true, percentual: 72, tensao_v: 12.1}},
      motores: {ts: iso(agora), dados: {demo: true, acao: 'parar', esquerda: 0, direita: 0, velocidade: 0}},
      gps: {ts: iso(agora), dados: {demo: true, fix: true, lat: -27.77, lon: -54.23, satelites: 7, velocidade_kmh: 1.3}},
      wifi: {ts: iso(agora), dados: {demo: true, conectado: false, ssid: '', ip: ''}},
    }},
    eventosSis, eventosBat: {eventos: [], proximo_antes_de: null},
    trajeto: {pontos: [], total: 0},
    log: {eventos: [{ts: iso(agora), tipo: 'sistema', topico: 'robo/telemetria/sistema', dados: dadosSis}], proximo_antes_de: null},
  };
}

function ambiente({tokenInicial = '', status = 200} = {}) {
  const agora = Date.now();
  const dados = respostas(agora);
  const elementos = new Map();
  const get = id => { if (!elementos.has(id)) elementos.set(id, new Elemento()); return elementos.get(id); };
  const armazem = () => { const m = new Map(); return {getItem: k => m.has(k) ? m.get(k) : null, setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k)}; };
  const local = armazem(), sessao = armazem();
  if (tokenInicial) local.setItem('atlas_token', tokenInicial);
  const document = {
    hidden: false, getElementById: get,
    createElement: () => new Elemento(),
    createElementNS: () => new Elemento(),
    createTextNode: t => ({textContent: String(t)}),
    addEventListener: () => {},
  };
  let chamadas = 0;
  const contexto = vm.createContext({
    document, console, setTimeout, clearTimeout, setInterval: () => {}, AbortController,
    localStorage: local, sessionStorage: sessao,
    window: {ATLAS_API: {base: '/api'}},
    fetch: async url => {
      chamadas++;
      if (status !== 200) { const e = new Error('HTTP ' + status); if (status === 401 || status === 403) e.auth = true; throw e; }
      const corpo = url.includes('/v1/estado') ? dados.estado
        : url.includes('tipo=sistema') && url.includes('limite=500') ? dados.eventosSis && {eventos: dados.eventosSis, proximo_antes_de: null}
        : url.includes('/v1/trajeto') ? dados.trajeto
        : url.includes('limite=50') ? dados.log
        : dados.eventosBat;
      return {ok: true, status: 200, json: async () => corpo};
    },
  });
  // buscar() lança e.auth em 401; para simular, embrulhamos: fetch acima já joga.
  vm.runInContext(script, contexto);
  return {get, chamadas: () => chamadas, elementos};
}

const espera = () => new Promise(r => setTimeout(r, 0));

// 1) Sem token: portão visível, conteúdo escondido, nada é buscado.
{
  const a = ambiente({tokenInicial: ''});
  await espera();
  assert.equal(a.get('portao').hidden, false, 'portão aparece sem token');
  assert.equal(a.get('conteudo').hidden, true, 'conteúdo escondido sem token');
  assert.equal(a.chamadas(), 0, 'nada é buscado sem token');
}

// 2) Entrar pelo formulário: guarda token, revela conteúdo, busca e desenha.
{
  const a = ambiente({tokenInicial: ''});
  await espera();
  a.get('campo-token').value = '  token-de-teste  ';
  a.get('lembrar').checked = true;
  await a.get('entrar').events.click();
  await espera(); await espera();
  assert.equal(a.get('conteudo').hidden, false, 'conteúdo aparece após entrar');
  assert.ok(a.chamadas() >= 4, 'buscou estado, históricos e trajeto');
  // visão geral: um cartão por tipo (5)
  assert.equal(a.get('cartoes-tipos').children.length, 5, 'cinco cartões de tipo');
  // saúde preenchida
  assert.match(a.get('s-temp').textContent, /66/);
  assert.match(a.get('s-cpu').textContent, /10/);
  assert.equal(a.get('s-nucleos').children.length, 4, 'quatro núcleos');
  // gráficos de sistema montados (5 figuras: temp, cpu, núcleos, memória, freq)
  assert.equal(a.get('g-sistema').children.length, 5, 'cinco gráficos de sistema');
  // log carregou uma linha
  assert.ok(a.get('log-corpo').children.length >= 1, 'log tem linhas');
}

// 3) Token guardado no início: entra direto, sem portão.
{
  const a = ambiente({tokenInicial: 'ja-tinha'});
  await espera(); await espera();
  assert.equal(a.get('portao').hidden, true, 'sem portão quando já há token');
  assert.equal(a.get('conteudo').hidden, false);
  assert.equal(a.get('sec-gps') && a.get('mapa-vazio').hidden, false, 'sem Leaflet, mostra "sem trajeto"');
}

// 4) API 401: corta a sessão e volta ao portão com erro.
{
  const a = ambiente({tokenInicial: 'invalido', status: 401});
  await espera(); await espera();
  assert.equal(a.get('portao').hidden, false, 'portão volta no 401');
  assert.match(a.get('portao-erro').textContent, /inválido|permiss/i);
}

console.log('OK: portão sem token, entrada, sessão persistida, gráficos montados, log e corte por 401.');
