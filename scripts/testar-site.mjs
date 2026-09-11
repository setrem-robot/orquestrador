// Executa os estados de rede do painel sem navegador, banco ou hardware.
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';
import vm from 'node:vm';

const site = resolve(dirname(fileURLToPath(import.meta.url)), '../site');
const script = readFileSync(resolve(site, 'ao-vivo.js'), 'utf8');
const paginas = ['index.html', 'painel/index.html'];
for (const pagina of paginas) {
  const html = readFileSync(resolve(site, pagina), 'utf8');
  for (const [, valor] of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
    if (/^(https?:|#)/.test(valor)) continue;
    assert.ok(existsSync(resolve(site, dirname(pagina), valor)), `${pagina}: ${valor}`);
  }
}

class Elemento {
  constructor() {this.children = []; this.hidden = false; this.textContent = ''; this.events = {}; this.style = {};}
  append(...itens) {this.children.push(...itens);}
  replaceChildren(...itens) {this.children = itens;}
  addEventListener(nome, callback) {this.events[nome] = callback;}
}

function ambiente() {
  const elementos = new Map();
  const get = id => {
    if (!elementos.has(id)) elementos.set(id, new Elemento());
    return elementos.get(id);
  };
  const resposta = {resumo: {tipos: []}, trajeto: {pontos: []}, saude: {dados: null}};
  const eventos = {};
  let chamadas = 0;
  const document = {
    hidden: false, getElementById: get, createElement: () => new Elemento(),
    addEventListener: (nome, fn) => {eventos[nome] = fn;},
  };
  const contexto = vm.createContext({
    document, window: {ATLAS_API: {base: '/api'}}, console,
    setTimeout, clearTimeout, setInterval: fn => {eventos.intervalo = fn;},
    AbortController,
    fetch: async url => {
      chamadas++;
      assert.ok(url.startsWith('/api/v1/publico/'), 'Apenas rotas públicas');
      const body = url.includes('resumo') ? resposta.resumo
        : url.includes('saude') ? resposta.saude
        : resposta.trajeto;
      if (body instanceof Error) throw body;
      return {ok: true, json: async () => body};
    },
  });
  vm.runInContext(script, contexto);
  return {get, resposta, eventos, document, contexto, chamadas: () => chamadas};
}

const aguardar = () => new Promise(resolve => setTimeout(resolve, 0));
const a = ambiente();
await aguardar();
assert.equal(a.get('total').textContent, '0');
assert.match(a.get('estado-conexao').textContent, /sem telemetria/);
assert.equal(a.get('mapa').hidden, true);
assert.equal(a.get('atualizar').disabled, false);

a.resposta.resumo = {tipos: [{tipo: 'gps', total: 42, ultima: new Date(Date.now() - 3600000).toISOString()}]};
a.resposta.trajeto = new Error('GPS indisponível');
await a.get('atualizar').events.click();
assert.equal(a.get('total').textContent, '42');
assert.match(a.get('estado-conexao').textContent, /Sem sinal recente/);
assert.match(a.get('aviso').textContent, /trajeto/);

a.resposta.resumo = new Error('Servidor indisponível');
await a.get('atualizar').events.click();
assert.equal(a.get('total').textContent, '42', 'Preservar último resumo na falha');
assert.match(a.get('estado-conexao').className, /erro/);
assert.match(a.get('aviso').textContent, /últimos registros/);
assert.equal(a.get('atualizar').disabled, false);

a.resposta.resumo = {tipos: [{tipo: 'gps', total: 43, ultima: new Date().toISOString()}]};
a.resposta.trajeto = {pontos: [{lat: -28.1, lon: -54.1, ts: new Date().toISOString()}]};
await a.get('atualizar').events.click();
assert.equal(a.get('total').textContent, '43');
assert.match(a.get('estado-conexao').className, /vivo/);
assert.match(a.get('mapa-vazio-titulo').textContent, /carregar o mapa/);

let removido = 0;
const mapa = {invalidateSize() {}, remove() {removido++;}, removeLayer() {}, fitBounds() {}};
a.contexto.window.L = {
  map: () => mapa, tileLayer: () => ({addTo() {}}),
  polyline: () => ({getBounds() {return [];}}), circleMarker: () => ({}),
  layerGroup: () => ({addTo() {return {};}}),
};
await a.get('atualizar').events.click();
assert.equal(a.get('mapa').hidden, false);
a.resposta.trajeto = {pontos: []};
await a.get('atualizar').events.click();
assert.equal(a.get('mapa').hidden, true);
assert.equal(removido, 1, 'Remover a posição anterior quando o período fica vazio');

a.resposta.saude = {ts: new Date().toISOString(), idade_s: 4, dados: {
  temperatura_c: 66.5, throttled: {ok: true},
  cpu: {uso_pct: 12.3, freq_mhz: 2400, governor: 'ondemand', voltagem_v: 0.887, por_nucleo: [10, 20, 30, 40]},
  memoria: {uso_pct: 55, disponivel_mb: 3600, swap_total_mb: 2048, swap_usado_mb: 0},
  disco: {uso_pct: 42, livre_gb: 16.6}, uptime_s: 7200, rede: {eth0: {rx_mb: 1.1, tx_mb: 0.4}},
}};
await a.get('atualizar').events.click();
assert.match(a.get('s-temp').textContent, /66\.5/);
assert.match(a.get('s-cpu').textContent, /12/);
assert.match(a.get('saude-estado').className, /vivo/);
assert.equal(a.get('s-nucleos').children.length, 4, 'Uma barra por núcleo');

a.resposta.saude = {ts: new Date().toISOString(), dados: {temperatura_c: 80, throttled: {ok: false}, cpu: {}, memoria: {}, disco: {}}};
await a.get('atualizar').events.click();
assert.match(a.get('saude-estado').className, /erro/, 'Subtensão/limite vira alerta');

const antes = a.chamadas();
a.document.hidden = true;
a.eventos.intervalo();
assert.equal(a.chamadas(), antes, 'Não consultar com aba oculta');
a.document.hidden = false;
const um = a.get('atualizar').events.click();
const dois = a.get('atualizar').events.click();
await Promise.all([um, dois]);
assert.equal(a.chamadas(), antes + 3, 'Não sobrepor consultas (resumo, trajeto e saúde)');

a.resposta.resumo = {outro: []};
await a.get('atualizar').events.click();
assert.match(a.get('estado-conexao').className, /erro/);
assert.equal(a.get('atualizar').disabled, false);

console.log('OK: referências locais, vazio, dado antigo, falha parcial, recuperação, mapa indisponível, limpeza do mapa, saúde do robô, alerta de subtensão, aba oculta, concorrência e contrato inválido.');
