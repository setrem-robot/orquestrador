# Site e painel da Atlas

Três áreas, na mesma instalação e sem processo de build:

| Caminho | Uso |
|---|---|
| `/` | Apresentação do projeto, propósito, componentes e links para código |
| `/painel/` | Consulta dos registros públicos e do trajeto recente (sem token) |
| `/completo/` | Console de análise de **toda** a telemetria; pede token, guardado só no navegador |
| `/api/*` | API existente; Caddy remove o prefixo antes de encaminhar |

## Prévia local

Na raiz do repositório:

```console
python scripts/preview-site.py
```

Abra `http://127.0.0.1:8093/` ou `/painel/`. O servidor de prévia encaminha
somente as duas consultas públicas à instalação existente, evitando bloqueio
por origem no navegador. A porta pode ser alterada com `--port 8094`.

Abrir o HTML diretamente pelo disco permite ver a apresentação, mas não é a
prévia indicada para consultar dados: o navegador pode recusar as requisições.

## Dados do painel

- `GET /api/v1/publico/resumo`: contagem de registros e última data por tipo.
- `GET /api/v1/publico/trajeto?limite=300`: posições recentes com a precisão
  fornecida pelo serviço público.
- `GET /api/v1/publico/saude`: a última leitura da saúde do Pi (temperatura,
  CPU, memória, disco, rede) — sem token, porque não revela onde o robô está.
- Consultas a cada 30 segundos, suspensas com a aba oculta, retomadas ao voltar.
- Atualização manual, timeout, proteção contra consultas sobrepostas e falhas
  tratadas separadamente para resumo e trajeto.

Contagens não são valores atuais dos sensores. A interface não inventa carga
de bateria, velocidade ou conexão do robô. Ela usa o horário do último registro
para indicar dados antigos. Nenhuma credencial fica no JavaScript e nenhum
comando de movimento é enviado. A API pública atual não identifica registros
de demonstração no resumo; a interface os apresenta como registros disponíveis,
sem afirmar que representam uma operação física atual.

Mapa: Leaflet 1.9.4 e tiles OpenStreetMap, com atribuição. Se a biblioteca ou
consulta de posições falhar, os demais dados continuam utilizáveis.

## Publicação no domínio existente

O túnel da Cloudflare já encaminha `atlas.kerlonr.com.br` para o Caddy. O compose
monta `../site` em `/srv/atlas` somente para leitura; o Caddy serve a apresentação
e o painel e mantém a API em `/api/`. Não mudar o DNS para outro serviço.

Quando o servidor contém o checkout completo, aplicar a configuração validada:

```console
cd cloud
docker compose config --quiet
docker compose up -d --no-deps --force-recreate caddy
```

Quando existe somente a pasta cloud, preparar um diretório contendo `site/`,
`Caddyfile` e `publicar-site.sh`. O script mantém cópia da configuração anterior,
valida o Caddy, monta `./site` e recria somente o Caddy. Se a verificação falhar,
restaura a configuração anterior. Requer administração do Docker:

```console
bash publicar-site.sh /caminho/da/cloud /caminho/da/publicacao-preparada
```

Conferir `/`, `/painel/`, os arquivos CSS/JS e `/api/saude` depois de publicar.
Não há nova implantação apenas por alterar arquivos nesta cópia local.

## Arquivos

Validação sem navegador: `node scripts/testar-site.mjs`. Exercita dados vazios,
antigos, falha parcial, recuperação, ausência do mapa e consultas concorrentes.

| Arquivo | Responsabilidade |
|---|---|
| `index.html` | Conteúdo institucional |
| `painel/index.html` | Estrutura da tela de consulta |
| `aoVivo.js` | Consulta, estados, mapa e apresentação dos registros |
| `config.js` | Prefixo da API e intervalo de consulta |
| `estilo.css` | Identidade visual compartilhada e responsividade |
| `assets/atlas.png` | Identidade original da Atlas, reutilizada do projeto |

Não incluir segredos nesta pasta: ela é pública. O Caddy oculta arquivos Markdown
e não habilita listagem de diretórios.
