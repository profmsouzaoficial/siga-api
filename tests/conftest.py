"""Fixtures e dublês do SIGA usados nos testes.

Os HTMLs em ``tests/fixtures`` são **sintéticos**: reproduzem a estrutura que o
SIGA devolve (grade de turmas, grade de TVCs, pauta com ``notas[N]`` e o Quadro
Geral), mas não são capturas reais. Servem para validar o parser e o contrato da
API sem depender de credenciais ou de rede.
"""

import json
from pathlib import Path

import pytest

from siga_bot import SigaAPI, SigaError

DIR_FIXTURES = Path(__file__).parent / "fixtures"


def ler(nome: str) -> str:
    """Lê um HTML de fixture."""
    return (DIR_FIXTURES / nome).read_text(encoding="utf-8")


class RespostaFalsa:
    """Imita ``requests.Response`` no mínimo que o cliente usa."""

    def __init__(self, texto="", status_code=200, url="", dados_json=None):
        self.text = texto
        self.status_code = status_code
        self.url = url
        self._json = dados_json

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)

    @property
    def ok(self):
        return self.status_code < 400


class SigaFake(SigaAPI):
    """SigaAPI com a rede substituída por páginas de fixture."""

    def __init__(self, paginas=None, erros=None):
        super().__init__()
        self.logado = True
        self.paginas = paginas or {}
        self.erros = erros or {}
        self.chamadas = []
        self.salvamentos = []

    def _get_html(self, url, referer=None):
        self.chamadas.append(("GET", url))
        for marcador, erro in self.erros.items():
            if marcador in url:
                raise erro
        candidatos = [marcador for marcador in self.paginas if marcador in url]
        if not candidatos:
            raise SigaError(f"URL inesperada no teste: {url}")
        return self.paginas[max(candidatos, key=len)]

    def _request(self, metodo, url, **kwargs):
        self.chamadas.append((metodo, url))
        if "formNotaParcialPasso2" in url:
            return RespostaFalsa(
                texto=json.dumps({"data": self.paginas["buscaTurmas"]}), url=url
            )
        if "saveNotasParciais" in url:
            self.salvamentos.append(kwargs.get("data") or {})
            return RespostaFalsa(
                texto=json.dumps({"data": "<div>Notas salvas com sucesso</div>"}), url=url
            )
        if "TVCSave" in url:
            self.salvamentos.append(kwargs.get("data") or {})
            return RespostaFalsa(texto=json.dumps({"data": "<div>TVC criada</div>"}), url=url)
        raise SigaError(f"POST inesperado no teste: {url}")


@pytest.fixture
def paginas_quadro_geral():
    return {
        "formNotaParcial/987": ler("pauta_tvc1.html"),
        "formNotaParcial/988": ler("pauta_tvc2.html"),
        "formNotaParcial/989": ler("pauta_tvc10.html"),
        "formNotaParcialPasso1": ler("passo1_periodos.html"),
        "formNotaParcialObject/12345": ler("turma_com_quadro.html"),
        "formQuadroGeral": ler("quadro_geral.html"),
        "formTVCFind": ler("tvc_grid.html"),
        "buscaTurmas": ler("passo2_turmas.html"),
    }


@pytest.fixture
def paginas_sem_quadro_geral():
    return {
        "formNotaParcial/987": ler("pauta_tvc1.html"),
        "formNotaParcial/988": ler("pauta_tvc2.html"),
        "formNotaParcial/989": ler("pauta_tvc10.html"),
        "formNotaParcialPasso1": ler("passo1_periodos.html"),
        "formNotaParcialObject/12345": ler("turma_sem_quadro.html"),
        "formTVCFind": ler("tvc_grid.html"),
        "buscaTurmas": ler("passo2_turmas.html"),
    }


@pytest.fixture
def siga_quadro_geral(paginas_quadro_geral):
    return SigaFake(paginas_quadro_geral)


@pytest.fixture
def siga_sem_quadro_geral(paginas_sem_quadro_geral):
    return SigaFake(paginas_sem_quadro_geral)
