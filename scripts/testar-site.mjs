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
  constructor() {this.children = []; this.hidden = false; this.textContent = ''; this.events = {};}
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
  const resposta = {resumo: {tipos: []}, trajeto: {pontos: []}};
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
      const body = url.includes('resumo') ? resposta.resumo : resposta.trajeto;
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

const antes = a.chamadas();
a.document.hidden = true;
a.eventos.intervalo();
assert.equal(a.chamadas(), antes, 'Não consultar com aba oculta');
a.document.hidden = false;
const um = a.get('atualizar').events.click();
const dois = a.get('atualizar').events.click();
await Promise.all([um, dois]);
assert.equal(a.chamadas(), antes + 2, 'Não sobrepor consultas');

a.resposta.resumo = {outro: []};
await a.get('atualizar').events.click();
assert.match(a.get('estado-conexao').className, /erro/);
assert.equal(a.get('atualizar').disabled, false);

console.log('OK: referências locais, vazio, dado antigo, falha parcial, recuperação, mapa indisponível, limpeza do mapa, aba oculta, concorrência e contrato inválido.');
