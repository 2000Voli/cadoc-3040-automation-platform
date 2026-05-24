import threading
import logging
from datetime import datetime
from pathlib import Path

import xml.etree.ElementTree as ET


def ajustes_renomear_xmls_por_fidc(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    fidc_reg,
    prefixo: str = "NC",
    app_logger=None,
    progress=None,
):
    logger = app_logger or logging.getLogger(__name__)
    pasta = Path(pasta_xmls)
    if not pasta.is_dir():
        logger.error(f"Pasta inválida para renomeação: {pasta_xmls}")
        return

    xml_files = [p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".xml"]
    if not xml_files:
        logger.info(f"Nenhum XML encontrado em {pasta}")
        return

    logger.info(f"Renomeando {len(xml_files)} XML(s) com base no CNPJ raiz e FIDCs cadastrados…")
    total = len(xml_files)
    renomeados = 0
    sem_fidc = []
    erros = []
    eventos_log = []

    for i, caminho_xml in enumerate(sorted(xml_files), start=1):
        if cancel_flag.is_set():
            logger.info("Renomeação cancelada pelo usuário.")
            break

        nome_antigo = caminho_xml.name
        try:
            tree = ET.parse(str(caminho_xml))
            root = tree.getroot()
        except ET.ParseError as e:
            msg = f"[ERRO XML] {nome_antigo}: erro de parsing ({e})"
            logger.warning(msg)
            erros.append(msg)
            continue
        except Exception as e:
            msg = f"[ERRO] {nome_antigo}: {e}"
            logger.warning(msg)
            erros.append(msg)
            continue

        cnpj_raiz = (root.get("CNPJ") or "").strip()
        if not cnpj_raiz:
            msg = f"[SEM CNPJ] {nome_antigo}: atributo CNPJ não encontrado na raiz Doc3040."
            logger.info(msg)
            sem_fidc.append(msg)
            continue

        item = fidc_reg.get_by_cnpj_root(cnpj_raiz) if hasattr(fidc_reg, "get_by_cnpj_root") else None
        if not item:
            msg = f"[FIDC NÃO ENCONTRADO] {nome_antigo}: CNPJ raiz={cnpj_raiz} não mapeado em FIDCs cadastrados."
            logger.info(msg)
            sem_fidc.append(msg)
            continue

        fid = item.get("id")
        if fid is None:
            msg = f"[ID AUSENTE] {nome_antigo}: registro FIDC sem ID (CNPJ raiz={cnpj_raiz})."
            logger.info(msg)
            sem_fidc.append(msg)
            continue

        try:
            fid_int = int(fid)
        except Exception:
            msg = f"[ID INVÁLIDO] {nome_antigo}: ID={fid!r} não numérico."
            logger.info(msg)
            sem_fidc.append(msg)
            continue

        novo_nome = f"{prefixo}{fid_int}.xml"
        caminho_novo = pasta / novo_nome
        if caminho_novo == caminho_xml:
            msg = f"[OK] {nome_antigo} já está nomeado como {novo_nome}."
            logger.info(msg)
            eventos_log.append(msg)
        elif caminho_novo.exists():
            msg = f"[CONFLITO] {nome_antigo} → {novo_nome}: já existe um arquivo com esse nome. Não renomeado."
            logger.warning(msg)
            erros.append(msg)
        else:
            try:
                caminho_xml.rename(caminho_novo)
                renomeados += 1
                msg = f"[RENOMEADO] {nome_antigo} → {novo_nome} (CNPJ raiz={cnpj_raiz}, ID={fid_int})"
                logger.info(msg)
                eventos_log.append(msg)
            except Exception as e:
                msg = f"[ERRO RENOMEAR] {nome_antigo} → {novo_nome}: {e}"
                logger.error(msg)
                erros.append(msg)

        if progress is not None:
            try:
                progress(int(i * 100 / total))
            except Exception:
                pass

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        log_path = pasta / f"log_renomeacao_nc_{ts}.txt"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"Total XMLs analisados: {total}\n")
            f.write(f"Arquivos renomeados: {renomeados}\n")
            f.write(f"Sem FIDC / problemas de ID: {len(sem_fidc)}\n")
            f.write(f"Erros: {len(erros)}\n\n")
            f.write("=== RENOMEADOS / OK ===\n")
            for ev in eventos_log:
                f.write(ev + "\n")
            f.write("\n=== SEM FIDC / ID PROBLEMA ===\n")
            for ev in sem_fidc:
                f.write(ev + "\n")
            f.write("\n=== ERROS ===\n")
            for ev in erros:
                f.write(ev + "\n")

        logger.info(f"Log de renomeação salvo em: {log_path}")
    except Exception as e:
        logger.warning(f"Não foi possível salvar log de renomeação: {e}")

    logger.info(
        f"Renomeação concluída. Renomeados: {renomeados} | "
        f"Sem FIDC/ID: {len(sem_fidc)} | Erros: {len(erros)}."
    )
