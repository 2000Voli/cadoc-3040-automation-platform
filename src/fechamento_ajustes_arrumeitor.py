import json
import os
import re
import shutil
import threading
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import xml.etree.ElementTree as ET


def ajustes_arrumeitor(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    pasta_saida: str,
    regras_condicionais=None,
    rules_default=None,
    detect_encoding=None,
    app_logger=None,
    progress=None,
):
    logger = app_logger or logging.getLogger(__name__)
    regras = regras_condicionais if (regras_condicionais and len(regras_condicionais) > 0) else (rules_default or [])
    attr_rules = [r for r in regras if r.get("type", "attr") != "regex"]
    regex_rules = [r for r in regras if r.get("type") == "regex"]

    pasta_xmls_path = Path(pasta_xmls)
    pasta_saida_path = Path(pasta_saida)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = pasta_saida_path / f"arrumeitor_backup_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    def _match_all(attrs: dict, cond: dict) -> bool:
        return all(attrs.get(k) == v for k, v in (cond or {}).items())

    def _aplicar_regras_atributos(caminho_xml: Path):
        eventos = []
        if not attr_rules:
            return False, eventos
        try:
            tree = ET.parse(str(caminho_xml))
            root = tree.getroot()
        except ET.ParseError as e:
            logger.warning(f"Falha ao abrir/parsing: {caminho_xml.name} — {e}")
            return False, eventos

        alterado = False
        for reg in attr_rules:
            cond, sub = reg.get("condicoes", {}), reg.get("substituicoes", {})
            if _match_all(root.attrib, cond):
                for k, v in sub.items():
                    root.set(k, v)
                alterado = True
                eventos.append({"tipo": "attr", "escopo": "Root", "regra": reg.get("id", ""), "label": reg.get("label", ""),
                                "condicoes": cond, "substituicoes": sub})

        for cli in root.findall("Cli"):
            for op in cli.findall("Op"):
                for reg in attr_rules:
                    cond, sub = reg.get("condicoes", {}), reg.get("substituicoes", {})
                    if _match_all(op.attrib, cond):
                        for k, v in sub.items():
                            op.set(k, v)
                        alterado = True
                        eventos.append({"tipo": "attr", "escopo": "Op", "regra": reg.get("id", ""), "label": reg.get("label", ""),
                                        "condicoes": cond, "substituicoes": sub})

        for ag in root.findall("Agreg"):
            for reg in attr_rules:
                cond, sub = reg.get("condicoes", {}), reg.get("substituicoes", {})
                if _match_all(ag.attrib, cond):
                    for k, v in sub.items():
                        ag.set(k, v)
                    alterado = True
                    eventos.append({"tipo": "attr", "escopo": "Agreg", "regra": reg.get("id", ""), "label": reg.get("label", ""),
                                    "condicoes": cond, "substituicoes": sub})

        if alterado:
            enc = detect_encoding(caminho_xml) if detect_encoding else "ISO-8859-1"
            tree.write(str(caminho_xml), encoding=enc or "ISO-8859-1", xml_declaration=True)
        return alterado, eventos

    def _aplicar_regras_regex(caminho_xml: Path):
        eventos = []
        if not regex_rules:
            return False, eventos

        enc = detect_encoding(caminho_xml) if detect_encoding else "ISO-8859-1"
        try:
            txt = caminho_xml.read_text(encoding=enc, errors="ignore")
        except Exception as e:
            logger.warning(f"Não consegui ler {caminho_xml.name} para regex: {e}")
            return False, eventos

        novo = txt
        alterado = False
        for reg in regex_rules:
            patt = reg.get("pattern", "")
            repl = reg.get("repl", "")
            flags_list = reg.get("flags", []) or []
            flags = 0
            for fl in flags_list:
                flags |= getattr(re, fl, 0)
            try:
                rx = re.compile(patt, flags)
                novo, n = rx.subn(repl, novo)
            except Exception as e:
                logger.warning(f"Regex inválido em '{reg.get('label', '')}': {e}")
                continue
            if n > 0:
                alterado = True
                eventos.append({
                    "tipo": "regex",
                    "escopo": "Texto",
                    "regra": reg.get("id", ""),
                    "label": reg.get("label", ""),
                    "pattern": patt,
                    "repl": repl,
                    "matches": n,
                })

        if alterado and novo != txt:
            try:
                caminho_xml.write_text(novo, encoding=enc)
            except Exception as e:
                logger.warning(f"Falha ao escrever {caminho_xml.name} pós-regex: {e}")
                alterado = False
                eventos.clear()
        return alterado, eventos

    xml_files = [f for f in os.listdir(pasta_xmls_path) if f.lower().endswith(".xml")]
    if not xml_files:
        logger.info("Nenhum XML encontrado na pasta informada para o Arrumeitor.")
        return

    logger.info(f"Arrumaitor: analisando {len(xml_files)} arquivo(s)…")
    rel_rows, eventos_all = [], []
    total_alterados = 0

    total = len(xml_files)
    for i, xml_name in enumerate(sorted(xml_files), start=1):
        if cancel_flag.is_set():
            logger.info("Arrumaitor cancelado pelo usuário.")
            break

        caminho_xml = pasta_xmls_path / xml_name
        logger.info(f"[{i}/{total}] {xml_name}")

        alterado_attr, ev_attr = _aplicar_regras_atributos(caminho_xml)
        alterado_regex, ev_regex = _aplicar_regras_regex(caminho_xml)
        alterado = alterado_attr or alterado_regex
        if alterado:
            try:
                shutil.copy2(str(caminho_xml), str(backup_dir / xml_name))
            except Exception as e:
                logger.warning(f"Não consegui fazer backup de {xml_name}: {e}")
            total_alterados += 1

        eventos = ev_attr + ev_regex
        label_set = sorted({(e.get("label") or e.get("regra") or "") for e in eventos}) if eventos else []
        rel_rows.append({
            "Arquivo": xml_name,
            "Alterado": "Sim" if alterado else "Não",
            "Qtde_Ajustes": len(eventos),
            "Regras_Aplicadas": "; ".join([s for s in label_set if s]),
        })

        for e in eventos:
            if e.get("tipo") == "regex":
                eventos_all.append({
                    "Arquivo": xml_name,
                    "Escopo": e.get("escopo", ""),
                    "Regra_ID": e.get("regra", ""),
                    "Regra_Label": e.get("label", ""),
                    "Pattern": e.get("pattern", ""),
                    "Repl": e.get("repl", ""),
                    "Matches": e.get("matches", 0),
                })
            else:
                eventos_all.append({
                    "Arquivo": xml_name,
                    "Escopo": e.get("escopo", ""),
                    "Regra_ID": e.get("regra", ""),
                    "Regra_Label": e.get("label", ""),
                    "Condicoes": json.dumps(e.get("condicoes", {}), ensure_ascii=False),
                    "Substituicoes": json.dumps(e.get("substituicoes", {}), ensure_ascii=False),
                })

        if progress is not None:
            try:
                progress(int(i * 100 / total))
            except Exception:
                pass

    if rel_rows:
        out = pasta_saida_path / f"arrumeitor_relatorio_{ts}.xlsx"
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            df_arq = pd.DataFrame(rel_rows, columns=["Arquivo", "Alterado", "Qtde_Ajustes", "Regras_Aplicadas"])
            df_arq.to_excel(writer, sheet_name="Arquivos", index=False)
            ws1 = writer.sheets["Arquivos"]
            ws1.set_column(0, 0, 40)
            ws1.set_column(1, 1, 10)
            ws1.set_column(2, 2, 14)
            ws1.set_column(3, 3, 60)
            if not df_arq.empty and len(df_arq.columns) > 0:
                ws1.autofilter(0, 0, len(df_arq), len(df_arq.columns) - 1)

            if eventos_all:
                df_evt = pd.DataFrame(eventos_all)
                df_evt.to_excel(writer, sheet_name="Eventos", index=False)
                ws2 = writer.sheets["Eventos"]
                ws2.set_column(0, 0, 40)
                ws2.set_column(1, 1, 10)
                ws2.set_column(2, 3, 18)
                if len(df_evt.columns) >= 5:
                    ws2.set_column(4, len(df_evt.columns) - 1, 50)
                if not df_evt.empty and len(df_evt.columns) > 0:
                    ws2.autofilter(0, 0, len(df_evt), len(df_evt.columns) - 1)

        logger.info(f"Arrumeitor: {total_alterados} arquivo(s) ajustado(s). Relatório: {out}")
        logger.info(f"Backup dos originais em: {backup_dir}")
    else:
        logger.info("Arrumeitor: nenhum ajuste necessário.")
