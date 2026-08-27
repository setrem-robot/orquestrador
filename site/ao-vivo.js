/**
 * Os dados ao vivo da página.
 *
 * Consulta só as rotas públicas da API — sem token, e servindo menos: o resumo
 * de quantas mensagens de cada tipo existem, e o trajeto recente com a posição
 * arredondada. Ver `cloud/api/app/seguranca.py`.
 *
 * A regra desta página é **degradar em silêncio**. Se a VM estiver fora do ar,
 * se o robô estiver desligado, se a API não estiver configurada — a seção "ao
 * vivo" diz isso em uma frase e o resto da página continua servindo. Uma
 * página institucional que aparece quebrada porque um servidor caiu é pior que
 * uma sem dados nenhum.
 */
(function () {
  'use strict';

  var config = window.ATLAS_API || {};
  var base = (config.base || '').replace(/\/+$/, '');
  var intervalo = (config.intervaloSegundos || 30) * 1000;

  var elEstado = document.getElementById('estado-conexao');
  var elPainel = document.getElementById('painel');
  var elNumeros = document.getElementById('numeros');
  var elSemDados = document.getElementById('sem-dados');

  var mapa = null;
  var linha = null;
  var marcador = null;

  /** Idade em texto curto: "agora", "há 3 min", "há 2 h", "há 5 d". */
  function idade(segundos) {
    if (segundos == null) return 'sem data';
    if (segundos < 20) return 'agora';
    if (segundos < 3600) return 'há ' + Math.round(segundos / 60) + ' min';
    if (segundos < 86400) return 'há ' + Math.round(segundos / 3600) + ' h';
    return 'há ' + Math.round(segundos / 86400) + ' d';
  }

  function marcarEstado(classe, texto) {
    elEstado.className = 'estado ' + classe;
    elEstado.textContent = texto;
  }

  function mostrarAviso(texto) {
    elPainel.hidden = true;
    elSemDados.hidden = false;
    elSemDados.textContent = texto;
  }

  function buscar(caminho) {
    // `cache: no-store` porque a Cloudflare guardaria a resposta na borda, e a
    // página passaria a mostrar um estado velho jurando que é ao vivo.
    return fetch(base + caminho, { cache: 'no-store' }).then(function (resposta) {
      if (!resposta.ok) throw new Error('HTTP ' + resposta.status);
      return resposta.json();
    });
  }

  /** Os cartões de número, montados a partir do resumo. */
  function desenharNumeros(resumo) {
    var porTipo = {};
    (resumo.tipos || []).forEach(function (item) {
      porTipo[item.tipo] = item;
    });

    var nomes = {
      gps: 'Posições registradas',
      motores: 'Comandos de motor',
      bateria: 'Leituras de bateria',
      wifi: 'Estados de rede',
    };

    var total = (resumo.tipos || []).reduce(function (soma, item) {
      return soma + (item.total || 0);
    }, 0);

    var cartoes = [
      '<li><span class="rotulo">Mensagens guardadas</span>' +
        '<span class="valor">' + total.toLocaleString('pt-BR') + '</span>' +
        '<span class="detalhe">desde que o robô começou a publicar</span></li>',
    ];

    Object.keys(nomes).forEach(function (tipo) {
      var item = porTipo[tipo];
      if (!item) return;
      cartoes.push(
        '<li><span class="rotulo">' + nomes[tipo] + '</span>' +
          '<span class="valor">' + (item.total || 0).toLocaleString('pt-BR') + '</span>' +
          '<span class="detalhe">última ' + idade(item.idade_s) + '</span></li>'
      );
    });

    elNumeros.innerHTML = cartoes.join('');

    // "Vivo" é sobre o dado mais recente de qualquer tipo, e não sobre a API
    // responder: a API pode estar ótima e o robô, desligado há uma semana.
    var maisNovo = (resumo.tipos || []).reduce(function (menor, item) {
      if (item.idade_s == null) return menor;
      return menor == null || item.idade_s < menor ? item.idade_s : menor;
    }, null);

    if (maisNovo == null) {
      marcarEstado('parado', 'sem telemetria ainda');
    } else if (maisNovo < 120) {
      marcarEstado('vivo', 'o robô está publicando');
    } else {
      marcarEstado('parado', 'último sinal ' + idade(maisNovo));
    }
  }

  function desenharMapa(pontos) {
    var caminho = pontos
      .filter(function (p) { return p.lat != null && p.lon != null; })
      .map(function (p) { return [p.lat, p.lon]; });

    if (!caminho.length) return false;

    if (!mapa) {
      mapa = L.map('mapa', { scrollWheelZoom: false });
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        // A licença dos dados (ODbL) pede a atribuição. Não remova.
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(mapa);
    }

    if (linha) mapa.removeLayer(linha);
    if (marcador) mapa.removeLayer(marcador);

    linha = L.polyline(caminho, { color: '#00bf6f', weight: 4 }).addTo(mapa);
    marcador = L.circleMarker(caminho[caminho.length - 1], {
      radius: 7,
      color: '#0a0e1a',
      weight: 2,
      fillColor: '#24ffc2',
      fillOpacity: 1,
    }).addTo(mapa);

    mapa.fitBounds(linha.getBounds(), { padding: [28, 28] });
    return true;
  }

  function atualizar() {
    if (!base) {
      marcarEstado('erro', 'API não configurada');
      mostrarAviso(
        'Falta apontar esta página para a API: edite o endereço em config.js. ' +
          'Enquanto isso, o resto do site funciona normalmente.'
      );
      return;
    }

    Promise.all([
      buscar('/v1/publico/resumo'),
      // O trajeto pode falhar sozinho (GPS ainda não instalado) sem derrubar os
      // números — daí ele ser tratado aqui, e não no `catch` de baixo.
      buscar('/v1/publico/trajeto?limite=300').catch(function () {
        return { pontos: [] };
      }),
    ])
      .then(function (respostas) {
        var resumo = respostas[0];
        var trajeto = respostas[1];

        if (!resumo.tipos || !resumo.tipos.length) {
          marcarEstado('parado', 'robô ainda não publicou nada');
          mostrarAviso(
            'O robô ainda não gravou telemetria. Assim que ele for ligado com ' +
              'rede, o mapa e os números aparecem aqui sozinhos.'
          );
          return;
        }

        elSemDados.hidden = true;
        elPainel.hidden = false;
        desenharNumeros(resumo);

        // O mapa precisa estar visível antes de o Leaflet medir o contêiner:
        // criado escondido, ele calcula tamanho zero e desenha um mapa cinza.
        var temMapa = desenharMapa(trajeto.pontos || []);
        if (!temMapa) {
          document.getElementById('mapa').innerHTML =
            '<p style="padding:24px;color:rgba(255,255,255,.38)">' +
            'O GPS ainda não está instalado no robô — quando estiver, o trajeto ' +
            'aparece aqui.</p>';
        }
      })
      .catch(function (erro) {
        marcarEstado('erro', 'sem contato com o robô');
        mostrarAviso(
          'Não consegui falar com a API do robô agora. Ela roda numa VM do ' +
            'LARCC e pode estar fora do ar — o resto da página continua valendo.'
        );
        if (window.console) console.warn('atlas: falha ao consultar a API', erro);
      });
  }

  atualizar();

  // Só continua atualizando com a aba à vista. Uma página esquecida numa TV do
  // laboratório não precisa bater na VM a cada trinta segundos por dias.
  setInterval(function () {
    if (!document.hidden) atualizar();
  }, intervalo);
})();
