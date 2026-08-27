# Landing page da Atlas

Página pública do projeto, com os dados do robô ao vivo embaixo. HTML, CSS e
JavaScript puros — sem build, sem `npm install`, sem framework. Para mexer,
abra o arquivo e edite.

```
site/
├── index.html    o conteúdo
├── estilo.css    a aparência (paleta da Setrem, fundo escuro)
├── ao-vivo.js    busca os dados na API e desenha o mapa
├── config.js     o único arquivo que muda entre instalações
└── README.md     este arquivo
```

## Ver localmente

```bash
cd site && python3 -m http.server 8080
# abra http://localhost:8080
```

Abrir o `index.html` com dois cliques também funciona para ver o layout, mas os
dados ao vivo não carregam: o navegador bloqueia requisições de páginas
servidas por `file://`.

## Apontar para a API

Só uma linha, em `config.js`:

```js
window.ATLAS_API = {
  base: 'https://api.seudominio.com.br',
  intervaloSegundos: 30,
};
```

E, do lado da VM, autorizar o domínio da página no `.env` do `cloud/`:

```bash
CORS_ORIGENS=https://atlas.seudominio.com.br
```

Sem isso o navegador recusa a resposta e a seção "ao vivo" mostra "sem contato
com o robô" — mesmo com a API funcionando perfeitamente quando testada pelo
`curl`. É o erro mais comum aqui, e o único cuja causa não aparece na tela.

## Sem token, de propósito

A página consulta apenas `/v1/publico/...`, que não exige autenticação e serve
menos: o resumo, e o trajeto com a posição arredondada para ~11 metros.

A alternativa seria pôr o token no JavaScript — o que faria dele um token
público com nome de segredo, legível por qualquer um que abrisse o inspetor do
navegador. Quem precisa dos dados completos é o aplicativo, que tem onde
guardar um segredo de verdade.

Para desligar as rotas públicas por inteiro (a página passa a mostrar só a
parte institucional): `PUBLICO_HABILITADO=false` no `.env` da VM.

## Publicar no Cloudflare Pages

O mesmo painel do domínio, e sem servidor:

1. **Workers & Pages → Create → Pages → Connect to Git**, e escolha este
   repositório.
2. Configure a build:
   - *Framework preset*: **None**
   - *Build command*: deixe vazio (não há build)
   - *Build output directory*: `site`
3. **Save and Deploy**. O primeiro deploy sai em um endereço `*.pages.dev`.
4. Em **Custom domains**, adicione o subdomínio que você quer (por exemplo
   `atlas.seudominio.com.br`). Como o domínio já está na Cloudflare, o DNS é
   criado sozinho e o certificado sai em minutos.

Cada `git push` na branch escolhida republica a página.

> A página fica no repositório `orquestrador` porque é dele que vem a API que
> ela consulta — os dois mudam juntos. Se um dia ela crescer e virar um site do
> projeto inteiro, o lugar natural é um repositório próprio; mover é copiar a
> pasta e reapontar o Pages.

## O que ela faz quando o robô está desligado

Degrada em silêncio, e é de propósito. Uma página institucional que aparece
quebrada porque um servidor caiu é pior que uma sem dados nenhum:

| Situação | O que a página mostra |
|---|---|
| API não configurada | aviso pedindo para editar o `config.js`; o resto do site funciona |
| VM fora do ar | "sem contato com o robô", e segue mostrando as três partes do projeto |
| Robô desligado | os números do histórico, com "último sinal há 3 d" |
| GPS não instalado | os números aparecem; no lugar do mapa, a explicação |

## Atribuição do mapa

O rodapé do mapa credita o OpenStreetMap. Isso não é enfeite: é o que a licença
dos dados (ODbL) pede em troca de usá-los sem chave de API e sem custo. Não
remova.
