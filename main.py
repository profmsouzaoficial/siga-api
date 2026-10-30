"""API HTTP para automação do SIGA/UFJF.

Rotas disponíveis:

* ``GET  /health``             — verificação de saúde (sem autenticação).
* ``POST /api/lancar-notas``   — grava notas de uma avaliação (TVC).
* ``POST /api/criar-avaliacao``— cria uma avaliação (TVC).
* ``GET  /api/notas``          — **lê** o quadro de notas da turma (JSON organizado).
* ``POST /api/notas``          — mesma leitura, com os parâmetros no corpo.

Todas as rotas ``/api/*`` exigem o cabeçalho ``X-API-Key``.
"""

import logging
import os
import secrets
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

try:  # Carrega o .env quando a dependência opcional está instalada.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - a API funciona com variáveis já exportadas
    pass

from siga_bot import SigaAPI, SigaAuthError, SigaError, SigaNaoEncontrado

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("auto_siga_api")

app = FastAPI(
    title="API AUTO-SIGA UFJF (HTTP Direto)",
    version="1.1.0",
    description=(
        "Automação não oficial do SIGA/UFJF via HTTP direto, sem navegador. "
        "Exige o cabeçalho X-API-Key em todas as rotas /api/*."
    ),
)

# ==========================================
# 1. SEGURANÇA E AUTENTICAÇÃO DA API
# ==========================================
api_key_header = APIKeyHeader(name="X-API-Key")


def validar_api_key(chave_recebida: str = Security(api_key_header)) -> str:
    """Valida a chave da API comparando em tempo constante."""
    chave_mestra = os.getenv("API_SECRET_KEY")
    if not chave_mestra:
        # Erro de configuração do servidor, não do cliente.
        LOGGER.error("API_SECRET_KEY não configurada.")
        raise HTTPException(
            status_code=503,
            detail="API_SECRET_KEY não configurada no servidor.",
        )
    if not chave_recebida or not secrets.compare_digest(
        str(chave_recebida), str(chave_mestra)
    ):
        raise HTTPException(status_code=403, detail="Acesso negado. Chave de API inválida.")
    return chave_recebida


# CORS opcional: informe as origens em API_CORS_ORIGINS (separadas por vírgula).
_origens = [o.strip() for o in (os.getenv("API_CORS_ORIGINS") or "").split(",") if o.strip()]
if _origens:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origens,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
    )


# ==========================================
# 2. MODELOS DE DADOS
# ==========================================
class LancarNotasRequest(BaseModel):
    turma: str = Field(..., min_length=3, examples=["CBV018GV-A"])
    descricao_avaliacao: str = Field(..., min_length=1, examples=["TVC 1"])
    notas_json: Dict[str, Any] = Field(
        ...,
        description="Mapa matrícula -> nota. Aceita número ou texto com vírgula.",
        examples=[{"202165001": 8.5, "202033002": "9,0"}],
    )
    periodo: Optional[str] = Field(
        None,
        description="Período letivo (ex.: '2026/3') ou ID interno do SIGA. "
        "Se omitido, usa SIGA_PERIODO_PADRAO.",
        examples=["2026/3"],
    )


class CriarAvaliacaoRequest(BaseModel):
    turma: str = Field(..., min_length=3, examples=["CBV018GV-A"])
    num_tvc: int = Field(..., ge=1, examples=[4])
    peso: float = Field(..., gt=0, examples=[10.0])
    data: str = Field(..., pattern=r"^\d{2}/\d{2}/\d{4}$", examples=["30/10/2026"])
    descricao: str = Field(..., min_length=1, examples=["Trabalho Prático"])
    periodo: Optional[str] = Field(None, examples=["2026/3"])


class ConsultaNotasRequest(BaseModel):
    turma: str = Field(..., min_length=3, examples=["CBV018GV-A"])
    periodo: str = Field(..., min_length=4, examples=["2026/3"])


# ==========================================
# 3. INFRAESTRUTURA
# ==========================================
def _periodo_ou_padrao(periodo: Optional[str]) -> str:
    """Resolve o período informado ou o padrão configurado no servidor."""
    resolvido = (periodo or os.getenv("SIGA_PERIODO_PADRAO") or "").strip()
    if not resolvido:
        raise HTTPException(
            status_code=400,
            detail="Informe 'periodo' (ex.: '2026/3') ou configure SIGA_PERIODO_PADRAO.",
        )
    return resolvido


