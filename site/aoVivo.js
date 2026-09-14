/* Leitura pública: nenhuma credencial e nenhum comando de movimento. */
(function () {
  'use strict';

  const config = window.ATLAS_API || {};
  const base = String(config.base || '/api').replace(/\/+$/, '');
  const intervalo = Math.max(15, Number(config.intervaloSegundos) || 30) * 1000;
  const el = id => document.getElementById(id);
  const nomes = {gps: 'GPS', motores: 'Motores', bateria: 'Bateria', wifi: 'Rede Wi-Fi'};
  const numero = valor => Number.isFinite(Number(valor)) ? Math.max(0, Number(valor)) : 0;
  let consultando = false;
  let resumoAtual = null;
  let resumoRecebidoEm = 0;
  let ultimaConsulta = null;
  let mapa = null;
  let camada = null;
  let assinaturaTrajeto = '';

  function idade(segundos) {
    if (!Number.isFinite(segundos)) return 'Sem data';
    if (segundos < 0) return 'Horário adiantado';
    if (segundos < 60) return 'Agora';
    if (segundos < 3600) return 'Há ' + Math.floor(segundos / 60) + ' min';
    if (segundos < 86400) return 'Há ' + Math.floor(segundos / 3600) + ' h';
    return 'Há ' + Math.floor(segundos / 86400) + ' dias';
  }

  function tempoLigado(segundos) {
    if (!Number.isFinite(segundos) || segundos < 0) return '—';
    const d = Math.floor(segundos / 86400);
    const h = Math.floor((segundos % 86400) / 3600);
    const m = Math.floor((segundos % 3600) / 60);
    if (d) return d + 'd ' + h + 'h';
    if (h) return h + 'h ' + m + 'min';
    return m + 'min';
  }

  const num = (v, casas = 0) => Number.isFinite(Number(v)) ? Number(v).toFixed(casas) : null;

  function idadeItem(item) {
    const timestamp = Date.parse(item.ultima);
    if (Number.isFinite(timestamp)) return (Date.now() - timestamp) / 1000;
    if (typeof item.idade_s === 'number' && Number.isFinite(item.idade_s)) {
      return item.idade_s + (Date.now() - resumoRecebidoEm) / 1000;
    }
    return null;
  }

  function estado(classe, texto) {
    el('estado-conexao').className = 'estado ' + classe;
    el('estado-conexao').textContent = texto;
  }

  async function buscar(caminho) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const resposta = await fetch(base + caminho, {cache: 'no-store', signal: controller.signal});
      if (!resposta.ok) throw new Error('HTTP ' + resposta.status);
      return await resposta.json();
    } finally {
      clearTimeout(timeout);
    }
  }

  function desenharResumo(resumo) {
    const tipos = resumo.tipos.filter(item => item && typeof item.tipo === 'string');
    const porTipo = Object.fromEntries(tipos.map(item => [item.tipo, item]));
    el('total').textContent = tipos.reduce((soma, item) => soma + numero(item.total), 0).toLocaleString('pt-BR');
    el('gps').textContent = numero(porTipo.gps?.total).toLocaleString('pt-BR');
    el('motores').textContent = numero(porTipo.motores?.total).toLocaleString('pt-BR');
    el('fontes').textContent = tipos.length.toLocaleString('pt-BR');
    el('sinais').replaceChildren();
    if (!tipos.length) {
      const li = document.createElement('li');
      li.className = 'sem-registro';
      li.textContent = 'Nenhuma fonte publicou registros disponíveis.';
      el('sinais').append(li);
      estado('parado', 'Ainda sem telemetria');
      return;
    }
    for (const item of tipos) {
      const li = document.createElement('li');
      const nome = document.createElement('span');
      nome.className = 'nome';
      nome.textContent = nomes[item.tipo] || item.tipo;
      const dados = document.createElement('div');
      dados.className = 'valor-sinal';
      const quantidade = document.createElement('div');
      quantidade.className = 'total';
      quantidade.textContent = numero(item.total).toLocaleString('pt-BR') + ' registros';
      const tempo = document.createElement('time');
      tempo.textContent = idade(idadeItem(item));
      const timestamp = Date.parse(item.ultima);
      if (Number.isFinite(timestamp)) {
        tempo.dateTime = new Date(timestamp).toISOString();
        tempo.title = new Date(timestamp).toLocaleString('pt-BR');
      }
      dados.append(quantidade, tempo);
      li.append(nome, dados);
      el('sinais').append(li);
    }
    const idades = tipos.map(idadeItem).filter(v => v !== null && Number.isFinite(v));
    const maisNovo = idades.length ? Math.min(...idades) : null;
    if (maisNovo === null) estado('parado', 'Registros sem horário disponível');
    else if (maisNovo < 0) estado('parado', 'Há registros com horário adiantado');
    else if (maisNovo < 120) estado('vivo', 'Telemetria recente');
    else estado('parado', 'Sem sinal recente · último registro ' + idade(maisNovo).toLowerCase());
  }

  function mapaVazio(titulo, texto) {
    // Desmontar evita manter posição antiga quando uma nova consulta vem vazia.
    if (mapa) mapa.remove();
    mapa = null;
    camada = null;
    assinaturaTrajeto = '';
    el('mapa').hidden = true;
    el('mapa-vazio').hidden = false;
    el('mapa-vazio-titulo').textContent = titulo;
    el('mapa-vazio-texto').textContent = texto;
    el('pontos-mapa').textContent = 'Sem trajeto disponível';
  }

  function desenharTrajeto(trajeto) {
    const pontos = trajeto.pontos.filter(p => p && typeof p.lat === 'number' && typeof p.lon === 'number' &&
      Number.isFinite(p.lat) && Number.isFinite(p.lon) && Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180);
    if (!pontos.length) {
      mapaVazio('Sem posições neste período', 'Não há registros de GPS disponíveis nas últimas 24 horas.');
      return;
    }
    if (!window.L) {
      mapaVazio('Não foi possível carregar o mapa', 'A biblioteca de mapas não está disponível. Os demais registros continuam acessíveis.');
      return;
    }
    el('mapa-vazio').hidden = true;
    el('mapa').hidden = false;
    if (!mapa) {
      mapa = window.L.map('mapa', {scrollWheelZoom: false});
      window.L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(mapa);
    }
    mapa.invalidateSize();
    const assinatura = JSON.stringify(pontos.map(p => [p.ts, p.lat, p.lon]));
    if (assinatura !== assinaturaTrajeto) {
      if (camada) mapa.removeLayer(camada);
      const caminho = pontos.map(p => [p.lat, p.lon]);
      const linha = window.L.polyline(caminho, {color: '#039f70', weight: 4});
      const marcador = window.L.circleMarker(caminho[caminho.length - 1], {
        radius: 7, color: '#093427', weight: 2, fillColor: '#45edb5', fillOpacity: 1,
      });
      camada = window.L.layerGroup([linha, marcador]).addTo(mapa);
      mapa.fitBounds(linha.getBounds(), {padding: [28, 28], maxZoom: 17});
      assinaturaTrajeto = assinatura;
    }
    const ultima = Date.parse(pontos[pontos.length - 1].ts);
    el('pontos-mapa').textContent = pontos.length + ' posições · última: ' +
      (Number.isFinite(ultima) ? new Date(ultima).toLocaleString('pt-BR') : 'horário indisponível');
    el('precisao-mapa').textContent = Number.isInteger(trajeto.precisao_casas) && trajeto.precisao_casas > 0
      ? 'Coordenadas arredondadas a ' + trajeto.precisao_casas + ' casas'
      : 'Coordenadas fornecidas pelo serviço público';
  }

  function saudeEstado(classe, texto) {
    const alvo = el('saude-estado');
    alvo.className = 'estado ' + classe;
    alvo.textContent = texto;
  }

  function desenharSaude(resp) {
    const d = resp && resp.dados;
    if (!d || typeof d !== 'object') {
      saudeEstado('parado', 'Sem leitura ainda');
      el('s-temp').textContent = el('s-cpu').textContent = el('s-mem').textContent = el('s-uptime').textContent = '—';
      el('s-nucleos').replaceChildren(Object.assign(document.createElement('li'), {className: 'sem-registro', textContent: 'Sem leitura de saúde disponível.'}));
      el('s-atualizado').textContent = 'Aguardando leitura…';
      return;
    }
    const cpu = d.cpu || {};
    const mem = d.memoria || {};
    const disco = d.disco || {};
    const thr = d.throttled;

    el('s-temp').textContent = num(d.temperatura_c, 1) !== null ? num(d.temperatura_c, 1) + ' °C' : '—';
    const throttledOk = thr && thr.ok;
    el('s-throttled').textContent = thr ? (throttledOk ? 'Energia e térmica OK' : 'Atenção: subtensão ou limite') : '—';
    el('s-throttled').className = 'detalhe' + (thr && !throttledOk ? ' alerta' : '');

    el('s-cpu').textContent = num(cpu.uso_pct) !== null ? num(cpu.uso_pct) + ' %' : '—';
    el('s-freq').textContent = [
      cpu.freq_mhz ? cpu.freq_mhz + ' MHz' : null,
      cpu.governor || null,
      num(cpu.voltagem_v, 2) !== null ? num(cpu.voltagem_v, 2) + ' V' : null,
    ].filter(Boolean).join(' · ') || '—';

    el('s-mem').textContent = num(mem.uso_pct) !== null ? num(mem.uso_pct) + ' %' : '—';
    const detMem = [];
    if (Number.isFinite(mem.disponivel_mb)) detMem.push(mem.disponivel_mb.toLocaleString('pt-BR') + ' MB livres');
    if (Number.isFinite(mem.swap_usado_mb) && mem.swap_usado_mb > 0) detMem.push('swap ' + mem.swap_usado_mb + ' MB');
    el('s-swap').textContent = detMem.join(' · ') || '—';

    el('s-uptime').textContent = tempoLigado(d.uptime_s);
    el('s-disco').textContent = num(disco.uso_pct) !== null
      ? 'Disco ' + num(disco.uso_pct) + ' %' + (Number.isFinite(disco.livre_gb) ? ' · ' + disco.livre_gb + ' GB livres' : '')
      : '';

    const lista = el('s-nucleos');
    lista.replaceChildren();
    const nucleos = Array.isArray(cpu.por_nucleo) ? cpu.por_nucleo : [];
    if (!nucleos.length) {
      lista.append(Object.assign(document.createElement('li'), {className: 'sem-registro', textContent: 'Uso por núcleo aparece na próxima leitura.'}));
    }
    nucleos.forEach((valor, i) => {
      const pct = Math.max(0, Math.min(100, Number(valor) || 0));
      const li = document.createElement('li');
      const nome = document.createElement('span');
      nome.className = 'n-nome';
      nome.textContent = 'Núcleo ' + i;
      const barra = document.createElement('div');
      barra.className = 'barra';
      const preenche = document.createElement('i');
      preenche.style.width = pct + '%';
      if (pct >= 85) preenche.className = 'quente';
      barra.append(preenche);
      const val = document.createElement('span');
      val.className = 'n-val';
      val.textContent = Math.round(pct) + ' %';
      li.append(nome, barra, val);
      lista.append(li);
    });

    const rede = d.rede && typeof d.rede === 'object' ? d.rede : {};
    const paresRede = Object.entries(rede);
    el('s-rede').textContent = paresRede.length
      ? 'Rede: ' + paresRede.map(([nome, v]) => nome + ' ↓' + v.rx_mb + ' ↑' + v.tx_mb + ' MB').join(' · ')
      : 'Rede: —';

    const ts = Date.parse(resp.ts);
    const idadeS = Number.isFinite(ts) ? (Date.now() - ts) / 1000 : (typeof resp.idade_s === 'number' ? resp.idade_s : NaN);
    el('s-atualizado').textContent = Number.isFinite(ts) ? 'Leitura ' + idade(idadeS).toLowerCase() : 'Horário indisponível';
    if (!Number.isFinite(idadeS)) saudeEstado('parado', 'Leitura sem horário');
    else if (idadeS < 0) saudeEstado('parado', 'Horário adiantado');
    else if (!throttledOk && thr) saudeEstado('erro', 'Atenção na alimentação/temperatura');
    else if (idadeS < 90) saudeEstado('vivo', 'Leitura recente');
    else saudeEstado('parado', 'Sem leitura recente · ' + idade(idadeS).toLowerCase());
  }

  async function atualizar() {
    if (consultando) return;
    consultando = true;
    el('atualizar').disabled = true;
    if (resumoAtual) desenharResumo(resumoAtual);
    el('ultima-consulta').textContent = 'Consultando…';
    try {
      const [resumo, trajeto, saude] = await Promise.allSettled([
        buscar('/v1/publico/resumo').then(d => {
          if (!d || !Array.isArray(d.tipos)) throw new Error('Resumo inválido');
          return d;
        }),
        buscar('/v1/publico/trajeto?limite=300').then(d => {
          if (!d || !Array.isArray(d.pontos)) throw new Error('Trajeto inválido');
          return d;
        }),
        buscar('/v1/publico/saude').then(d => {
          if (!d || typeof d !== 'object') throw new Error('Saúde inválida');
          return d;
        }),
      ]);
      const avisos = [];
      if (resumo.status === 'fulfilled') {
        resumoAtual = resumo.value;
        resumoRecebidoEm = Date.now();
        ultimaConsulta = new Date();
        desenharResumo(resumoAtual);
      } else {
        estado('erro', 'Não foi possível atualizar a telemetria');
        avisos.push(resumoAtual ? 'Exibindo os últimos registros consultados. Tente atualizar novamente.' :
          'Os registros não estão disponíveis agora. Isso não permite concluir se o robô está ligado.');
        if (!resumoAtual) el('sinais').textContent = 'Consulta indisponível.';
      }
      if (trajeto.status === 'fulfilled') desenharTrajeto(trajeto.value);
      else {
        mapaVazio('Trajeto indisponível', 'Não foi possível consultar as posições. Tente novamente em instantes.');
        avisos.push('A consulta do trajeto não respondeu.');
      }
      // A saúde do robô fala por si na própria seção; uma falha aqui não vira
      // aviso global — o painel de contagens e trajeto continua legível.
      if (saude.status === 'fulfilled') desenharSaude(saude.value);
      else saudeEstado('erro', 'Não foi possível ler a saúde');
      el('aviso').textContent = avisos.join(' ');
      el('aviso').hidden = !avisos.length;
      el('ultima-consulta').textContent = ultimaConsulta
        ? 'Resumo consultado às ' + ultimaConsulta.toLocaleTimeString('pt-BR')
        : 'Nenhuma consulta concluída';
    } catch (erro) {
      estado('erro', 'Não foi possível exibir os dados');
      el('aviso').textContent = 'Ocorreu uma falha ao exibir as informações. Tente atualizar novamente.';
      el('aviso').hidden = false;
      el('ultima-consulta').textContent = 'Consulta incompleta';
      console.warn('Atlas: falha ao exibir telemetria', erro);
    } finally {
      consultando = false;
      el('atualizar').disabled = false;
    }
  }

  el('atualizar').addEventListener('click', atualizar);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) atualizar();});
  setInterval(() => {if (!document.hidden) atualizar();}, intervalo);
  atualizar();
})();
