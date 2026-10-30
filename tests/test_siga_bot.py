"""Testes do cliente do SIGA (parsing e fluxo de leitura/lançamento)."""

import json

import pytest
from bs4 import BeautifulSoup
from conftest import RespostaFalsa, SigaFake, ler

from siga_bot import (
    SigaAPI,
    SigaError,
    SigaNaoEncontrado,
    _href_absoluto,
    _id_da_url,
    chave_busca,
    normalizar,
    parse_numero,
)


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("8,5", 8.5),
        ("8.5", 8.5),
        ("10", 10.0),
        ("-", None),
        ("", None),
        ("   ", None),
        ("—", None),
        ("1.234,5", 1234.5),
        ("Aprovado", None),
        (8, 8.0),
    ],
)
def test_parse_numero(entrada, esperado):
    assert parse_numero(entrada) == esperado


def test_normalizar_remove_acento_e_espaco():
    assert normalizar("  Frequência\n  do   Aluno ") == "Frequencia do Aluno"
    assert chave_busca("Nota Final") == "nota final"
    assert normalizar(None) == ""


def test_id_e_href():
    assert _id_da_url("/index.php/siga/academico/nota/formTVCFind/987?go=x") == "987"
    assert _id_da_url("https://x/y/12345/") == "12345"
    assert _href_absoluto("/index.php/a").startswith("https://siga.ufjf.br/index.php/a")
    assert _href_absoluto("") == ""


# ---------------------------------------------------------------------------
# Descoberta de período, docente e turma
# ---------------------------------------------------------------------------
def test_resolver_periodo_por_texto_e_por_id():
    soup = BeautifulSoup(ler("passo1_periodos.html"), "html.parser")
    siga = SigaAPI()
    assert siga._resolver_periodo(soup, "2026/3") == "268"
    assert siga._resolver_periodo(soup, "268") == "268"
    with pytest.raises(SigaNaoEncontrado):
        siga._resolver_periodo(soup, "2030/1")


def test_resolver_periodo_por_variavel_de_ambiente(monkeypatch):
    monkeypatch.setenv("SIGA_PERIODO_IDS", "2026/9=999")
    soup = BeautifulSoup("<html></html>", "html.parser")
    assert SigaAPI()._resolver_periodo(soup, "2026/9") == "999"


def test_descobrir_id_docente_sem_hardcode(monkeypatch):
    soup = BeautifulSoup(ler("passo1_periodos.html"), "html.parser")
    assert SigaAPI._descobrir_id_docente(soup) == "29230"

    monkeypatch.setenv("SIGA_ID_DOCENTE", "4242")
    soup_vazia = BeautifulSoup("<html><body><form></form></body></html>", "html.parser")
    assert SigaAPI._descobrir_id_docente(soup_vazia) == "4242"

    monkeypatch.delenv("SIGA_ID_DOCENTE")
    assert SigaAPI._descobrir_id_docente(soup_vazia) is None


def test_localizar_turma_exige_codigo_exato():
    soup = BeautifulSoup(ler("passo2_turmas.html"), "html.parser")
    _linha, celulas = SigaAPI._localizar_linha(soup, "CBV018GV-A", rotulo="turma")
    assert "CBV018GV-A" in celulas
    assert "CBV018GV-AB" not in celulas

    _linha_ab, celulas_ab = SigaAPI._localizar_linha(soup, "CBV018GV-AB", rotulo="turma")
    assert "CBV018GV-AB" in celulas_ab

    # Prefixo casa em duas linhas: deve recusar em vez de escolher a errada.
    with pytest.raises(SigaError) as erro:
        SigaAPI._localizar_linha(soup, "CBV018GV", rotulo="turma")
    assert "ambíguo" in str(erro.value).lower()


def test_localizar_turma_nao_encontrada():
    soup = BeautifulSoup(ler("passo2_turmas.html"), "html.parser")
    with pytest.raises(SigaNaoEncontrado):
        SigaAPI._localizar_linha(soup, "XXXX000GV-Z", rotulo="turma")


# ---------------------------------------------------------------------------
# Parsers de tabela
# ---------------------------------------------------------------------------
def test_parse_avaliacoes():
    avaliacoes = SigaAPI()._parse_avaliacoes(ler("tvc_grid.html"))
    assert [a["id"] for a in avaliacoes] == ["987", "988", "989"]
    primeira = avaliacoes[0]
    assert primeira["descricao"] == "TVC 1"
    assert primeira["peso"] == 3.0
    assert primeira["data"] == "10/09/2026"
    assert primeira["url"].endswith("/formNotaParcial/987?go=formTVCFind")
    # O link por imagem (TVC 10) também foi reconhecido.
    assert avaliacoes[2]["descricao"] == "TVC 10"


