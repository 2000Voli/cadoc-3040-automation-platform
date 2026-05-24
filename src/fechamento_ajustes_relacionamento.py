import os
import shutil
import threading
import logging
from datetime import datetime
from pathlib import Path

import xml.etree.ElementTree as ET


def ajustes_ajusta_inicio_relacionamento(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    pasta_saida: str,
    detect_encoding,
    app_logger=None,
    progress=None,
):
    """Ajusta IniRelactCli para menor DtContr de cada cliente."""
    logger = app_logger or logging.getLogger(__name__)

    def _parse_date(s: str):
        if not s:
            return None
        s = str(s).strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        return None

    pasta_xmls_path = Path(pasta_xmls)
    pasta_saida_path = Path(pasta_saida)
    if not pasta_xmls_path.is_dir():
        logger.error(f"Pasta inválida: {pasta_xmls_path}")
        return

    xml_files = [f for f in os.listdir(pasta_xmls_path) if f.lower().endswith(".xml")]
    if not xml_files:
        logger.info("Nenhum XML encontrado para ajustar início de relacionamento.")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = pasta_saida_path / f"ajusta_inicio_rel_backup_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Iniciando ajuste de início de relacionamento em {len(xml_files)} arquivo(s)…")
    total_cli_ajustados = 0
    total_arqs_alterados = 0
    total = len(xml_files)

    for i, xml_name in enumerate(sorted(xml_files), start=1):
        if cancel_flag.is_set():
            logger.info("Ajuste cancelado pelo usuário.")
            return

        caminho_xml = pasta_xmls_path / xml_name
        try:
            tree = ET.parse(str(caminho_xml))
            root = tree.getroot()
        except ET.ParseError as e:
            logger.warning(f"[{i}/{total}] Falha ao abrir/parsing: {xml_name} — {e}")
            continue

        alterado_no_arquivo = False
        cli_ajustados_no_arquivo = 0

        for cli in root.findall("Cli"):
            ini_rel_str = cli.attrib.get("IniRelactCli") or ""
            ini_rel_dt = _parse_date(ini_rel_str)

            min_dtcontr_dt = None
            min_dtcontr_str = None
            for op in cli.findall("Op"):
                dtc_str = op.attrib.get("DtContr") or ""
                dtc_dt = _parse_date(dtc_str)
                if dtc_dt is not None and (min_dtcontr_dt is None or dtc_dt < min_dtcontr_dt):
                    min_dtcontr_dt = dtc_dt
                    min_dtcontr_str = dtc_str

            if min_dtcontr_dt is not None and (ini_rel_dt is None or ini_rel_dt > min_dtcontr_dt):
                cli.set("IniRelactCli", min_dtcontr_str)
                alterado_no_arquivo = True
                cli_ajustados_no_arquivo += 1
                logger.info(f"  • Cli Cd={cli.attrib.get('Cd', '?')}: IniRelactCli {ini_rel_str or '∅'} → {min_dtcontr_str}")

        if alterado_no_arquivo:
            try:
                shutil.copy2(str(caminho_xml), str(backup_dir / xml_name))
            except Exception as e:
                logger.warning(f"Não consegui fazer backup de {xml_name}: {e}")

            enc = detect_encoding(caminho_xml, default="ISO-8859-1")
            try:
                tree.write(str(caminho_xml), encoding=enc or "ISO-8859-1", xml_declaration=True)
                total_arqs_alterados += 1
                total_cli_ajustados += cli_ajustados_no_arquivo
                logger.info(f"[{i}/{total}] {xml_name} — {cli_ajustados_no_arquivo} cliente(s) ajustado(s).")
            except Exception as e:
                logger.error(f"Falha ao escrever {xml_name}: {e}")
        else:
            logger.info(f"[{i}/{total}] {xml_name} — OK (sem ajustes).")

        if progress is not None:
            try:
                progress(int(i * 100 / total))
            except Exception:
                pass

    logger.info(f"Concluído. Arquivos alterados: {total_arqs_alterados} | Clientes ajustados: {total_cli_ajustados}.")
    logger.info(f"Backup dos originais em: {backup_dir}")
