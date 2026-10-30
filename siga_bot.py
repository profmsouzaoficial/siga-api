"""Cliente HTTP direto para o SIGA da UFJF.

Este módulo concentra toda a conversa com o SIGA (autenticação CAS em
auth.ufjf.br + páginas acadêmicas em siga.ufjf.br). Nada aqui depende de
navegador: são requisições ``requests`` com parsing por ``BeautifulSoup``.

Principais operações:

* :meth:`SigaAPI.login` — autentica e mantém os cookies de sessão.
* :meth:`SigaAPI.criar_avaliacao` — cria uma TVC na turma.
* :meth:`SigaAPI.lancar_notas` — grava as notas de uma TVC.
* :meth:`SigaAPI.obter_quadro_notas` — **leitura** do quadro de notas da turma
  (notas parciais, nota final, frequência e situação), objeto da rota
  ``/api/notas``.

A leitura do quadro tenta primeiro o submenu "Quadro Geral" da turma (que o
próprio SIGA descreve como "visão geral de todas as notas parciais e finais,
além da frequência e situação dos alunos") e, se ele não existir ou não for
reconhecido, monta o mesmo quadro percorrendo as TVCs uma a uma. Esse segundo
caminho reaproveita exatamente o HTML que já é consumido por ``lancar_notas``.
"""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://siga.ufjf.br"
AUTH_URL = "https://auth.ufjf.br"
URL_LOGIN = AUTH_URL + "/login?service=" + quote(BASE_URL + "/index.php", safe="")
URL_PAINEL = BASE_URL + "/index.php"
URL_PASSO1 = BASE_URL + "/siga/academico/nota/formNotaParcialPasso1/docente"
URL_BUSCA_TURMAS = BASE_URL + "/siga/academico/nota/formNotaParcialPasso2"
URL_TURMA = BASE_URL + "/siga/academico/nota/formNotaParcialObject/{}"
URL_TVCS = BASE_URL + "/siga/academico/nota/formTVCFind/{}"

LOGGER = logging.getLogger("siga_bot")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)

# Cabeçalho padrão do Dojo Toolkit, usado pelo SIGA em todas as chamadas AJAX.
CABECALHO_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "User-Agent": USER_AGENT,
}

PAYLOAD_BASE = {
    "__LAYOUT": "default",
    "__ELEMENT": "centerPane",
    "__TEMPLATE": "base",
    "__ISFILEUPLOAD": "no",
    "__ISAJAXEVENT": "no",
    "__EVENTTARGET": "btnPost",
    "ajaxResponseType": "json",
}

# Palavras-chave usadas para reconhecer colunas pelos títulos da tabela.
CHAVES_COLUNA = {
    "matricula": ("matric", "registro", "ra "),
    "nome": ("nome", "aluno", "discente"),
    "situacao": ("situac",),
    "frequencia": ("frequen", "falta", "presenc", "comparec"),
    "nota_final": ("nota final", "final", "conceito final", "resultado final"),
    "media": ("media", "media parcial"),
    "peso": ("peso",),
    "data": ("data", "aplicac"),
    "descricao": ("descri", "titulo", "avaliac"),
}

VAZIOS = {"", "-", "--", "---", "—", "n/a", "na", "nd", "none", "null"}
PADRAO_ERRO = re.compile(
    r"erro|invalid|incorret|falha|expirad|bloquead|nao foi possivel|"
    r"nao permit|nao autoriz|sem permissao"
)
PALAVRAS_STATUS = {
    "matriculado", "matriculada", "trancado", "trancada", "cancelado",
    "cancelada", "aprovado", "aprovada", "reprovado", "reprovada", "ativo",
    "ativa", "inativo", "inativa", "cursando", "concluido", "concluida",
    "dispensado", "dispensada", "total", "sim", "nao",
}
MATRICULA_RE = re.compile(r"^\d{6,12}$")
DATA_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
NUMERO_RE = re.compile(r"^-?\d+(?:[.,]\d+)?")


# ==========================================================================
# Erros
# ==========================================================================
class SigaError(Exception):
    """Erro genérico de comunicação/negócio com o SIGA."""


class SigaAuthError(SigaError):
    """Credenciais recusadas ou sessão expirada."""


class SigaNaoEncontrado(SigaError):
    """Um elemento esperado (turma, período, avaliação) não foi localizado."""


# ==========================================================================
# Utilitários de texto
# ==========================================================================
def normalizar(texto: Any) -> str:
    """Remove acentos, colapsa espaços e devolve ``str`` limpo.

    Usado **apenas para comparação** (nomes de menu, títulos de coluna). Os
    valores devolvidos pela API preservam a acentuação original.

    >>> normalizar("  Nota  Final\\n ")
    'Nota Final'
    >>> normalizar("Frequência")
    'Frequencia'
    """
    if texto is None:
        return ""
    decomposto = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento).strip()


def limpar_texto(texto: Any) -> str:
    """Colapsa espaços (inclusive ``&nbsp;``) preservando acentos.

    >>> limpar_texto("  José  da\\nSilva ")
    'José da Silva'
    """
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", str(texto).replace("\xa0", " ")).strip()


def chave_busca(texto: Any) -> str:
    """Versão minúscula de :func:`normalizar`, usada nas comparações."""
    return normalizar(texto).lower()


