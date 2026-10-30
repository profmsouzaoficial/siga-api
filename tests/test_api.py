"""Testes das rotas HTTP (sem tocar no SIGA real)."""

from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

import main
from siga_bot import SigaAuthError, SigaError, SigaNaoEncontrado

CHAVE = "chave-de-teste"

QUADRO_EXEMPLO = {
    "consulta": {
        "periodo": "2026/3",
        "id_periodo": "268",
        "turma": "CBV018GV-A",
        "id_turma": "12345",
        "fonte": "quadro_geral",
        "gerado_em": "2026-10-30T18:00:00+00:00",
        "requisicoes": 4,
    },
    "avaliacoes": [{"id": "987", "descricao": "TVC 1", "peso": 3.0, "data": "10/09/2026"}],
    "colunas": ["Matrícula", "Nome", "TVC 1", "Nota Final", "Frequência", "Situação"],
    "alunos": [
        {
            "matricula": "202165001",
            "nome": "ANA BEATRIZ SOUZA",
            "notas": {"TVC 1": 8.5},
            "nota_final": 8.3,
            "frequencia": "100,0",
            "situacao": "Aprovado",
        }
    ],
    "resumo": {
        "total_alunos": 1,
        "com_nota_final": 1,
        "sem_nota_final": 0,
        "media_nota_final": 8.3,
        "situacoes": {"Aprovado": 1},
    },
    "avisos": [],
}


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    """Configuração mínima de ambiente para cada teste."""
    monkeypatch.setenv("API_SECRET_KEY", CHAVE)
    monkeypatch.setenv("SIGA_CPF", "00000000000")
    monkeypatch.setenv("SIGA_SENHA", "senha-ficticia")
    monkeypatch.delenv("SIGA_PERIODO_PADRAO", raising=False)
    monkeypatch.delenv("API_CORS_ORIGINS", raising=False)


@pytest.fixture
def cliente():
    return TestClient(main.app)


def fabrica_siga(resultado=None, erro=None, erro_login=None):
    """Cria uma classe dublê de SigaAPI para substituir main.SigaAPI."""

    class SigaFalso:
        instancias: ClassVar[list] = []

        def __init__(self):
            self.fechado = False
            SigaFalso.instancias.append(self)

        def login(self, cpf, senha):
            if erro_login:
                raise erro_login

        def obter_quadro_notas(self, turma, periodo):
            if erro:
                raise erro
            return resultado

        def lancar_notas(self, **kwargs):
            if erro:
                raise erro
            return {
                "avaliacao": kwargs["descricao_avaliacao"],
                "id_avaliacao": "987",
                "id_turma": "12345",
                "lancados": [{"matricula": "202165001", "nome": "ANA", "nota": 8.5}],
                "nao_encontrados": [],
                "notas_invalidas": [],
            }

        def criar_avaliacao(self, **kwargs):
            if erro:
                raise erro
            return {}

        def fechar(self):
            self.fechado = True

    return SigaFalso


# ---------------------------------------------------------------------------
# Infraestrutura e autenticação
# ---------------------------------------------------------------------------
def test_raiz_e_health_sem_chave(cliente):
    assert cliente.get("/health").json() == {"status": "ok"}
    assert cliente.get("/").json()["servico"] == "API AUTO-SIGA UFJF"


def test_chave_ausente(cliente):
    resposta = cliente.get("/api/notas", params={"turma": "CBV018GV-A", "periodo": "2026/3"})
    assert resposta.status_code == 403