def _abrir_sessao() -> SigaAPI:
    """Cria e autentica uma sessão no SIGA."""
    cpf = (os.getenv("SIGA_CPF") or "").strip()
    senha = os.getenv("SIGA_SENHA") or ""
    if not cpf or not senha:
        raise HTTPException(
            status_code=500,
            detail="Credenciais do SIGA ausentes. Configure SIGA_CPF e SIGA_SENHA.",
        )
    siga = SigaAPI()
    try:
        siga.login(cpf=cpf, senha=senha)
    except Exception:
        siga.fechar()
        raise
    return siga


def _executar(acao):
    """Executa *acao* numa sessão autenticada, traduzindo erros para HTTP."""
    siga = None
    try:
        siga = _abrir_sessao()
        return acao(siga)
    except SigaNaoEncontrado as erro:
        LOGGER.warning("Não encontrado: %s", erro)
        raise HTTPException(status_code=404, detail=str(erro)) from erro
    except SigaAuthError as erro:
        LOGGER.warning("Falha de autenticação no SIGA: %s", erro)
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    except SigaError as erro:
        LOGGER.warning("Erro do SIGA: %s", erro)
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    except HTTPException:
        raise
    except Exception:  # pragma: no cover - rede de segurança
        LOGGER.exception("Erro inesperado ao falar com o SIGA")
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao falar com o SIGA. Consulte os logs do serviço.",
        ) from None
    finally:
        if siga is not None:
            siga.fechar()


# ==========================================
# 4. ROTAS DA API
# ==========================================
@app.get("/", tags=["infra"], summary="Identificação do serviço")
def raiz():
    return {
        "servico": "API AUTO-SIGA UFJF",
        "versao": app.version,
        "documentacao": "/docs",
        "saude": "/health",
    }


@app.get("/health", tags=["infra"], summary="Verificação de saúde")
def health():
    return {"status": "ok"}


@app.get(
    "/api/notas",
    tags=["notas"],
    summary="Quadro de notas da turma",
    description=(
        "Lê o quadro de notas de uma turma: notas parciais por avaliação, nota "
        "final, frequência e situação de cada aluno, além de um resumo da turma. "
        "Os dados vêm do submenu 'Quadro Geral' do SIGA e, quando ele não está "
        "disponível, são montados percorrendo as TVCs da turma."
    ),
    responses={
        200: {"description": "Quadro de notas retornado com sucesso."},
        403: {"description": "X-API-Key ausente ou inválida."},
        404: {"description": "Turma, período ou avaliação não encontrados."},
        502: {"description": "SIGA indisponível, credenciais recusadas ou HTML alterado."},
    },
)
def api_notas(
    turma: str,
    periodo: str,
    api_key: str = Depends(validar_api_key),
):
    return _executar(lambda siga: siga.obter_quadro_notas(turma=turma, periodo=periodo))


@app.post(
    "/api/notas",
    tags=["notas"],
    summary="Quadro de notas da turma (corpo JSON)",
    description="Mesma leitura de GET /api/notas, com os parâmetros no corpo.",
    responses={200: {"description": "Quadro de notas retornado com sucesso."}},
)
def api_notas_post(
    req: ConsultaNotasRequest,
    api_key: str = Depends(validar_api_key),
):
    return _executar(
        lambda siga: siga.obter_quadro_notas(turma=req.turma, periodo=req.periodo)
    )


@app.post(
    "/api/lancar-notas",
    tags=["notas"],
    summary="Lançar notas de uma avaliação",
)
def api_lancar_notas(
    req: LancarNotasRequest,
    api_key: str = Depends(validar_api_key),
):
    periodo = _periodo_ou_padrao(req.periodo)
    resultado = _executar(
        lambda siga: siga.lancar_notas(
            turma=req.turma,
            descricao_avaliacao=req.descricao_avaliacao,
            notas_json=req.notas_json,
            periodo_texto=periodo,
        )
    )
    return {
        "status": "sucesso",
        "mensagem": f"Notas lançadas na turma {req.turma} (avaliação '{resultado['avaliacao']}').",
        **resultado,
    }


@app.post(
    "/api/criar-avaliacao",
    tags=["avaliacoes"],
    summary="Criar avaliação (TVC)",
)
def api_criar_avaliacao(
    req: CriarAvaliacaoRequest,
    api_key: str = Depends(validar_api_key),
):
    periodo = _periodo_ou_padrao(req.periodo)
    _executar(
        lambda siga: siga.criar_avaliacao(
            turma=req.turma,
            num_tvc=req.num_tvc,
            peso=req.peso,
            data=req.data,
            descricao=req.descricao,
            periodo=periodo,
        )
    )
    return {
        "status": "sucesso",
        "mensagem": f"Avaliação '{req.descricao}' criada na turma {req.turma}.",
    }