def test_escolher_avaliacao_prefere_match_exato():
    siga = SigaAPI()
    avaliacoes = siga._parse_avaliacoes(ler("tvc_grid.html"))
    assert siga._escolher_avaliacao(avaliacoes, "TVC 1")["id"] == "987"
    assert siga._escolher_avaliacao(avaliacoes, "TVC 10")["id"] == "989"
    with pytest.raises(SigaError):
        siga._escolher_avaliacao(avaliacoes, "TVC")
    with pytest.raises(SigaNaoEncontrado):
        siga._escolher_avaliacao(avaliacoes, "Prova Final")


def test_parse_pauta_com_inputs():
    registros = SigaAPI()._parse_pauta(ler("pauta_tvc1.html"))
    assert len(registros) == 3
    assert registros[0] == {
        "matricula": "202165001",
        "nome": "ANA BEATRIZ SOUZA",
        "nota": 8.5,
    }
    assert [r["nota"] for r in registros] == [8.5, 6.0, 10.0]

    registros_vazios = SigaAPI()._parse_pauta(ler("pauta_tvc2.html"))
    assert registros_vazios[2]["nota"] is None  # Carla ainda sem nota


def test_parse_quadro_geral():
    quadro = SigaAPI()._parse_quadro_geral(ler("quadro_geral.html"))
    assert [a["matricula"] for a in quadro["alunos"]] == [
        "202165001",
        "202033002",
        "202288003",
        "202011004",
    ]
    ana = quadro["alunos"][0]
    assert ana["nome"] == "ANA BEATRIZ SOUZA"
    assert ana["notas"] == {"TVC 1": 8.5, "TVC 2": 9.0, "TVC 3": 7.5}
    assert ana["nota_final"] == 8.3
    assert ana["frequencia"] == "100,0"
    assert ana["situacao"] == "Aprovado"

    diego = quadro["alunos"][3]
    assert diego["notas"] == {"TVC 1": None, "TVC 2": None, "TVC 3": None}
    assert diego["nota_final"] is None
    assert diego["situacao"] == "Reprovado por falta"

    assert "Matrícula" in quadro["colunas"]
    assert "Nota Final" in quadro["colunas"]


def test_parse_quadro_geral_usa_media_quando_falta_nota_final():
    html = """
    <table>
      <tr><th>Matrícula</th><th>Nome</th><th>Média</th><th>Situação</th></tr>
      <tr><td>202165001</td><td>ANA</td><td>7,5</td><td>Aprovado</td></tr>
    </table>
    """
    aluno = SigaAPI()._parse_quadro_geral(html)["alunos"][0]
    assert aluno["nota_final"] == 7.5
    assert aluno["media"] == 7.5


def test_detectar_erro_em_resposta():
    assert SigaAPI._detectar_erro('<div class="alert">Erro ao salvar nota inválida</div>')
    assert SigaAPI._detectar_erro("<div>OK</div>") is None


# ---------------------------------------------------------------------------
# Desembrulho do JSON do Dojo
# ---------------------------------------------------------------------------
def test_html_da_resposta_desembrulha_json():
    siga = SigaAPI()
    assert siga._html_da_resposta(
        RespostaFalsa(texto=json.dumps({"data": "<table><tr><td>x</td></tr></table>"}))
    ) == "<table><tr><td>x</td></tr></table>"
    assert siga._html_da_resposta(
        RespostaFalsa(texto=json.dumps([{"data": "<p>lista</p>"}]))
    ) == "<p>lista</p>"
    assert siga._html_da_resposta(RespostaFalsa(texto="<html>html puro</html>")) == (
        "<html>html puro</html>"
    )
    with pytest.raises(SigaError):
        siga._html_da_resposta(RespostaFalsa(texto=json.dumps({"error": "boom"})))


# ---------------------------------------------------------------------------
# Fluxo completo
# ---------------------------------------------------------------------------
def test_quadro_de_notas_pelo_quadro_geral(siga_quadro_geral):
    resultado = siga_quadro_geral.obter_quadro_notas("CBV018GV-A", "2026/3")

    assert resultado["consulta"]["fonte"] == "quadro_geral"
    assert resultado["consulta"]["id_turma"] == "12345"
    assert resultado["consulta"]["id_periodo"] == "268"
    assert len(resultado["alunos"]) == 4
    assert resultado["alunos"][0]["matricula"] == "202165001"
    assert resultado["resumo"] == {
        "total_alunos": 4,
        "com_nota_final": 3,
        "sem_nota_final": 1,
        "media_nota_final": 8.03,
        "situacoes": {
            "Aprovado": 2,
            "Reprovado por nota": 1,
            "Reprovado por falta": 1,
        },
    }
    assert resultado["avisos"] == []


