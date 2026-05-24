import json
import os
import tempfile
import threading
import logging
from pathlib import Path

import xml.etree.ElementTree as ET
from openpyxl import Workbook


def ajustes_valida_composicao_ipoc(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    pasta_saida: str,
    app_logger=None,
    progress=None,
):
    """Valida a composição do IPOC e exporta divergências em Excel/TXT."""
    logger = app_logger or logging.getLogger(__name__)

    def ajustar_codigo_cliente(tp: str, cd_cliente: str) -> str:
        cd_cliente = (cd_cliente or "").strip()
        if tp == "1":
            return cd_cliente.zfill(11)[-11:]
        if tp == "2":
            return cd_cliente.zfill(8)[-8:]
        return cd_cliente

    def montar_ipoc_correto(cnpj_raiz: str, modalidade: str, tp: str, cd_cliente: str, contrato: str) -> str:
        codigo_cliente_ajustado = ajustar_codigo_cliente(tp, cd_cliente)
        return f"{cnpj_raiz}{modalidade}{tp}{codigo_cliente_ajustado}{contrato}"

    def remover_namespace(tag: str) -> str:
        return tag.split("}", 1)[1] if "}" in tag else tag

    def diagnosticar_divergencia(
        ipoc_xml: str,
        cnpj_raiz: str,
        modalidade: str,
        tp: str,
        cd_cliente: str,
        contrato: str,
    ) -> str:
        codigo_cliente_ajustado = ajustar_codigo_cliente(tp, cd_cliente)
        ipoc_correto = montar_ipoc_correto(cnpj_raiz, modalidade, tp, cd_cliente, contrato)

        if ipoc_xml == ipoc_correto:
            return "Sem divergência"
        if ipoc_xml == ipoc_correto + contrato:
            return "IPOC com contrato repetido no final"

        partes_divergentes = []
        tam_cnpj = len(cnpj_raiz)
        tam_modalidade = len(modalidade)
        tam_tp = len(tp)
        tam_cd_cliente = len(codigo_cliente_ajustado)
        tam_contrato = len(contrato)

        pos = 0
        bloco_cnpj_xml = ipoc_xml[pos:pos + tam_cnpj]
        pos += tam_cnpj
        bloco_modalidade_xml = ipoc_xml[pos:pos + tam_modalidade]
        pos += tam_modalidade
        bloco_tp_xml = ipoc_xml[pos:pos + tam_tp]
        pos += tam_tp
        bloco_cd_cliente_xml = ipoc_xml[pos:pos + tam_cd_cliente]
        pos += tam_cd_cliente
        bloco_contrato_xml = ipoc_xml[pos:pos + tam_contrato]

        if bloco_cnpj_xml != cnpj_raiz:
            partes_divergentes.append("CNPJ")
        if bloco_modalidade_xml != modalidade:
            partes_divergentes.append("modalidade")
        if bloco_tp_xml != tp:
            partes_divergentes.append("tipo do cliente")
        if bloco_cd_cliente_xml != codigo_cliente_ajustado:
            partes_divergentes.append("código do cliente")
        if bloco_contrato_xml != contrato:
            partes_divergentes.append("contrato")

        comprimento_esperado = len(ipoc_correto)
        if len(ipoc_xml) != comprimento_esperado:
            sobra = ipoc_xml[comprimento_esperado:]
            if sobra == contrato:
                return "IPOC com contrato repetido no final"
            if partes_divergentes:
                return f"{', '.join(partes_divergentes)} divergente(s) e comprimento do IPOC divergente"
            return "Comprimento do IPOC divergente"

        if not partes_divergentes:
            return "Outro problema na composição"
        if len(partes_divergentes) == 1:
            return f"{partes_divergentes[0].capitalize()} divergente"
        if len(partes_divergentes) == 2:
            return f"{partes_divergentes[0].capitalize()} e {partes_divergentes[1]} divergentes"
        return f"Divergências em: {', '.join(partes_divergentes)}"

    def iterar_divergencias_xml(xml_path: Path):
        contexto = ET.iterparse(xml_path, events=("start", "end"))
        cnpj_raiz = ""
        tp_atual = ""
        cd_cliente_atual = ""

        for evento, elem in contexto:
            tag = remover_namespace(elem.tag)
            if evento == "start":
                if tag == "Doc3040":
                    cnpj_raiz = (elem.attrib.get("CNPJ") or "").strip()
                elif tag == "Cli":
                    tp_atual = (elem.attrib.get("Tp") or "").strip()
                    cd_cliente_atual = (elem.attrib.get("Cd") or "").strip()
                elif tag == "Op":
                    ipoc_xml = (elem.attrib.get("IPOC") or "").strip()
                    contrato = (elem.attrib.get("Contrt") or "").strip()
                    modalidade = (elem.attrib.get("Mod") or "").strip()

                    ipoc_correto = montar_ipoc_correto(
                        cnpj_raiz=cnpj_raiz,
                        modalidade=modalidade,
                        tp=tp_atual,
                        cd_cliente=cd_cliente_atual,
                        contrato=contrato,
                    )
                    if ipoc_xml != ipoc_correto:
                        yield {
                            "arquivo_xml": xml_path.name,
                            "contrato": contrato,
                            "ipoc_xml": ipoc_xml,
                            "ipoc_correto": ipoc_correto,
                            "descricao_divergencia": diagnosticar_divergencia(
                                ipoc_xml=ipoc_xml,
                                cnpj_raiz=cnpj_raiz,
                                modalidade=modalidade,
                                tp=tp_atual,
                                cd_cliente=cd_cliente_atual,
                                contrato=contrato,
                            ),
                        }
            if evento == "end":
                elem.clear()

    def salvar_em_excel_from_jsonl(arquivo_jsonl: Path, arquivo_saida: Path) -> None:
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Divergencias_IPOC")
        ws.append([
            "Arquivo XML",
            "Contrato",
            "IPOC contido no XML",
            "IPOC correto",
            "O que está diferente",
        ])

        with open(arquivo_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                reg = json.loads(line)
                ws.append([
                    reg.get("arquivo_xml", ""),
                    reg.get("contrato", ""),
                    reg.get("ipoc_xml", ""),
                    reg.get("ipoc_correto", ""),
                    reg.get("descricao_divergencia", ""),
                ])

        wb.save(arquivo_saida)

    def salvar_em_txt_from_jsonl(arquivo_jsonl: Path, arquivo_saida: Path) -> None:
        with open(arquivo_saida, "w", encoding="utf-8") as out_f:
            out_f.write("ARQUIVO_XML|CONTRATO|IPOC_XML|IPOC_CORRETO|O_QUE_ESTA_DIFERENTE\n")
            with open(arquivo_jsonl, "r", encoding="utf-8") as in_f:
                for line in in_f:
                    if not line.strip():
                        continue
                    reg = json.loads(line)
                    out_f.write(
                        f"{reg.get('arquivo_xml', '')}|{reg.get('contrato', '')}|{reg.get('ipoc_xml', '')}|"
                        f"{reg.get('ipoc_correto', '')}|{reg.get('descricao_divergencia', '')}\n"
                    )

    pasta_entrada = Path(pasta_xmls)
    pasta_destino = Path(pasta_saida)
    pasta_destino.mkdir(parents=True, exist_ok=True)

    if not pasta_entrada.is_dir():
        logger.error(f"Pasta de XMLs inválida: {pasta_entrada}")
        return

    arquivos_xml = sorted(pasta_entrada.glob("*.xml"))
    if not arquivos_xml:
        logger.info("Nenhum XML encontrado na pasta informada.")
        return

    logger.info(f"Validando composição do IPOC em {len(arquivos_xml)} XML(s)…")

    fd_tmp, tmp_path = tempfile.mkstemp(prefix="ipoc_divergencias_", suffix=".jsonl")
    os.close(fd_tmp)
    arquivo_tmp = Path(tmp_path)

    total_divergencias = 0
    total = len(arquivos_xml)
    try:
        with open(arquivo_tmp, "w", encoding="utf-8") as ftmp:
            for i, xml_file in enumerate(arquivos_xml, start=1):
                if cancel_flag.is_set():
                    logger.info("Validação de IPOC cancelada pelo usuário.")
                    return
                logger.info(f"[{i}/{total}] Processando: {xml_file.name}")
                try:
                    for divergencia in iterar_divergencias_xml(xml_file):
                        ftmp.write(json.dumps(divergencia, ensure_ascii=False) + "\n")
                        total_divergencias += 1
                except ET.ParseError as e:
                    logger.warning(f"Erro de parse no arquivo {xml_file.name}: {e}")
                except Exception as e:
                    logger.warning(f"Erro inesperado no arquivo {xml_file.name}: {e}")

                if progress is not None:
                    try:
                        progress(int(i * 100 / total))
                    except Exception:
                        pass

        logger.info(f"Total de divergências encontradas: {total_divergencias}")
        if total_divergencias == 0:
            logger.info("Nenhuma divergência de IPOC encontrada.")
            return

        limite_linhas_excel = 1_048_576
        if total_divergencias + 1 <= limite_linhas_excel:
            arquivo_excel = pasta_destino / "ipocs_incorretos.xlsx"
            salvar_em_excel_from_jsonl(arquivo_tmp, arquivo_excel)
            logger.info(f"Relatório gerado: {arquivo_excel}")
            return

        arquivo_txt = pasta_destino / "ipocs_incorretos.txt"
        salvar_em_txt_from_jsonl(arquivo_tmp, arquivo_txt)
        logger.info(f"Arquivo gerado: {arquivo_txt}")
    finally:
        try:
            if arquivo_tmp.exists():
                arquivo_tmp.unlink()
        except Exception:
            pass
