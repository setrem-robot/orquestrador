// Mesmo domínio em produção: o Caddy encaminha /api para a API de leitura.
// O servidor de prévia também encaminha /api. Abrir pelo sistema de arquivos
// é útil para ver o desenho, mas a telemetria pode ser bloqueada por CORS.
// Uma instalação de teste pode definir ATLAS_API antes deste arquivo.
window.ATLAS_API = Object.assign({
  base: location.protocol === 'file:' ? 'https://atlas.kerlonr.com.br/api' : '/api',
  intervaloSegundos: 30,
}, window.ATLAS_API || {});
