import os
import threading
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import xml.etree.ElementTree as ET


def ajustes_filtrar_operacoes_por_contratos(
    cancel_flag: threading.Event,
    caminho_xml: str,
    caminho_excel: str,
    pasta_saida: str,
    inf_tp: str,
    detect_encoding,
    app_logger=None,
    progress=None,
):
    logger = app_logger or logging.getLogger(__name__)
    atributos_para_remover = {"DtaProxParcela", "VlrProxParcela", "QtdParcelas", "ProvConsttd"}

    if not os.path.isfile(caminho_xml):
        logger.error(f"XML não encontrado: {caminho_xml}")
        return
    if not os.path.isfile(caminho_excel):
        logger.error(f"Planilha Excel não encontrada: {caminho_excel}")
        return
    if inf_tp not in {"0301", "0302", "0399"}:
        logger.error(f"Valor de 'Tp' inválido: {inf_tp} (use 0301, 0302 ou 0399).")
        return
    try:
        os.makedirs(pasta_saida, exist_ok=True)
    except Exception as e:
        logger.error(f"Não foi possível criar a pasta de saída: {e}")
        return

    try:
        df = pd.read_excel(caminho_excel)
    except Exception as e:
        logger.error(f"Falha ao ler Excel: {e}")
        return

    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    col_contrt = None
    for cand in ("contrt", "contrato", "contratos", "idcontrato"):
        if cand in cols_lower:
            col_contrt = cols_lower[cand]
            break
    if not col_contrt:
        for c in df.columns:
            if "contrt" in str(c).lower():
                col_contrt = c
                break
    if not col_contrt:
        logger.error("A planilha precisa conter uma coluna de contratos (ex.: 'Contrt').")
        return

    contratos = set(df[col_contrt].astype(str).str.strip())
    if not contratos:
        logger.info("Nenhum contrato encontrado na planilha. Nada a fazer.")
        return

    logger.info(f"Total de contratos na planilha: {len(contratos)}")
    xml_path = Path(caminho_xml)
    enc = detect_encoding(xml_path, default="ISO-8859-1")

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except ET.ParseError as e:
        logger.error(f"Erro ao abrir/parsing XML: {e}")
        return

    novo_root = ET.Element(root.tag, root.attrib)
    mapa_cli = {}
    total_ops_entrada = 0
    total_ops_saida = 0
    clis = root.findall("Cli")
    total_clis = len(clis) or 1

    for idx, cli in enumerate(clis, start=1):
        if cancel_flag.is_set():
            logger.info("Processo cancelado pelo usuário.")
            return
        cd = (cli.attrib.get("Cd") or "").strip()
        tp = (cli.attrib.get("Tp") or "").strip()
        chave = (cd, tp)

        novo_cli = mapa_cli.get(chave)
        if novo_cli is None:
            novo_cli = ET.Element("Cli", cli.attrib)
            mapa_cli[chave] = novo_cli

        for op in cli.findall("Op"):
            total_ops_entrada += 1
            contrt = (op.attrib.get("Contrt") or "").strip()
            if contrt in contratos:
                attrs_filtrados = {k: v for k, v in op.attrib.items() if k not in atributos_para_remover}
                nova_op = ET.Element("Op", attrs_filtrados)
                nova_op.append(ET.Element("Inf", {"Tp": inf_tp}))
                novo_cli.append(nova_op)
                total_ops_saida += 1

        if progress is not None:
            try:
                progress(int(idx * 100 / total_clis))
            except Exception:
                pass

    for cli_unico in mapa_cli.values():
        if list(cli_unico):
            novo_root.append(cli_unico)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"{xml_path.stem}_com_saida_{inf_tp}_{ts}.xml"
    out_path = Path(pasta_saida) / out_name

    try:
        nova_tree = ET.ElementTree(novo_root)
        nova_tree.write(out_path, encoding=enc or "utf-8", xml_declaration=True)
        logger.info(f"Arquivo gerado: {out_path}")
        logger.info(f"Operações lidas: {total_ops_entrada} | Operações mantidas: {total_ops_saida}")
    except Exception as e:
        logger.error(f"Falha ao escrever XML de saída: {e}")