def parse_numero(valor: Any) -> Optional[float]:
    """Converte texto de nota do SIGA em ``float`` (ou ``None``).

    Aceita ``"8,5"``, ``"8.5"``, ``"10"``, ``"1.234,5"``; devolve ``None`` para
    células vazias, travessões e marcadores textuais.

    >>> parse_numero("8,5")
    8.5
    >>> parse_numero("-")
    >>> parse_numero("  ")
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = normalizar(valor)
    if chave_busca(texto) in VAZIOS:
        return None
    texto = texto.replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    achado = NUMERO_RE.match(texto)
    if not achado:
        return None
    try:
        return float(achado.group(0))
    except ValueError:  # pragma: no cover - defensivo
        return None


def _celulas(linha) -> List[str]:
    """Extrai o texto das células ``td``/``th`` de uma linha.

    A acentuação é preservada (nomes de alunos e títulos de coluna são expostos
    na resposta da API); as comparações usam :func:`chave_busca`.
    """
    return [
        limpar_texto(celula.get_text(" ", strip=True))
        for celula in linha.find_all(["td", "th"])
    ]


def _link_por_texto(escopo, alvo: str):
    """Devolve o primeiro ``<a>`` (ou ``<img title>``) cujo texto casa com *alvo*."""
    alvo = chave_busca(alvo)
    for link in escopo.find_all("a"):
        texto = chave_busca(link.get_text(" ", strip=True))
        if alvo and alvo in texto:
            return link
        for imagem in link.find_all("img"):
            titulo = chave_busca(imagem.get("title") or imagem.get("alt"))
            if alvo and alvo in titulo:
                return link
    return None


def _href_absoluto(href: str) -> str:
    """Converte href relativo do SIGA em URL absoluta."""
    if not href:
        return ""
    return urljoin(BASE_URL + "/", href.strip())


def _id_da_url(href: str) -> str:
    """Extrai o ID numérico final de um href do SIGA (ex.: ``.../123``)."""
    if not href:
        return ""
    caminho = re.split(r"[?#]", href.strip())[0].rstrip("/")
    return caminho.split("/")[-1]


def _resumo_linhas(linhas) -> str:
    """Resume algumas linhas de tabela para mensagens de erro."""
    return "; ".join(
        " | ".join(celula[:60] for celula in celulas[:4]) for _, celulas in linhas[:5]
    )


def _indice_por_chave(cabecalho: List[str], grupo: str) -> Optional[int]:
    """Localiza o índice da coluna cujo título contém alguma palavra do grupo."""
    chaves = CHAVES_COLUNA.get(grupo, ())
    for indice, titulo in enumerate(cabecalho):
        alvo = chave_busca(titulo)
        if any(chave in alvo for chave in chaves):
            return indice
    return None


# ==========================================================================
# Cliente do SIGA
# ==========================================================================
class SigaAPI:
    """Cliente de sessão para o SIGA da UFJF."""

    def __init__(self, timeout: Optional[int] = None):
        self.session = requests.Session()
        self.session.headers.update(CABECALHO_BASE)
        self.timeout = timeout or int(os.getenv("SIGA_TIMEOUT", "30"))
        self.logado = False
        self.urls_consultadas: List[str] = []
        self._periodos_cache: Dict[str, str] = {}
        self.debug_dir = os.getenv("SIGA_DEBUG_DIR")
        if self.debug_dir:
            os.makedirs(self.debug_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Camada HTTP
    # ------------------------------------------------------------------
    def _headers_ajax(self, referer: Optional[str] = None) -> Dict[str, str]:
        cabecalho = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }
        if referer:
            cabecalho["Referer"] = referer
        return cabecalho

    def _request(self, metodo: str, url: str, **kwargs) -> requests.Response:
        """Executa a requisição com timeout e uma retentativa em falhas de rede."""
        kwargs.setdefault("timeout", self.timeout)
        ultimo_erro: Optional[Exception] = None
        for tentativa in (1, 2):
            try:
                resposta = self.session.request(metodo, url, **kwargs)
                if resposta.status_code >= 500 and tentativa == 1:
                    ultimo_erro = SigaError(
                        f"SIGA respondeu {resposta.status_code} em {url} (tentativa {tentativa})."
                    )
                    time.sleep(0.5)
                    continue
                self.urls_consultadas.append(url)
                return resposta
            except requests.Timeout:
                ultimo_erro = SigaError(
                    f"Tempo esgotado ({self.timeout}s) ao acessar {url}."
                )
            except requests.RequestException as erro:
                ultimo_erro = SigaError(f"Falha de rede ao acessar {url}: {erro}")
            if tentativa == 1:
                time.sleep(0.5)
        raise ultimo_erro or SigaError(f"Falha desconhecida ao acessar {url}.")

    @staticmethod
    def _pagina_de_login(resposta: requests.Response) -> bool:
        """True quando a resposta é a tela de autenticação do CAS."""
        if AUTH_URL in (resposta.url or ""):
            return True
        html = resposta.text or ""
        if not html:
            return False
        formulario = BeautifulSoup(html, "html.parser").find("form")
        if not formulario:
            return False
        return bool(
            formulario.find("input", {"name": "password"})
            or formulario.find("input", {"type": "password"})
        )

    def _html_da_resposta(self, resposta: requests.Response) -> str:
        """Devolve o HTML de uma resposta, desembrulhando o JSON do Dojo.

        O SIGA responde às chamadas AJAX como ``{"data": "<html>..."}``. Quando
        a resposta é HTML puro, devolve o texto como veio.
        """
        texto = resposta.text or ""
        try:
            dados = resposta.json()
        except ValueError:
            return texto

        if isinstance(dados, dict):
            erro = dados.get("error") or dados.get("erro")
            if erro and not dados.get("data"):
                raise SigaError(f"SIGA retornou erro: {erro}")
            for chave in ("data", "html", "content"):
                valor = dados.get(chave)
                if isinstance(valor, str) and valor.strip():
                    return valor
        elif isinstance(dados, list):
            for item in dados:
                if isinstance(item, dict) and isinstance(item.get("data"), str):
                    return item["data"]
        return texto

    def _salvar_debug(self, nome: str, conteudo: str) -> None:
        """Grava a resposta em disco quando ``SIGA_DEBUG_DIR`` está definido."""
        if not self.debug_dir:
            return
        seguro = re.sub(r"[^A-Za-z0-9_.-]+", "_", nome)[:80]
        caminho = os.path.join(self.debug_dir, f"{seguro}.html")
        try:
            with open(caminho, "w", encoding="utf-8") as arquivo:
                arquivo.write(conteudo)
            LOGGER.debug("HTML salvo para depuração em %s", caminho)
        except OSError as erro:  # pragma: no cover - defensivo
            LOGGER.warning("Não foi possível salvar %s: %s", caminho, erro)

    def _get_html(self, url: str, referer: Optional[str] = None) -> str:
        """GET autenticado que devolve HTML já desembrulhado."""
        LOGGER.debug("GET %s", url)
        resposta = self._request("GET", url, headers=self._headers_ajax(referer))
        if self._pagina_de_login(resposta):
            self.logado = False
            raise SigaAuthError(
                "A sessão do SIGA expirou ou o login não foi concluído. "
                "Tente novamente; se persistir, confira SIGA_CPF/SIGA_SENHA."
            )
        if resposta.status_code >= 400:
            raise SigaError(f"SIGA respondeu {resposta.status_code} para {url}.")
        html = self._html_da_resposta(resposta)
        self._salvar_debug(url.replace(BASE_URL, "").replace("/", "_"), html)
        return html

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------
    def login(self, cpf: str, senha: str) -> None:
        """Autentica no CAS da UFJF e mantém os cookies na sessão."""
        LOGGER.info("Iniciando login no SIGA...")
        self.logado = False

        pagina = self._request("GET", URL_LOGIN)
        soup = BeautifulSoup(pagina.text or "", "html.parser")
        formulario = soup.find("form")
        if not formulario:
            raise SigaError(
                "Formulário de autenticação não encontrado em auth.ufjf.br. "
                "Resposta recebida: " + (pagina.text or "")[:300]
            )

        # Campos ocultos do CAS (execution, _eventId, lt, ...).
        dados = {}
        for campo in formulario.find_all("input"):
            nome = campo.get("name")
            if nome:
                dados[nome] = campo.get("value", "")
        dados["username"] = cpf
        dados["password"] = senha

        acao = formulario.get("action")
        url_post = urljoin(pagina.url, acao) if acao else pagina.url
        resposta = self._request("POST", url_post, data=dados)

        painel = self._request("GET", URL_PAINEL)
        if self._pagina_de_login(painel):
            trecho = chave_busca(painel.text or "")
            if re.search(r"invali|incorret|nao encontrad|bloquead|expirad", trecho):
                raise SigaAuthError(
                    "O SIGA recusou as credenciais (CPF ou senha inválidos). "
                    "Confira SIGA_CPF/SIGA_SENHA."
                )
            raise SigaAuthError(
                "Login não foi concluído: o SIGA devolveu a tela de autenticação."
            )
        if not (painel.ok or resposta.ok):
            raise SigaError(
                f"Falha no login (auth={resposta.status_code}, painel={painel.status_code})."
            )

        self.logado = True
        LOGGER.info("Login concluído com sucesso.")

    def _exigir_login(self) -> None:
        if not self.logado:
            raise SigaAuthError("É necessário fazer login no SIGA antes desta operação.")

    # ------------------------------------------------------------------
    # Descoberta de período, docente e turma
    # ------------------------------------------------------------------
    def _resolver_periodo(self, soup: BeautifulSoup, periodo_texto: str) -> str:
        """Traduz "2026/3" no ID interno do SIGA (ex.: "268")."""
        alvo = chave_busca(periodo_texto)
        if alvo.isdigit():
            return alvo

        if alvo in self._periodos_cache:
            return self._periodos_cache[alvo]

        # Escape hatch operacional: permite fixar o mapa sem depender do HTML.
        mapa_env = os.getenv("SIGA_PERIODO_IDS", "")
        for par in mapa_env.split(","):
            if "=" not in par:
                continue
            texto, valor = (parte.strip() for parte in par.split("=", 1))
            if chave_busca(texto) == alvo and valor:
                self._periodos_cache[alvo] = valor
                return valor

        opcoes: List[Tuple[str, str]] = []
        for select in soup.find_all("select"):
            for opcao in select.find_all("option"):
                valor = (opcao.get("value") or "").strip()
                texto = normalizar(opcao.get_text(" ", strip=True))
                if valor and valor not in {"-1", "-2", "0"}:
                    opcoes.append((valor, texto))

        for comparador in (
            lambda valor, texto: chave_busca(texto) == alvo,
            lambda valor, texto: chave_busca(texto).startswith(alvo),
            lambda valor, texto: alvo in chave_busca(texto),
        ):
            for valor, texto in opcoes:
                if comparador(valor, texto):
                    self._periodos_cache[alvo] = valor
                    LOGGER.info("Período %r traduzido para o ID %s.", periodo_texto, valor)
                    return valor

        disponiveis = ", ".join(sorted({texto for _, texto in opcoes})[:15])
        raise SigaNaoEncontrado(
            f"Período '{periodo_texto}' não encontrado no SIGA."
            + (f" Opções disponíveis: {disponiveis}." if disponiveis else "")
            + " Use SIGA_PERIODO_IDS='2026/3=268' para informar o ID manualmente."
        )

    @staticmethod
    def _descobrir_id_docente(soup: BeautifulSoup) -> Optional[str]:
        """Descobre o ID do docente logado na página de filtro (sem hardcode).

        O SIGA envia ``filter::idDocente`` junto com o filtro de período. O valor
        é do professor autenticado — por isso não pode ficar fixo no código.
        """
        campo = soup.find("input", {"name": re.compile(r"idDocente", re.I)})
        if campo and (campo.get("value") or "").strip():
            return campo.get("value").strip()

        select = soup.find("select", {"name": re.compile(r"idDocente", re.I)})
        if select:
            marcado = select.find("option", selected=True)
            if marcado and (marcado.get("value") or "").strip():
                return marcado.get("value").strip()
            opcoes = [o for o in select.find_all("option") if (o.get("value") or "").strip()]
            if len(opcoes) == 1:
                return opcoes[0].get("value").strip()

        return (os.getenv("SIGA_ID_DOCENTE") or "").strip() or None

    def _localizar_turma(self, turma_codigo: str, periodo_texto: str) -> Dict[str, str]:
        """Resolve o ID interno da turma a partir do código e do período."""
        html_passo1 = self._get_html(URL_PASSO1)
        soup_passo1 = BeautifulSoup(html_passo1, "html.parser")

        id_periodo = self._resolver_periodo(soup_passo1, periodo_texto)

        dados = dict(PAYLOAD_BASE)
        dados["filter::idPeriodoLetivo"] = id_periodo
        id_docente = self._descobrir_id_docente(soup_passo1)
        if id_docente:
            dados["filter::idDocente"] = id_docente
            LOGGER.info("ID do docente usado no filtro: %s", id_docente)
        else:
            LOGGER.warning("ID do docente não identificado; o SIGA usará o usuário logado.")

        resposta = self._request(
            "POST",
            URL_BUSCA_TURMAS,
            data=dados,
            headers=self._headers_ajax(URL_PASSO1),
        )
        if self._pagina_de_login(resposta):
            self.logado = False
            raise SigaAuthError(
                "A sessão do SIGA expirou antes da listagem de turmas. "
                "Tente novamente."
            )
        if resposta.status_code >= 400:
            raise SigaError(
                f"Falha ao listar turmas do período {periodo_texto} "
                f"({resposta.status_code})."
            )
        html_turmas = self._html_da_resposta(resposta)
        self._salvar_debug("busca_turmas_" + str(id_periodo), html_turmas)

        soup = BeautifulSoup(html_turmas, "html.parser")
        linha, celulas = self._localizar_linha(soup, turma_codigo, rotulo="turma")

        link = linha.find("a", href=re.compile(r"formNotaParcialObject", re.I))
        if not link:
            link = _link_por_texto(linha, "editar")
        if not link or not link.get("href"):
            raise SigaNaoEncontrado(
                f"Turma {turma_codigo} localizada, mas o link de edição da pauta "
                "não foi encontrado na tabela."
            )

        id_turma = _id_da_url(link["href"])
        if not id_turma.isdigit():
            raise SigaError(
                f"Não foi possível extrair o ID da turma a partir de '{link['href']}'."
            )

        LOGGER.info("Turma %s -> ID interno %s", turma_codigo, id_turma)
        return {
            "codigo": turma_codigo,
            "id_turma": id_turma,
            "id_periodo": id_periodo,
            "celulas": celulas,
        }

    def _obter_id_turma(self, turma_codigo: str, periodo_texto: str) -> str:
        """Compatibilidade: devolve apenas o ID interno da turma."""
        return self._localizar_turma(turma_codigo, periodo_texto)["id_turma"]

    @staticmethod
    def _localizar_linha(
        soup: BeautifulSoup, texto: str, rotulo: str = "registro"
    ) -> Tuple[Any, List[str]]:
        """Encontra a linha da tabela cuja célula casa com *texto*.

        A comparação é exata primeiro (evita confundir ``CBV018GV-A`` com
        ``CBV018GV-AB``, ou ``TVC 1`` com ``TVC 10``) e só depois por
        substring. Caso mais de uma linha case exatamente, devolve erro de
        ambiguidade em vez de escolher a turma errada.
        """
        alvo = chave_busca(texto)
        if not alvo:
            raise SigaNaoEncontrado(f"Texto de {rotulo} vazio.")

        exatas: List[Tuple[Any, List[str]]] = []
        parciais: List[Tuple[Any, List[str]]] = []
        for linha in soup.find_all("tr"):
            celulas = _celulas(linha)
            if not celulas:
                continue
            chaves = [chave_busca(celula) for celula in celulas]
            if alvo in chaves:
                exatas.append((linha, celulas))
            elif any(alvo in chave for chave in chaves):
                parciais.append((linha, celulas))

        if len(exatas) == 1:
            return exatas[0]
        if len(exatas) > 1:
            exemplos = _resumo_linhas(exatas)
            raise SigaError(f"Mais de uma {rotulo} corresponde a '{texto}': {exemplos}")
        if len(parciais) == 1:
            return parciais[0]
        if len(parciais) > 1:
            exemplos = _resumo_linhas(parciais)
            raise SigaError(
                f"'{texto}' é ambíguo para {rotulo}; candidatas: {exemplos}. "
                "Informe o código completo."
            )
        raise SigaNaoEncontrado(
            f"{rotulo.capitalize()} '{texto}' não encontrada(o) na resposta do SIGA "
            f"para o período solicitado."
        )

    # ------------------------------------------------------------------
    # Criação de avaliação
    # ------------------------------------------------------------------
    def criar_avaliacao(
        self,
        turma: str,
        num_tvc: int,
        peso: float,
        data: str,
        descricao: str,
        periodo: str = "268",
    ) -> Dict[str, Any]:
        """Cria uma TVC (avaliação) na turma informada."""
        self._exigir_login()

        info = self._localizar_turma(turma, periodo)
        id_turma = info["id_turma"]

        url_alvo = BASE_URL + "/siga/academico/nota/TVCSave/?go=formTVCFind"
        referer = URL_TURMA.format(id_turma) + "?filter::idDepartamento="

        dados = dict(PAYLOAD_BASE)
        dados.update(
            {
                "__ELEMENT": "dialog",
                "__EVENTTARGET": "btnPostTvc",
                "turma::tipoCalculo": os.getenv("SIGA_TIPO_CALCULO", "Soma"),
                "tvc::numTVC": str(num_tvc),
                "tvc::peso": str(peso),
                "tvc::dataAplicacao": data,
                "tvc::descricao": descricao,
                "tvc::idTVC": "",
                "tvc::idTurma": id_turma,
            }
        )

        resposta = self._request(
            "POST", url_alvo, data=dados, headers=self._headers_ajax(referer)
        )
        corpo = self._html_da_resposta(resposta)
        self._salvar_debug("criar_avaliacao", corpo)

        if resposta.status_code >= 400:
            raise SigaError(
                f"Erro ao criar a avaliação: HTTP {resposta.status_code}."
            )
        erro = self._detectar_erro(corpo)
        if erro:
            raise SigaError(f"O SIGA recusou a criação da avaliação: {erro}")

        LOGGER.info("Avaliação %r criada na turma %s.", descricao, turma)
        return {"id_turma": id_turma, "descricao": descricao}

    # ------------------------------------------------------------------
    # Lançamento de notas
    # ------------------------------------------------------------------
    def lancar_notas(
        self,
        turma: str,
        descricao_avaliacao: str,
        notas_json: Dict[str, Any],
        periodo_texto: str = "2026/3",
    ) -> Dict[str, Any]:
        """Grava as notas de uma TVC e devolve o que foi (ou não) lançado."""
        self._exigir_login()
        if not notas_json:
            raise SigaError("Nenhuma nota foi informada em 'notas_json'.")

        info = self._localizar_turma(turma, periodo_texto)
        id_turma = info["id_turma"]
        url_turma = URL_TURMA.format(id_turma)
        url_tvcs = URL_TVCS.format(id_turma)

        html_tvcs = self._get_html(url_tvcs, referer=url_turma)
        avaliacoes = self._parse_avaliacoes(html_tvcs)
        avaliacao = self._escolher_avaliacao(avaliacoes, descricao_avaliacao)

        html_pauta = self._get_html(avaliacao["url"], referer=url_tvcs)
        soup_pauta = BeautifulSoup(html_pauta, "html.parser")

        # Payload base + campos ocultos da pauta (tvc::*) e notas já existentes.
        dados = dict(PAYLOAD_BASE)
        dados.update(
            {
                "tvc::idTVC": avaliacao["id"],
                "tvc::idTurma": id_turma,
                "stats::media": "0",
                "stats::desviopadrao": "0",
                "gridFind_PAGING": "no",
                "gridFind_PAGE": "1",
                "gridFind_GOPAGE": "1",
                "gridFind__VIEWSTATE": "",
            }
        )

        alunos_por_matricula: Dict[str, Dict[str, str]] = {}
        cabecalho_pauta = self._cabecalho_com_matriculas(soup_pauta)
        indice_matricula = _indice_por_chave(cabecalho_pauta, "matricula")
        indice_nome = _indice_por_chave(cabecalho_pauta, "nome")
        for campo in soup_pauta.find_all("input"):
            nome = campo.get("name")
            if not nome:
                continue
            if nome.startswith("tvc::") and nome not in dados:
                dados[nome] = campo.get("value", "")
            elif re.match(r"notas\[\d+\]", nome):
                dados[nome] = campo.get("value", "")
                linha = campo.find_parent("tr")
                if linha:
                    celulas_linha = _celulas(linha)
                    matricula = self._valor_coluna(
                        celulas_linha, indice_matricula
                    ) or self._matricula_da_linha(celulas_linha)
                    if matricula:
                        alunos_por_matricula[matricula] = {
                            "campo": nome,
                            "nome": self._valor_coluna(celulas_linha, indice_nome)
                            or self._nome_da_linha(celulas_linha),
                        }

        if not alunos_por_matricula:
            raise SigaError(
                "Não foi possível identificar os alunos na pauta desta avaliação. "
                "O HTML do SIGA pode ter mudado."
            )

        lancados: List[Dict[str, Any]] = []
        nao_encontrados: List[Any] = []
        invalidos: List[Dict[str, Any]] = []

        for matricula, nota in notas_json.items():
            chave = str(matricula).strip()
            aluno = alunos_por_matricula.get(chave)
            if aluno is None:
                # Tolerância para matrícula enviada como número (ex.: 202165001.0).
                sufixo = chave.split(".")[0]
                aluno = alunos_por_matricula.get(sufixo)
            if aluno is None:
                nao_encontrados.append(matricula)
                continue

            valor = parse_numero(nota)
            if valor is None:
                invalidos.append({"matricula": chave, "nota": nota})
                continue

            dados[aluno["campo"]] = f"{valor:g}"
            lancados.append(
                {"matricula": chave, "nome": aluno["nome"], "nota": valor}
            )
            LOGGER.debug("Matrícula %s -> campo %s = %s", chave, aluno["campo"], valor)

        if not lancados:
            raise SigaNaoEncontrado(
                "Nenhuma matrícula informada foi encontrada na pauta da avaliação "
                f"'{descricao_avaliacao}'."
            )

        resposta = self._request(
            "POST",
            BASE_URL + "/siga/academico/nota/saveNotasParciais",
            data=dados,
            headers=self._headers_ajax(avaliacao["url"]),
        )
        corpo = self._html_da_resposta(resposta)
        self._salvar_debug("lancar_notas", corpo)

        if resposta.status_code >= 400:
            raise SigaError(f"Erro ao salvar notas: HTTP {resposta.status_code}.")
        erro = self._detectar_erro(corpo)
        if erro:
            raise SigaError(f"O SIGA recusou o lançamento das notas: {erro}")

        LOGGER.info(
            "Notas lançadas em %r: %d aluno(s).", descricao_avaliacao, len(lancados)
        )
        return {
            "id_turma": id_turma,
            "avaliacao": avaliacao["descricao"],
            "id_avaliacao": avaliacao["id"],
            "lancados": lancados,
            "nao_encontrados": nao_encontrados,
            "notas_invalidas": invalidos,
        }

    @staticmethod
    def _detectar_erro(html: str) -> Optional[str]:
        """Procura mensagens de erro conhecidas no retorno de uma ação do SIGA."""
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        for seletor in (".error", ".erro", ".alert", ".mensagemErro", ".msgErro"):
            for bloco in soup.select(seletor):
                texto = normalizar(bloco.get_text(" ", strip=True))
                if texto and PADRAO_ERRO.search(chave_busca(texto)):
                    return texto[:300]
        return None

    # ------------------------------------------------------------------
    # Leitura do quadro de notas
    # ------------------------------------------------------------------
    def obter_quadro_notas(
        self, turma: str, periodo: str
    ) -> Dict[str, Any]:
        """Devolve o quadro de notas da turma em formato JSON organizado.

        Estrutura de saída::

            {
              "consulta": {...},
              "avaliacoes": [{"id": .., "descricao": .., "peso": .., "data": ..}],
              "colunas": ["Matricula", "Nome", "TVC 1", ...],
              "alunos": [
                {"matricula": .., "nome": .., "notas": {"TVC 1": 8.5},
                 "nota_final": .., "frequencia": .., "situacao": ..}
              ],
              "resumo": {...},
              "avisos": [...]
            }
        """
        self._exigir_login()
        inicio = time.time()
        avisos: List[str] = []

        info = self._localizar_turma(turma, periodo)
        id_turma = info["id_turma"]
        url_turma = URL_TURMA.format(id_turma)

        avaliacoes: List[Dict[str, Any]] = []
        alunos: List[Dict[str, Any]] = []
        colunas: List[str] = []
        fonte = None

        html_turma = self._get_html(url_turma, referer=URL_PASSO1)
        url_quadro = self._localizar_link_quadro_geral(html_turma)

        if url_quadro:
            try:
                html_quadro = self._get_html(url_quadro, referer=url_turma)
                quadro = self._parse_quadro_geral(html_quadro)
                if quadro["alunos"]:
                    alunos = quadro["alunos"]
                    colunas = quadro["colunas"]
                    fonte = "quadro_geral"
                else:
                    avisos.append(
                        "Quadro Geral encontrado, mas nenhuma tabela de notas foi "
                        "reconhecida; o quadro foi montado a partir das TVCs."
                    )
            except SigaError as erro:
                avisos.append(f"Falha ao ler o Quadro Geral: {erro}")
        else:
            avisos.append(
                "Submenu 'Quadro Geral' não localizado na página da turma; "
                "o quadro foi montado a partir das TVCs."
            )

        if fonte is None:
            alunos, avaliacoes, colunas = self._quadro_por_tvc(
                id_turma, url_turma, avisos
            )
            if alunos:
                fonte = "tvc_por_avaliacao"
            elif not avaliacoes:
                avisos.append(
                    "Nenhuma TVC cadastrada ou reconhecida para esta turma."
                )

        # A ordem dos alunos segue a ordem do próprio SIGA (a mesma que o
        # professor vê na tela), em vez de ser reordenada pelo cliente.
        resumo = self._resumir(alunos)

        LOGGER.info(
            "Quadro da turma %s lido via %s em %.1fs (%d aluno(s)).",
            turma,
            fonte,
            time.time() - inicio,
            len(alunos),
        )

        return {
            "consulta": {
                "periodo": periodo,
                "id_periodo": info["id_periodo"],
                "turma": turma,
                "id_turma": id_turma,
                "fonte": fonte,
                "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "requisicoes": len(self.urls_consultadas),
            },
            "avaliacoes": avaliacoes,
            "colunas": colunas,
            "alunos": alunos,
            "resumo": resumo,
            "avisos": avisos,
        }

    @staticmethod
    def _localizar_link_quadro_geral(html_turma: str) -> Optional[str]:
        """Encontra o href do submenu "Quadro Geral" da página da turma."""
        soup = BeautifulSoup(html_turma, "html.parser")
        for link in soup.find_all("a"):
            texto = chave_busca(link.get_text(" ", strip=True))
            titulo = chave_busca(link.get("title") or "")
            if "quadro geral" in texto or "quadro geral" in titulo:
                href = link.get("href") or ""
                if href and not href.lower().startswith("javascript"):
                    return _href_absoluto(href)
        return None

    def _quadro_por_tvc(
        self, id_turma: str, url_turma: str, avisos: List[str]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
        """Monta o quadro percorrendo as TVCs e as respectivas pautas."""
        url_tvcs = URL_TVCS.format(id_turma)
        try:
            html_tvcs = self._get_html(url_tvcs, referer=url_turma)
        except SigaError as erro:
            avisos.append(f"Não foi possível listar as TVCs: {erro}")
            return [], [], []

        avaliacoes = self._parse_avaliacoes(html_tvcs)
        if not avaliacoes:
            return [], [], []

        alunos: Dict[str, Dict[str, Any]] = {}
        rotulos: List[str] = []

        for indice, avaliacao in enumerate(avaliacoes, start=1):
            rotulo = avaliacao["descricao"] or f"Avaliação {indice}"
            while rotulo in rotulos:
                rotulo = f"{rotulo} ({indice})"
            rotulos.append(rotulo)
            avaliacao["rotulo"] = rotulo

            if not avaliacao["url"]:
                avisos.append(f"TVC '{rotulo}' sem link de pauta; ignorada.")
                continue

            try:
                html_pauta = self._get_html(avaliacao["url"], referer=url_tvcs)
            except SigaError as erro:
                avisos.append(f"Falha ao ler a pauta de '{rotulo}': {erro}")
                continue

            for registro in self._parse_pauta(html_pauta):
                matricula = registro["matricula"]
                chave = matricula or f"sem-matricula:{registro['nome']}"
                aluno = alunos.setdefault(
                    chave,
                    {
                        "matricula": matricula,
                        "nome": registro["nome"],
                        "notas": {},
                        "nota_final": None,
                        "media": None,
                        "frequencia": None,
                        "situacao": None,
                    },
                )
                if not aluno["nome"] and registro["nome"]:
                    aluno["nome"] = registro["nome"]
                aluno["notas"][rotulo] = registro["nota"]

        for avaliacao in avaliacoes:
            avaliacao.pop("url", None)

        # Garante uma coluna para cada TVC em todos os alunos (None = sem nota),
        # na mesma ordem de "colunas".
        for aluno in alunos.values():
            aluno["notas"] = {rotulo: aluno["notas"].get(rotulo) for rotulo in rotulos}

        return list(alunos.values()), avaliacoes, rotulos

    def _parse_avaliacoes(self, html: str) -> List[Dict[str, Any]]:
        """Interpreta a grade de TVCs da turma."""
        soup = BeautifulSoup(html, "html.parser")
        cabecalho = self._cabecalho_da_tabela_com_link(soup, ("lançar", "lancar"))
        indice_descricao = _indice_por_chave(cabecalho, "descricao")
        indice_peso = _indice_por_chave(cabecalho, "peso")
        indice_data = _indice_por_chave(cabecalho, "data")

        avaliacoes: List[Dict[str, Any]] = []
        vistos = set()
        for linha in soup.find_all("tr"):
            link = _link_por_texto(linha, "lançar") or _link_por_texto(linha, "lancar")
            if not link or not link.get("href"):
                continue
            href = link["href"]
            id_tvc = _id_da_url(href)
            if not id_tvc.isdigit() or id_tvc in vistos:
                continue
            vistos.add(id_tvc)

            celulas = _celulas(linha)
            descricao = self._valor_coluna(celulas, indice_descricao)
            if not descricao:
                descricao = self._adivinhar_descricao(celulas)
            peso = parse_numero(self._valor_coluna(celulas, indice_peso))
            if peso is None:
                peso = self._adivinhar_peso(celulas)
            data = self._valor_coluna(celulas, indice_data) or self._adivinhar_data(celulas)

            avaliacoes.append(
                {
                    "id": id_tvc,
                    "descricao": descricao or f"TVC {id_tvc}",
                    "peso": peso,
                    "data": data,
                    "url": _href_absoluto(href),
                }
            )
        return avaliacoes

    @staticmethod
    def _escolher_avaliacao(
        avaliacoes: List[Dict[str, Any]], descricao: str
    ) -> Dict[str, Any]:
        """Seleciona a TVC pelo nome, com match exato antes do parcial."""
        alvo = chave_busca(descricao)
        if not alvo:
            raise SigaError("Informe 'descricao_avaliacao'.")
        exatas = [a for a in avaliacoes if chave_busca(a["descricao"]) == alvo]
        if len(exatas) == 1:
            return exatas[0]
        if len(exatas) > 1:
            raise SigaError(f"Mais de uma avaliação chamada '{descricao}'.")
        parciais = [a for a in avaliacoes if alvo in chave_busca(a["descricao"])]
        if len(parciais) == 1:
            return parciais[0]
        if len(parciais) > 1:
            nomes = ", ".join(a["descricao"] for a in parciais)
            raise SigaError(
                f"'{descricao}' é ambíguo; avaliações encontradas: {nomes}."
            )
        nomes = ", ".join(a["descricao"] for a in avaliacoes) or "nenhuma"
        raise SigaNaoEncontrado(
            f"Avaliação '{descricao}' não encontrada. Disponíveis: {nomes}."
        )

    def _parse_pauta(self, html: str) -> List[Dict[str, Any]]:
        """Lê a pauta de uma TVC: matrícula, nome e nota de cada aluno."""
        soup = BeautifulSoup(html, "html.parser")
        registros: List[Dict[str, Any]] = []

        cabecalho_pauta = self._cabecalho_com_matriculas(soup)
        indice_matricula = _indice_por_chave(cabecalho_pauta, "matricula")
        indice_nome = _indice_por_chave(cabecalho_pauta, "nome")

        # Caminho principal: inputs "notas[N]" (o mesmo usado por lancar_notas).
        for campo in soup.find_all("input"):
            nome_campo = campo.get("name") or ""
            if not re.match(r"notas\[\d+\]", nome_campo):
                continue
            linha = campo.find_parent("tr")
            if not linha:
                continue
            celulas = _celulas(linha)
            matricula = self._valor_coluna(
                celulas, indice_matricula
            ) or self._matricula_da_linha(celulas)
            nome = self._valor_coluna(celulas, indice_nome) or self._nome_da_linha(celulas)
            registros.append(
                {
                    "matricula": matricula,
                    "nome": nome,
                    "nota": parse_numero(campo.get("value")),
                }
            )

        if registros:
            return registros

        # Alternativa: pauta somente leitura, com as notas em células de texto.
        cabecalho = cabecalho_pauta
        indice_nota = None
        for indice, titulo in enumerate(cabecalho):
            alvo = chave_busca(titulo)
            if "nota" in alvo or "tvc" in alvo:
                indice_nota = indice
                break

        for linha in soup.find_all("tr"):
            celulas = _celulas(linha)
            if not celulas:
                continue
            matricula = (
                self._valor_coluna(celulas, indice_matricula)
                or self._matricula_da_linha(celulas)
            )
            if not matricula:
                continue
            nome = self._valor_coluna(celulas, indice_nome) or self._nome_da_linha(celulas)
            nota = None
            if indice_nota is not None and indice_nota < len(celulas):
                nota = parse_numero(celulas[indice_nota])
            elif len(celulas) >= 3:
                nota = parse_numero(celulas[-1])
            registros.append({"matricula": matricula, "nome": nome, "nota": nota})
        return registros

    def _parse_quadro_geral(self, html: str) -> Dict[str, Any]:
        """Interpreta a tabela do Quadro Geral (notas, frequência, situação)."""
        soup = BeautifulSoup(html, "html.parser")
        melhor = None
        for tabela in soup.find_all("table"):
            linhas = tabela.find_all("tr")
            if len(linhas) < 2:
                continue
            cabecalho = self._cabecalho_da_tabela(linhas)
            if not cabecalho:
                continue

            tem_matricula = _indice_por_chave(cabecalho, "matricula") is not None
            tem_nome = _indice_por_chave(cabecalho, "nome") is not None
            titulos_de_nota = sum(
                1
                for titulo in cabecalho
                if any(
                    chave in chave_busca(titulo)
                    for chave in ("nota", "tvc", "media", "final", "avaliac")
                )
            )
            pontuacao = len(cabecalho) + 10 * tem_matricula + 5 * tem_nome
            pontuacao += 3 * titulos_de_nota
            # Tabela de notas tem muitas linhas de aluno; menus têm poucas.
            pontuacao += min(len(linhas), 30) // 3
            if melhor is None or pontuacao > melhor[0]:
                melhor = (pontuacao, tabela, cabecalho, linhas)

        if melhor is None:
            return {"alunos": [], "colunas": []}

        _, tabela, cabecalho, linhas = melhor
        indice_matricula = _indice_por_chave(cabecalho, "matricula")
        indice_nome = _indice_por_chave(cabecalho, "nome")
        indice_situacao = _indice_por_chave(cabecalho, "situacao")
        indice_frequencia = _indice_por_chave(cabecalho, "frequencia")
        indice_final = _indice_por_chave(cabecalho, "nota_final")
        indice_media = _indice_por_chave(cabecalho, "media")

        conhecidas = {
            i
            for i in (
                indice_matricula,
                indice_nome,
                indice_situacao,
                indice_frequencia,
                indice_final,
                indice_media,
            )
            if i is not None
        }
        # Colunas de frequência/faltas não são notas, mesmo que o título não
        # tenha casado com os grupos conhecidos.
        for i, titulo in enumerate(cabecalho):
            alvo = chave_busca(titulo)
            if any(chave in alvo for chave in ("falt", "frequen", "presenc", "comparec")):
                conhecidas.add(i)

        colunas_notas = [
            (i, cabecalho[i])
            for i in range(len(cabecalho))
            if i not in conhecidas and cabecalho[i] and len(cabecalho[i]) <= 60
        ]

        alunos: List[Dict[str, Any]] = []
        for linha in linhas:
            celulas = _celulas(linha)
            if not celulas:
                continue
            matricula = self._valor_coluna(celulas, indice_matricula)
            if not matricula:
                matricula = self._matricula_da_linha(celulas)
            if not matricula or not MATRICULA_RE.match(matricula):
                continue

            notas: Dict[str, Optional[float]] = {}
            for indice, titulo in colunas_notas:
                if indice < len(celulas):
                    notas[titulo] = parse_numero(celulas[indice])

            alunos.append(
                {
                    "matricula": matricula,
                    "nome": self._valor_coluna(celulas, indice_nome)
                    or self._nome_da_linha(celulas),
                    "notas": notas,
                    "nota_final": parse_numero(self._valor_coluna(celulas, indice_final)),
                    "media": parse_numero(self._valor_coluna(celulas, indice_media)),
                    "frequencia": self._valor_coluna(celulas, indice_frequencia),
                    "situacao": self._valor_coluna(celulas, indice_situacao),
                }
            )

        # "nota_final" vazio mas "media" preenchida: usa a média como referência.
        for aluno in alunos:
            if aluno["nota_final"] is None:
                aluno["nota_final"] = aluno.get("media")

        colunas = [cabecalho[i] for i in range(len(cabecalho)) if cabecalho[i]]
        return {"alunos": alunos, "colunas": colunas}

    # ------------------------------------------------------------------
    # Auxiliares de parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _cabecalho_mais_provavel(soup: BeautifulSoup) -> List[str]:
        """Escolhe o cabeçalho mais informativo entre as tabelas da página."""
        melhor: List[str] = []
        for tabela in soup.find_all("table"):
            cabecalho = SigaAPI._cabecalho_da_tabela(tabela.find_all("tr"))
            if len(cabecalho) > len(melhor):
                melhor = cabecalho
        return melhor

    @staticmethod
    def _cabecalho_com_matriculas(soup: BeautifulSoup) -> List[str]:
        """Cabeçalho da tabela que efetivamente lista matrículas de alunos."""
        for tabela in soup.find_all("table"):
            linhas = tabela.find_all("tr")
            if any(SigaAPI._matricula_da_linha(_celulas(linha)) for linha in linhas[1:]):
                return SigaAPI._cabecalho_da_tabela(linhas)
        return SigaAPI._cabecalho_mais_provavel(soup)

    @staticmethod
    def _cabecalho_da_tabela_com_link(
        soup: BeautifulSoup, textos: Iterable[str]
    ) -> List[str]:
        """Cabeçalho da tabela que contém um link com um dos *textos*."""
        for tabela in soup.find_all("table"):
            if any(_link_por_texto(tabela, texto) for texto in textos):
                return SigaAPI._cabecalho_da_tabela(tabela.find_all("tr"))
        return SigaAPI._cabecalho_mais_provavel(soup)

    @staticmethod
    def _cabecalho_da_tabela(linhas: Iterable[Any]) -> List[str]:
        """Devolve a linha de cabeçalho da tabela (até as 3 primeiras linhas)."""
        candidatos: List[List[str]] = []
        for linha in list(linhas)[:3]:
            celulas = _celulas(linha)
            if celulas and any(celulas):
                candidatos.append(celulas)
        if not candidatos:
            return []
        if len(candidatos) == 1:
            return candidatos[0]
        cabecalho = max(candidatos, key=len)
        return cabecalho if any(cabecalho) else []

    @staticmethod
    def _valor_coluna(celulas: List[str], indice: Optional[int]) -> Optional[str]:
        """Devolve a célula no índice informado, se existir."""
        if indice is None or indice >= len(celulas):
            return None
        valor = celulas[indice]
        return valor or None

    @staticmethod
    def _matricula_da_linha(celulas: List[str]) -> Optional[str]:
        """Primeira célula que parece uma matrícula (só dígitos)."""
        for celula in celulas:
            if MATRICULA_RE.match(celula):
                return celula
        return None

    @staticmethod
    def _nome_da_linha(celulas: List[str]) -> Optional[str]:
        """Célula com mais letras e que não parece nota/data (nome do aluno)."""
        melhor = None
        melhor_pontos = 0
        for celula in celulas:
            if not celula or MATRICULA_RE.match(celula) or DATA_RE.match(celula):
                continue
            if chave_busca(celula) in PALAVRAS_STATUS:
                continue
            letras = sum(1 for c in celula if c.isalpha())
            pontos = letras - (5 if parse_numero(celula) is not None else 0)
            if letras >= 3 and pontos > melhor_pontos:
                melhor, melhor_pontos = celula, pontos
        return melhor

    @staticmethod
    def _adivinhar_descricao(celulas: List[str]) -> Optional[str]:
        """Heurística para a coluna de descrição quando o cabeçalho não ajuda."""
        ignorar = {"lancar", "editar", "excluir", "tvc", "peso", "data", "nota", "acoes"}
        candidatos = []
        for celula in celulas:
            chave = chave_busca(celula)
            letras = sum(1 for c in celula if c.isalpha())
            if letras >= 3 and chave not in ignorar and parse_numero(celula) is None:
                candidatos.append((letras, celula))
        if not candidatos:
            return None
        return max(candidatos)[1]

    @staticmethod
    def _adivinhar_peso(celulas: List[str]) -> Optional[float]:
        """Número pequeno e não-data em célula isolada, provavelmente o peso."""
        for celula in celulas:
            if DATA_RE.match(celula):
                continue
            valor = parse_numero(celula)
            if valor is not None and 0 < valor <= 100:
                return valor
        return None

    @staticmethod
    def _adivinhar_data(celulas: List[str]) -> Optional[str]:
        """Primeira célula no formato dd/mm/aaaa."""
        for celula in celulas:
            if DATA_RE.match(celula):
                return celula
        return None

    @staticmethod
    def _resumir(alunos: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Estatísticas simples sobre as notas finais efetivamente retornadas."""
        finais = [
            aluno["nota_final"]
            for aluno in alunos
            if isinstance(aluno.get("nota_final"), (int, float))
        ]
        situacoes = Counter(
            limpar_texto(aluno.get("situacao"))
            for aluno in alunos
            if aluno.get("situacao")
        )
        return {
            "total_alunos": len(alunos),
            "com_nota_final": len(finais),
            "sem_nota_final": len(alunos) - len(finais),
            "media_nota_final": round(sum(finais) / len(finais), 2) if finais else None,
            "situacoes": dict(situacoes),
        }

    # ------------------------------------------------------------------
    def fechar(self) -> None:
        """Encerra a sessão HTTP."""
        self.session.close()
