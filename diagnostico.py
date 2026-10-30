#!/usr/bin/env python3
"""Diagnóstico do parser do SIGA (uso local, linha de comando).

Faz login com as credenciais do ``.env``, consulta o quadro de notas de uma
turma, imprime o JSON e — o mais importante — salva em disco cada HTML
recebido do SIGA. Com esses arquivos é possível ajustar o parser quando o SIGA
mudar o layout, sem precisar abrir o navegador.

Uso::

    python diagnostico.py CBV018GV-A 2026/3
    python diagnostico.py CBV018GV-A 2026/3 --saida /tmp/siga_html

Atenção: os HTMLs salvos contêm **dados pessoais de alunos** (nomes,
matrículas e notas). Não versione a pasta de saída.
"""

import argparse
import json
import os
import sys

from siga_bot import SigaAPI, SigaError, SigaNaoEncontrado

try:  # credenciais do .env, quando python-dotenv está instalado
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico do parser do SIGA/UFJF.")
    parser.add_argument("turma", help="Código da turma, ex.: CBV018GV-A")
    parser.add_argument("periodo", help="Período letivo, ex.: 2026/3 (ou o ID interno)")
    parser.add_argument(
        "--saida",
        default="debug_html",
        help="Pasta onde os HTMLs serão salvos (padrão: debug_html)",
    )
    arguments = parser.parse_args()

    cpf = (os.getenv("SIGA_CPF") or "").strip()
    senha = os.getenv("SIGA_SENHA") or ""
    if not cpf or not senha:
        print(
            "Configure SIGA_CPF e SIGA_SENHA (arquivo .env ou variáveis de ambiente).",
            file=sys.stderr,
        )
        return 2

    os.environ["SIGA_DEBUG_DIR"] = arguments.saida
    siga = SigaAPI()
    try:
        siga.login(cpf=cpf, senha=senha)
        quadro = siga.obter_quadro_notas(turma=arguments.turma, periodo=arguments.periodo)
        print(json.dumps(quadro, ensure_ascii=False, indent=2))
        print(
            f"\n{len(siga.urls_consultadas)} requisição(ões); HTMLs salvos em "
            f"'{arguments.saida}'.",
            file=sys.stderr,
        )
        if not quadro["alunos"]:
            print(
                "Nenhum aluno reconhecido: envie os HTMLs dessa pasta para ajustar o parser.",
                file=sys.stderr,
            )
        return 0
    except (SigaNaoEncontrado, SigaError) as erro:
        print(f"Falha: {erro}", file=sys.stderr)
        return 1
    finally:
        siga.fechar()


if __name__ == "__main__":
    raise SystemExit(main())
