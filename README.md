# 🚀 AUTO-SIGA-API (UFJF)

Uma API não oficial, ultrarrápida e baseada em requisições HTTP diretas para automatizar a gestão de turmas, criação de avaliações, lançamento de notas e — a partir da versão 1.1 — **leitura do quadro de notas da turma** no SIGA da Universidade Federal de Juiz de Fora (UFJF).

Desenvolvido para eliminar o trabalho manual e repetitivo dos docentes, permitindo a integração do SIGA com ferramentas de automação (n8n, Make, Chatbots) ou painéis personalizados.

## ⚠️ Aviso Legal e Administrativo (Disclaimer)
**Este é um projeto open-source independente e não oficial.**
Não possui qualquer vínculo com o CGCO, com a administração da UFJF ou com a empresa desenvolvedora do SIGA. O código foi desenvolvido estritamente para fins de pesquisa e automação pessoal.
**Segurança:** A execução deste código e o tráfego de credenciais federais (CPF e Senha) são de inteira e exclusiva responsabilidade do utilizador que clonar e hospedar esta API. Nunca compartilhe a sua instância hospedada com terceiros para evitar infrações à LGPD ou bloqueios por parte do firewall da instituição.

---

## ✨ Principais Características
- **Velocidade Extrema:** Não utiliza navegadores virtuais (Playwright/Selenium). Toda a navegação é feita via `requests` e `BeautifulSoup`, reduzindo o tempo de operação de ~60 segundos para **1 a 2 segundos**.
- **Segurança de Acesso:** Protegido por uma chave API customizada (`X-API-Key`) exigida no cabeçalho (Header) das requisições.
- **Leitura do Quadro de Notas:** notas por avaliação, nota final, frequência e situação de cada aluno, em JSON pronto para planilha, dashboard ou banco de dados.
- **Sem ID fixo de professor:** o ID do docente é descoberto na própria sessão do SIGA, então a API funciona para qualquer professor que configurar as credenciais.

---

## 🛠️ Como Instalar e Rodar Localmente

### 1. Clonar o repositório e preparar o ambiente
```bash
git clone https://github.com/profmsouzaoficial/siga-api.git
cd siga-api
```

### 2. Configurar as Variáveis de Ambiente
Na raiz do projeto, faça uma cópia do arquivo de exemplo `.env.example` e renomeie-o para `.env`. Preencha com as suas credenciais reais:

```env
SIGA_CPF=12345678900
SIGA_SENHA=SuaSenhaSecreta
API_SECRET_KEY=SuaChaveDeAPI_ParaProtegerORoteador
```

*(Nota: o `.env` está no `.gitignore` e nunca deve ser enviado ao GitHub. O `python-dotenv` carrega esse arquivo automaticamente ao iniciar a API; variáveis já exportadas no ambiente têm prioridade.)*

