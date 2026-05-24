import os
import threading
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import xml.etree.ElementTree as ET


def ajustes_incluir_carac_especial(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    caminho_excel: str,
    valor_alvo: str,
    app_logger=None,
    progress=None,
):
    logger = app_logger or logging.getLogger(__name__)
    pasta_xmls_path = Path(pasta_xmls)

    if not pasta_xmls_path.is_dir():
        logger.error(f"Pasta de XMLs inválida: {pasta_xmls}")
        return
    if not os.path.isfile(caminho_excel):
        logger.error(f"Planilha Excel não encontrada: {caminho_excel}")
        return

    def carregar_ipocs(caminho: str, sheet=0, coluna_ipoc="IPOC"):
        df = pd.read_excel(caminho, sheet_name=sheet, dtype={coluna_ipoc: str})
        ipocs = set()
        for v in df[coluna_ipoc].dropna().astype(str):
            v_norm = v.strip()
            if v_norm:
                ipocs.add(v_norm)
        return ipocs

    def _tem_valor(lista, alvo):
        return any(x == alvo for x in lista)

    def _split_carac(valor_attr):
        partes = []
        if not valor_attr:
            return partes
        for p in str(valor_attr).split(";"):
            p = (p or "").strip()
            if p and p not in partes:
                partes.append(p)
            return partes

    try:
        ipocs_alvo = carregar_ipocs(caminho_excel, sheet=0, coluna_ipoc="IPOC")
    except Exception as e:
        logger.error(f"Falha ao carregar IPOCs do Excel '{caminho_excel}': {e}")
        return

    if not ipocs_alvo:
        logger.warning("A lista de IPOCs do Excel está vazia. Verifique a coluna 'IPOC' e a primeira aba.")
        return

    xml_files = [f for f in os.listdir(pasta_xmls_path) if f.lower().endswith(".xml")]
    if not xml_files:
        logger.info(f"Nenhum XML encontrado em {pasta_xmls_path}")
        return

    logger.info(
        f"Incluindo característica especial '{valor_alvo}' em {len(ipocs_alvo)} IPOC(s) "
        f"e {len(xml_files)} arquivo(s) XML."
    )

    avisos_gerais = []
    erros = []
    total = len(xml_files)

    for i, nome_arquivo in enumerate(sorted(xml_files), start=1):
        if cancel_flag.is_set():
            logger.info("Processo de inclusão de característica especial cancelado pelo usuário.")
            break
        caminho_xml = pasta_xmls_path / nome_arquivo

        try:
            tree = ET.parse(str(caminho_xml))
            root = tree.getroot()
        except ET.ParseError as e:
            msg = f"Erro ao processar {nome_arquivo}: erro de parsing XML: {e}"
            logger.warning(msg)
            erros.append(msg)
            continue
        except Exception as e:
            msg = f"Erro inesperado em {nome_arquivo}: {e}"
            logger.warning(msg)
            erros.append(msg)
            continue

        alterou_arquivo = False
        for op in root.findall(".//Op"):
            ipoc = (op.get("IPOC") or "").strip()
            if not ipoc or ipoc not in ipocs_alvo:
                continue
            atual = op.get("CaracEspecial", "")
            itens = _split_carac(atual)

            if _tem_valor(itens, valor_alvo):
                msg = f"[{nome_arquivo}] IPOC={ipoc}: já possuía {valor_alvo} (CaracEspecial={';'.join(itens)})"
                avisos_gerais.append(msg)
                logger.info(msg)
                continue

            itens.append(valor_alvo)
            novo = ";".join(itens) if itens else valor_alvo
            op.set("CaracEspecial", novo)
            alterou_arquivo = True
            msg = (
                f"[{nome_arquivo}] IPOC={ipoc}: acrescentado {valor_alvo} -> {novo}"
                if atual
                else f"[{nome_arquivo}] IPOC={ipoc}: criado CaracEspecial={novo}"
            )
            avisos_gerais.append(msg)
            logger.info(msg)

        if alterou_arquivo:
            tree.write(str(caminho_xml), encoding="utf-8", xml_declaration=True)

        if progress is not None:
            try:
                progress(int(i * 100 / total))
            except Exception:
                pass

    if avisos_gerais or erros:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            caminho_avisos = pasta_xmls_path / f"avisos_caracespecial_{ts}.txt"
            with open(caminho_avisos, "w", encoding="utf-8") as f:
                for a in avisos_gerais:
                    f.write(a + "\n")
            logger.info(f"{len(avisos_gerais)} avisos gerados. Log em: {caminho_avisos}")
        except Exception as e:
            logger.warning(f"Falha ao escrever avisos_caracespecial: {e}")

        if erros:
            try:
                caminho_erros = pasta_xmls_path / f"erros_caracespecial_{ts}.txt"
                with open(caminho_erros, "w", encoding="utf-8") as f:
                    for e in erros:
                        f.write(e + "\n")
                logger.warning(f"{len(erros)} arquivo(s) apresentaram erro. Detalhes em: {caminho_erros}")
            except Exception as e:
                logger.warning(f"Falha ao escrever erros_caracespecial: {e}")
    else:
        logger.info("Nenhuma alteração realizada em CaracEspecial.")