def test_chave_invalida(cliente):
    resposta = cliente.get(
        "/api/notas",
        params={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": "chave-errada"},
    )
    assert resposta.status_code == 403


def test_chave_mestra_nao_configurada(cliente, monkeypatch):
    monkeypatch.delenv("API_SECRET_KEY")
    resposta = cliente.get(
        "/api/notas",
        params={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": "qualquer"},
    )
    assert resposta.status_code == 503


def test_credenciais_do_siga_ausentes(cliente, monkeypatch):
    monkeypatch.delenv("SIGA_SENHA")
    resposta = cliente.get(
        "/api/notas",
        params={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 500
    assert "SIGA_CPF" in resposta.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/notas
# ---------------------------------------------------------------------------
def test_notas_retorna_quadro(cliente, monkeypatch):
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga(resultado=QUADRO_EXEMPLO))
    resposta = cliente.get(
        "/api/notas",
        params={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["consulta"]["fonte"] == "quadro_geral"
    assert corpo["alunos"][0]["notas"] == {"TVC 1": 8.5}
    assert corpo["resumo"]["total_alunos"] == 1


def test_notas_turma_inexistente_retorna_404(cliente, monkeypatch):
    monkeypatch.setattr(
        main, "SigaAPI", fabrica_siga(erro=SigaNaoEncontrado("Turma 'XXX' não encontrada."))
    )
    resposta = cliente.get(
        "/api/notas",
        params={"turma": "XXX", "periodo": "2026/3"},
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 404
    assert "não encontrada" in resposta.json()["detail"]


def test_notas_siga_fora_do_ar_retorna_502(cliente, monkeypatch):
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga(erro=SigaError("Falha de rede")))
    resposta = cliente.get(
        "/api/notas",
        params={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 502


def test_notas_login_recusado_retorna_502(cliente, monkeypatch):
    monkeypatch.setattr(
        main, "SigaAPI", fabrica_siga(erro_login=SigaAuthError("Credenciais inválidas"))
    )
    resposta = cliente.get(
        "/api/notas",
        params={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 502
    assert "Credenciais" in resposta.json()["detail"]


def test_erro_inesperado_nao_vaza_traceback(cliente, monkeypatch):
    monkeypatch.setattr(
        main, "SigaAPI", fabrica_siga(erro=RuntimeError("detalhe-interno-secreto"))
    )
    resposta = cliente.get(
        "/api/notas",
        params={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 500
    detalhe = resposta.json()["detail"]
    assert "detalhe-interno-secreto" not in detalhe
    assert "Traceback" not in detalhe


def test_sessao_e_fechada_ao_final(cliente, monkeypatch):
    duble = fabrica_siga(resultado=QUADRO_EXEMPLO)
    monkeypatch.setattr(main, "SigaAPI", duble)
    cliente.get(
        "/api/notas",
        params={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": CHAVE},
    )
    assert duble.instancias[0].fechado is True


# ---------------------------------------------------------------------------
# POST /api/notas
# ---------------------------------------------------------------------------
def test_notas_post_com_corpo(cliente, monkeypatch):
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga(resultado=QUADRO_EXEMPLO))
    resposta = cliente.post(
        "/api/notas",
        json={"turma": "CBV018GV-A", "periodo": "2026/3"},
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 200
    assert resposta.json()["consulta"]["id_turma"] == "12345"


def test_notas_post_sem_periodo_e_422(cliente, monkeypatch):
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga(resultado=QUADRO_EXEMPLO))
    resposta = cliente.post(
        "/api/notas", json={"turma": "CBV018GV-A"}, headers={"X-API-Key": CHAVE}
    )
    assert resposta.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/lancar-notas
# ---------------------------------------------------------------------------
def test_lancar_notas_exige_periodo_ou_padrao(cliente, monkeypatch):
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga())
    resposta = cliente.post(
        "/api/lancar-notas",
        json={
            "turma": "CBV018GV-A",
            "descricao_avaliacao": "TVC 1",
            "notas_json": {"202165001": 8.5},
        },
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 400
    assert "periodo" in resposta.json()["detail"]


def test_lancar_notas_usa_periodo_padrao_do_servidor(cliente, monkeypatch):
    monkeypatch.setenv("SIGA_PERIODO_PADRAO", "2026/3")
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga())
    resposta = cliente.post(
        "/api/lancar-notas",
        json={
            "turma": "CBV018GV-A",
            "descricao_avaliacao": "TVC 1",
            "notas_json": {"202165001": 8.5},
        },
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "sucesso"
    assert corpo["lancados"][0]["matricula"] == "202165001"
    assert "TVC 1" in corpo["mensagem"]


def test_lancar_notas_com_notas_vazias_e_422(cliente, monkeypatch):
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga())
    resposta = cliente.post(
        "/api/lancar-notas",
        json={
            "turma": "CBV018GV-A",
            "descricao_avaliacao": "TVC 1",
            "notas_json": {},
            "periodo": "2026/3",
        },
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/criar-avaliacao
# ---------------------------------------------------------------------------
def test_criar_avaliacao_valida_data(cliente, monkeypatch):
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga())
    resposta = cliente.post(
        "/api/criar-avaliacao",
        json={
            "turma": "CBV018GV-A",
            "num_tvc": 4,
            "peso": 10,
            "data": "2026-10-30",
            "descricao": "Trabalho",
            "periodo": "2026/3",
        },
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 422


def test_criar_avaliacao_ok(cliente, monkeypatch):
    monkeypatch.setattr(main, "SigaAPI", fabrica_siga())
    resposta = cliente.post(
        "/api/criar-avaliacao",
        json={
            "turma": "CBV018GV-A",
            "num_tvc": 4,
            "peso": 10,
            "data": "30/10/2026",
            "descricao": "Trabalho Prático",
            "periodo": "2026/3",
        },
        headers={"X-API-Key": CHAVE},
    )
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "sucesso"


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------
def test_openapi_documenta_as_rotas():
    esquema = main.app.openapi()
    assert "/api/notas" in esquema["paths"]
    assert {"get", "post"} <= set(esquema["paths"]["/api/notas"])
    assert "/api/lancar-notas" in esquema["paths"]
    assert "/api/criar-avaliacao" in esquema["paths"]