### 3. Opção A: Rodar via Python (Uvicorn)
Requer [Python 3.10+](https://www.python.org/).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Opção B: Rodar via Docker (Recomendado)
```bash
docker build -t auto-siga-api .
docker run -p 8000:8000 --env-file .env auto-siga-api
```
O container já traz um `HEALTHCHECK` apontando para `/health`, útil no Coolify e em outros orquestradores.

### 4. Verificar se subiu
```bash
curl http://localhost:8000/health
# {"status":"ok","versao":"1.1.2"}
```

---

## ⚙️ Variáveis de Ambiente

| Variável | Obrigatória | Para que serve |
|---|---|---|
| `SIGA_CPF` | ✅ | CPF usado no login do SIGA. |
| `SIGA_SENHA` | ✅ | Senha do SIGA. |
| `API_SECRET_KEY` | ✅ | Chave que o cliente envia em `X-API-Key`. |
| `SIGA_PERIODO_PADRAO` | — | Período usado quando o cliente não envia `periodo` nas rotas de escrita. |
| `SIGA_TIMEOUT` | — | Timeout, em segundos, de cada requisição ao SIGA (padrão `30`). |
| `SIGA_ID_DOCENTE` | — | ID interno do docente, só necessário se o SIGA não enviar esse campo na página de filtro (normalmente é descoberto sozinho). |
| `SIGA_PERIODO_IDS` | — | Mapa manual de períodos, ex.: `2026/3=268,2026/2=267`. Útil se o SIGA mudar o HTML do filtro. |
| `SIGA_TIPO_CALCULO` | — | Tipo de cálculo ao criar TVC (padrão `Soma`). |
| `SIGA_DEBUG_DIR` | — | Salva em disco cada HTML baixado do SIGA (diagnóstico do parser). **Contém dados de alunos: não versionar.** |
| `API_CORS_ORIGINS` | — | Origens liberadas no CORS, separadas por vírgula. Vazio = CORS desligado. |
| `LOG_LEVEL` | — | Nível de log (`INFO` por padrão). |

---

## 📖 Como Usar (Endpoints)

Após iniciar o servidor, acesse a documentação interativa do Swagger:
👉 **`http://localhost:8000/docs`**

Para utilizar as rotas `/api/*`, autentique-se no botão **Authorize** usando a sua `API_SECRET_KEY`.

### 0. Rotas de infraestrutura (sem chave)
| Rota | Descrição |
|---|---|
| `GET /` | Identificação do serviço e versão. |
| `GET /health` | Verificação de saúde com a versão publicada (`{"status":"ok","versao":"1.1.2"}`), usada pelo Docker/Coolify. |

### 1. Quadro de Notas da Turma ⭐ (novo na 1.1)
`GET /api/notas?turma=CBV018GV-A&periodo=2026/3`
ou
`POST /api/notas` com corpo JSON:
```json
{
  "turma": "CBV018GV-A",
  "periodo": "2026/3"
}
```

> Dica: no `GET`, a barra do período precisa ser codificada (`periodo=2026%2F3`). No `POST`, basta enviar `"2026/3"`.

**Resposta (estrutura real, dados fictícios):**
```json
{
  "consulta": {
    "periodo": "2026/3",
    "id_periodo": "268",
    "turma": "CBV018GV-A",
    "id_turma": "12345",
    "fonte": "quadro_geral",
    "gerado_em": "2026-10-30T18:05:00+00:00",
    "requisicoes": 4
  },
  "avaliacoes": [
    {"id": "596505", "descricao": "Exercício Avaliativo", "peso": 10.0, "data": "25/09/2026"},
    {"id": "596506", "descricao": "Prova 1", "peso": 20.0, "data": "25/09/2026"}
  ],
  "colunas": ["Matrícula", "Nome", "Exercício Avaliativo", "Prova 1", "Nota Final", "Freq", "Situação", "Consolidar"],
  "alunos": [
    {
      "matricula": "202506007GV",
      "nome": "ALÉXIA KLARA ALVES DE SOUZA",
      "notas": {"Exercício Avaliativo": 10.0, "Prova 1": 16.0},
      "nota_final": null,
      "media": null,
      "frequencia": "FS",
      "situacao": "Matriculado"
    },
    {
      "matricula": "202506015GV",
      "nome": "FABIELLY SARAH ANDRADE FRANÇA",
      "notas": {"Exercício Avaliativo": 9.5, "Prova 1": 12.0},
      "nota_final": null,
      "media": null,
      "frequencia": "FS",
      "situacao": "Matriculado"
    }
  ],
  "resumo": {
    "total_alunos": 2,
    "com_nota_final": 0,
    "sem_nota_final": 2,
    "media_nota_final": null,
    "situacoes": {"Matriculado": 2}
  },
  "avisos": []
}
```

*O Quadro Geral do SIGA rotula as colunas como "Avaliação 1", "Avaliação 2"... A API lê a grade de TVCs em paralelo e, quando a quantidade de avaliações confere, substitui os rótulos genéricos pelas descrições reais (como no exemplo). Se não conferir, mantém os rótulos do SIGA e avisa em `avisos`.*

*Detalhes do SIGA real que a API preserva: a **matrícula pode ter sufixo de campus** (`202506007GV`); a coluna de peso é na verdade **"Peso/Valor"** (pontos da avaliação, podem passar de 10); `Freq` é um código do SIGA (ex.: `FS`); e a **Nota Final fica `null` até o professor consolidar** a turma.*

**Campos da resposta:**

| Campo | Significado |
|---|---|
| `consulta.fonte` | `quadro_geral` (leu o Quadro Geral do SIGA) ou `tvc_por_avaliacao` (montou o quadro percorrendo as TVCs). |
| `consulta.gerado_em` | Data/hora da consulta em ISO 8601 (UTC). |
| `avaliacoes` | Avaliações (TVCs) da turma: ID, descrição, `peso` (valor/pontos), data. |
| `colunas` | Ordem das colunas como estão no SIGA — útil para montar planilha/tabela. |
| `alunos[].notas` | Nota por avaliação, com o nome da avaliação como chave. `null` = sem nota lançada. |
| `alunos[].nota_final` | Nota final calculada pelo SIGA. `null` enquanto a turma não for consolidada. |
| `alunos[].media` | Média parcial mostrada pelo SIGA, quando existir. |
| `alunos[].frequencia` / `situacao` | Códigos/valores exatamente como o SIGA exibe (ex.: `FS`, `Matriculado`). |
| `resumo` | Totais e média apenas das notas finais **efetivamente retornadas**. |
| `avisos` | Lista de avisos (ex.: Quadro Geral indisponível, TVC sem pauta). A resposta continua válida. |

> **Nota importante:** a API **não inventa** nota final nem média ponderada. Quando o SIGA ainda não calculou a nota final, o campo vem `null` e o cálculo continua sendo responsabilidade do SIGA.

**Como a leitura funciona por dentro:**
1. Autentica no CAS (`auth.ufjf.br`) e mantém a sessão.
2. Traduz o período (`2026/3`) para o ID interno do SIGA (`268`).
3. Localiza a turma na grade do professor e obtém o ID interno da turma.
4. Abre o submenu **Quadro Geral** — a tela que o próprio SIGA descreve como "visão geral de todas as notas parciais e finais, além da frequência e situação dos alunos". No SIGA real esse submenu é um item Dojo (não é um `<a href>`): a API lê o handler `doLinkButton(...)` no JavaScript e descobre a URL `formNotasParciaisFind/{id_turma}`; se o menu mudar, ela usa essa URL diretamente como reserva.
5. Lê também a grade de **TVCs** (`formTVCFind/{id_turma}`) para preencher `avaliacoes` e trocar os rótulos genéricos ("Avaliação 1") pelas descrições reais, quando as quantidades conferem.
6. Se o Quadro Geral não existir ou não for reconhecido, monta o mesmo quadro percorrendo cada TVC e cada pauta (o mesmo HTML que a rota de lançamento já usava). O resultado vem com `fonte: "tvc_por_avaliacao"` e um aviso explicando o motivo.

### 2. Lançar Notas
`POST /api/lancar-notas`

```json
{
  "turma": "CBV018GV-A",
  "descricao_avaliacao": "TVC 1",
  "notas_json": {
    "202165001": 8.5,
    "202033002": "9,0"
  },
  "periodo": "2026/3"
}
```
*(As chaves de `notas_json` são as matrículas reais; os valores aceitam número ou texto com vírgula. `periodo` é opcional se `SIGA_PERIODO_PADRAO` estiver configurado.)*

A resposta agora informa **o que foi e o que não foi lançado**, em vez de dizer apenas "sucesso":
```json
{
  "status": "sucesso",
  "mensagem": "Notas lançadas na turma CBV018GV-A (avaliação 'TVC 1').",
  "id_turma": "12345",
  "avaliacao": "TVC 1",
  "id_avaliacao": "987",
  "lancados": [{"matricula": "202165001", "nome": "ANA BEATRIZ SOUZA", "nota": 8.5}],
  "nao_encontrados": ["202033002"],
  "notas_invalidas": []
}
```
Se **nenhuma** matrícula for encontrada na pauta, a API devolve erro em vez de fingir sucesso.

### 3. Criar Avaliação (TVC)
`POST /api/criar-avaliacao`

```json
{
  "turma": "CBV018GV-A",
  "num_tvc": 4,
  "peso": 10.0,
  "data": "30/10/2026",
  "descricao": "Trabalho Prático",
  "periodo": "2026/3"
}
```
A data é validada no formato `dd/mm/aaaa` e o peso deve ser maior que zero.

---

## 🧪 Testes

A suíte cobre os parsers (com HTMLs sintéticos que reproduzem a estrutura do SIGA) e as rotas HTTP, sem tocar no SIGA real:

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

---

## 🔎 Diagnóstico quando o SIGA muda

O SIGA é um sistema fechado e pode alterar o HTML sem aviso. Para investigar sem abrir o navegador:

```bash
python diagnostico.py CBV018GV-A 2026/3 --saida debug_html
```

O script faz login, imprime o JSON do quadro e salva **cada HTML recebido** em `debug_html/`. Se algum campo vier vazio ou `null`, esses arquivos mostram exatamente o que o SIGA devolveu. Alternativamente, defina `SIGA_DEBUG_DIR=debug_html` no `.env` para que a própria API salve os HTMLs durante as requisições.

> Os HTMLs contêm nomes, matrículas e notas de alunos. `debug_html/` já está no `.gitignore`; apague a pasta depois do diagnóstico.

---

## 🩺 Solução de Problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `403 Not authenticated` | Falta o cabeçalho `X-API-Key` | Envie a chave no header (ou use **Authorize** no `/docs`). |
| `503 API_SECRET_KEY não configurada` | `.env`/variável ausente no servidor | Configure `API_SECRET_KEY` e reinicie. |
| `500 Credenciais do SIGA ausentes` | Faltam `SIGA_CPF`/`SIGA_SENHA` | Configure as duas variáveis. |
| `502 O SIGA recusou as credenciais` | CPF/senha inválidos ou conta bloqueada | Confira as credenciais; se necessário, renove a senha no SIGA. |
| `404 Período '2026/3' não encontrado` | O período não está na lista do professor | Confira o período; se o HTML mudou, use `SIGA_PERIODO_IDS='2026/3=268'`. |
| `Mais de uma turma corresponde a ...` | Código incompleto (ex.: `CBV018GV` casa com `-A` e `-AB`) | Informe o código completo da turma. |
| `Quadro Geral não localizado` | Submenu mudou de nome/URL | A API usa o fallback por TVCs; se o resultado vier vazio, rode o `diagnostico.py` e ajuste o parser. |
| Timeout (`Tempo esgotado`) | SIGA lento ou indisponível | Aumente `SIGA_TIMEOUT` e tente novamente. |

---

## 🔐 Segurança e LGPD

- A API trafega credenciais federais; hospede-a em ambiente confiável e nunca a exponha sem `API_SECRET_KEY`.
- A chave é comparada em tempo constante (`secrets.compare_digest`), e erros internos **não** são devolvidos ao cliente (só ficam nos logs).
- A rota `/api/notas` devolve dados pessoais de alunos: trate a resposta como dado pessoal (LGPD), restrinja o acesso à chave e não a registre em logs de terceiros.
- O `.env` e a pasta `debug_html/` estão no `.gitignore`.

---

## 📝 O que mudou na versão 1.1.2

- `GET /health` passou a informar a versão publicada (`{"status":"ok","versao":"1.1.2"}`), o que permite confirmar em segundos se o deploy automático entrou no ar.

## 📝 O que mudou na versão 1.1.1

Correções encontradas ao testar contra o SIGA **real** (turma `CBV018GV-A`, período `2026/3`):

- **URLs sem `/index.php`**: as páginas acadêmicas retornavam 404. Corrigido em todas as rotas (passo 1, busca de turmas, turma, TVCs e salvamento).
- **Quadro Geral não era encontrado**: o submenu é um item Dojo (`MenuBarItem` + `doLinkButton`), não um `<a href>`. A API agora lê o handler em JavaScript e ainda tem a URL `formNotasParciaisFind/{id_turma}` como reserva.
- **Matrícula com sufixo de campus** (`202506007GV`) era rejeitada pelo filtro que só aceitava dígitos — a turma inteira era descartada. Corrigido, e o lançamento de notas agora aceita a matrícula com ou sem o sufixo.
- **Coluna "Avaliação 1" era confundida com coluna de ação** (o filtro testava a substring `"acao"`, que existe dentro de "avaliação") e as notas eram descartadas. A comparação passou a ser por palavra inteira.
- **Coluna `Freq`** não era reconhecida como frequência (o parser exigia "frequência"/"falta"); passou a aceitar `Freq` e a ignorar colunas de ação como `Consolidar`.
- **Rótulos genéricos**: as colunas de nota eram expostas como "Avaliação 1/2"; agora recebem as descrições reais das TVCs quando a contagem confere.
- Validado de ponta a ponta: **7 alunos, 2 avaliações, 1,5 s e 8 requisições**.

## 📝 O que mudou na versão 1.1.0

Correções feitas durante a revisão do projeto:

- **ID do docente fixo (`29230`) removido** — era um bloqueio para qualquer outro professor; agora o ID é lido da página do SIGA (ou de `SIGA_ID_DOCENTE`).
- **`.env` não estava no `.gitignore`**, embora o README dissesse que sim — risco de vazar CPF e senha no Git. Corrigido.
- **`python-dotenv` não estava nas dependências**, então o `.env` era ignorado no uso local. Adicionado.
- **Login validava sucesso com `"Siga" in html`**, o que também dava verdadeiro na própria tela de login (falso positivo). Agora a sessão é verificada de verdade e erro de credencial vira `502` com mensagem clara.
- **Nenhuma requisição tinha timeout** — uma instabilidade do SIGA travava o worker. Agora há timeout configurável e uma retentativa.
- **Traceback completo era devolvido ao cliente** em caso de erro. Agora o detalhe fica nos logs e o cliente recebe uma mensagem genérica.
- **HTML embrulhado em JSON do Dojo era tratado de forma inconsistente** entre os métodos (a busca de turmas podia falhar ao "mapear a tabela"). Unificado em um único extrator.
- **Busca por turma/TVC/matrícula usava substring**, o que podia casar `TVC 1` com `TVC 10` e lançar nota na avaliação errada. Agora o match exato tem prioridade e ambiguidades são recusadas.
- **Alunos não encontrados eram ignorados em silêncio** no lançamento de notas. Agora a resposta informa `nao_encontrados`/`notas_invalidas` e falha se nada casar.
- **Período fixo (`"2026/3"`) como padrão** foi substituído por `SIGA_PERIODO_PADRAO`, com erro explícito quando faltar.
- Adicionados `/health`, CORS opcional, logging, `LICENSE` (MIT), `.dockerignore`, `HEALTHCHECK`, testes e documentação da nova rota.

---

## 🤝 Contribuição
Contribuições são muito bem-vindas! Se você mapeou novas rotas do SIGA (como lançamento de frequência, retificação de notas ou geração de pautas em PDF), sinta-se à vontade para abrir um *Pull Request* ou reportar nas *Issues*.

## 📄 Licença
Este projeto está sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.
