// Onde a página busca os dados ao vivo.
//
// Este é o único arquivo que muda entre uma instalação e outra. Ele fica
// separado do resto de propósito: dá para publicar o site inteiro e ajustar o
// endereço depois, sem reconstruir nada — e é o que permite ter uma cópia de
// teste apontando para outra VM.
//
// A página **não usa token**. Só as rotas `/v1/publico/...` são consultadas
// aqui, e elas servem menos: o resumo, e o trajeto com a precisão reduzida a
// ~11 metros. Um token no JavaScript de uma página estática seria um segredo à
// vista de qualquer um que abrisse o inspetor — melhor não fingir que é um.
window.ATLAS_API = {
  // O domínio publicado pelo túnel da Cloudflare. Sem barra no fim.
  base: 'https://api.seudominio.com.br',

  // De quantos em quantos segundos atualizar. Trinta segundos é bastante para
  // uma página de apresentação, e mantém a VM em paz mesmo com a página
  // esquecida aberta numa TV do laboratório.
  intervaloSegundos: 30,
};