def test_quadro_de_notas_montado_pelas_tvcs(siga_sem_quadro_geral):
    resultado = siga_sem_quadro_geral.obter_quadro_notas("CBV018GV-A", "2026/3")

    assert resultado["consulta"]["fonte"] == "tvc_por_avaliacao"
    assert [a["id"] for a in resultado["avaliacoes"]] == ["987", "988", "989"]
    assert resultado["colunas"] == ["TVC 1", "TVC 2", "TVC 10"]

    ana = next(a for a in resultado["alunos"] if a["matricula"] == "202165001")
    assert ana["notas"] == {"TVC 1": 8.5, "TVC 2": 9.0, "TVC 10": 7.0}
    assert ana["nota_final"] is None  # não é inventada

    carla = next(a for a in resultado["alunos"] if a["matricula"] == "202288003")
    assert carla["notas"] == {"TVC 1": 10.0, "TVC 2": None, "TVC 10": None}

    assert any("Quadro Geral" in aviso for aviso in resultado["avisos"])


def test_quadro_de_notas_com_falha_no_quadro_geral_usa_fallback(paginas_quadro_geral):
    siga = SigaFake(
        paginas_quadro_geral,
        erros={"formQuadroGeral": SigaError("HTML inesperado")},
    )
    resultado = siga.obter_quadro_notas("CBV018GV-A", "2026/3")
    assert resultado["consulta"]["fonte"] == "tvc_por_avaliacao"
    assert len(resultado["alunos"]) == 3
    assert any("Falha ao ler o Quadro Geral" in aviso for aviso in resultado["avisos"])


def test_lancar_notas_mapeia_matriculas_e_reporta_faltantes(siga_sem_quadro_geral):
    resultado = siga_sem_quadro_geral.lancar_notas(
        turma="CBV018GV-A",
        descricao_avaliacao="TVC 1",
        notas_json={"202165001": 9.5, "202033002": "7,0", "999999999": 5, "202288003": "x"},
        periodo_texto="2026/3",
    )

    assert [lancado["matricula"] for lancado in resultado["lancados"]] == [
        "202165001",
        "202033002",
    ]
    assert resultado["nao_encontrados"] == ["999999999"]
    assert resultado["notas_invalidas"] == [{"matricula": "202288003", "nota": "x"}]

    enviado = siga_sem_quadro_geral.salvamentos[-1]
    assert enviado["tvc::idTVC"] == "987"
    assert enviado["tvc::idTurma"] == "12345"
    assert enviado["notas[501]"] == "9.5"
    assert enviado["notas[502]"] == "7"
    # A nota de quem não foi informado segue como estava no SIGA.
    assert enviado["notas[503]"] == "10,0"


def test_lancar_notas_sem_matricula_valida_nao_salva(siga_sem_quadro_geral):
    with pytest.raises(SigaNaoEncontrado):
        siga_sem_quadro_geral.lancar_notas(
            turma="CBV018GV-A",
            descricao_avaliacao="TVC 1",
            notas_json={"123456789": 10},
            periodo_texto="2026/3",
        )
    assert siga_sem_quadro_geral.salvamentos == []


def test_exigir_login():
    siga = SigaAPI()
    with pytest.raises(SigaError):
        siga.obter_quadro_notas("CBV018GV-A", "2026/3")


def test_parse_pauta_nao_confunde_situacao_com_nome():
    """Nome curto não pode ser trocado por "Matriculado"."""
    html = """
    <table>
      <tr><th>Matrícula</th><th>Aluno</th><th>Situação</th><th>Nota</th></tr>
      <tr>
        <td>202165001</td><td>JOSÉ LIMA</td><td>Matriculado</td>
        <td><input type="text" name="notas[1]" value="7,0"></td>
      </tr>
    </table>
    """
    registro = SigaAPI()._parse_pauta(html)[0]
    assert registro["nome"] == "JOSÉ LIMA"
    assert registro["matricula"] == "202165001"
    assert registro["nota"] == 7.0


def test_parse_pauta_sem_cabecalho_nao_confunde_situacao_com_nome():
    html = """
    <table>
      <tr>
        <td>202165001</td><td>JOSÉ LIMA</td><td>Matriculado</td>
        <td><input type="text" name="notas[1]" value="7,0"></td>
      </tr>
    </table>
    """
    registro = SigaAPI()._parse_pauta(html)[0]
    assert registro["nome"] == "JOSÉ LIMA"
