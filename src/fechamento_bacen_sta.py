import os
import shutil
import subprocess
import threading
import time
import hashlib
import logging
from datetime import datetime

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,
    StaleElementReferenceException,
)


def bacen_valida_xmls(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    caminho_validador: str,
    pasta_saida: str,
    app_logger=None,
    progress=None,
):
    logger = app_logger or logging.getLogger(__name__)

    caminho_validador = os.path.normpath(caminho_validador)
    if os.path.isfile(caminho_validador):
        caminho_validador = os.path.dirname(caminho_validador)

    if not os.path.isdir(pasta_xmls):
        logger.error(f"Pasta de XMLs inválida: {pasta_xmls}")
        return
    if not os.path.isdir(caminho_validador):
        logger.error("Caminho do Validador inválido. Configure em Config → Configurações.")
        return

    pasta_lib = os.path.join(caminho_validador, "lib")
    pasta_classes = os.path.join(caminho_validador, "classes")
    if not (os.path.isdir(pasta_lib) and os.path.isdir(pasta_classes)):
        logger.error("A pasta do Validador deve conter subpastas 'lib' e 'classes'.")
        return

    os.makedirs(pasta_saida, exist_ok=True)

    jars = [os.path.join("lib", j) for j in os.listdir(pasta_lib) if j.lower().endswith(".jar")]
    if not jars:
        logger.error("Nenhum .jar encontrado em lib/.")
        return
    classpath = os.pathsep.join(jars + ["classes"])

    limite_mb = 5
    limite_bytes = limite_mb * 1024 * 1024
    todos_xmls = [f for f in os.listdir(pasta_xmls) if f.lower().endswith(".xml")]
    if not todos_xmls:
        logger.info("Nenhum XML para validar.")
        return

    xmls_pequenos, xmls_grandes = [], []
    for x in todos_xmls:
        p = os.path.join(pasta_xmls, x)
        (xmls_pequenos if os.path.getsize(p) <= limite_bytes else xmls_grandes).append(x)

    logger.info(f"Total de XMLs pequenos (≤ {limite_mb} MB): {len(xmls_pequenos)}")
    logger.info(f"Total de XMLs grandes   (> {limite_mb} MB): {len(xmls_grandes)}")

    resultados_total = []
    processados = 0
    total = len(todos_xmls) or 1

    def validar_lote(lista_xmls, timeout=None, tipo="PEQUENO"):
        nonlocal processados
        resultados = []
        for xml in lista_xmls:
            if cancel_flag.is_set():
                logger.info("Validação cancelada pelo usuário.")
                break

            caminho_xml = os.path.join(pasta_xmls, xml)
            comando = [
                "java",
                "-Xms512m",
                "-Xmx1024m",
                "-cp",
                classpath,
                "br.gov.bcb.scr2.validador.linhacomando.ValidadorIfLinhaComando",
                os.path.abspath(caminho_xml),
                "no_warn",
            ]

            logger.info(f"[{tipo}] Validando: {xml}")
            inicio = time.perf_counter()
            status = "Desconhecido"
            saida = ""
            try:
                proc = subprocess.run(
                    comando,
                    capture_output=True,
                    text=True,
                    cwd=caminho_validador,
                    timeout=timeout,
                )
                saida = (proc.stdout or "").strip()
                err = (proc.stderr or "").strip()
                if err and not saida:
                    saida = err

                low = saida.lower()
                if "validação com sucesso" in low or "validacao com sucesso" in low:
                    status = "Sucesso"
                elif "não passou na validação" in low or "nao passou na validacao" in low:
                    status = "Erro de validação"
                elif "exception" in low or proc.returncode not in (0,):
                    status = "Falha técnica"
                else:
                    status = "Desconhecido"
            except subprocess.TimeoutExpired:
                saida = "Tempo excedido (timeout)"
                status = "Timeout"

            duracao = round(time.perf_counter() - inicio, 2)
            resultados.append(
                {
                    "Arquivo XML": xml,
                    "Status": status,
                    "Tempo (s)": duracao,
                    "Saída do validador": saida,
                }
            )

            if status == "Sucesso":
                try:
                    destino_xml = os.path.join(pasta_saida, xml)
                    shutil.move(caminho_xml, destino_xml)
                    zip_nome = f"{xml}_VALIDADO.zip"
                    caminho_zip = os.path.join(pasta_xmls, zip_nome)
                    if os.path.exists(caminho_zip):
                        destino_zip = os.path.join(pasta_saida, zip_nome)
                        shutil.move(caminho_zip, destino_zip)
                except Exception as e:
                    logger.warning(f"Falha ao mover arquivos validados de '{xml}': {e}")

            processados += 1
            if progress is not None:
                try:
                    progress(int(processados * 100 / total))
                except Exception:
                    pass
        return resultados

    resultados_total.extend(validar_lote(xmls_pequenos, tipo="PEQUENO"))
    if not cancel_flag.is_set():
        resultados_total.extend(validar_lote(xmls_grandes, timeout=5400, tipo="GRANDE"))

    if resultados_total:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        saida_excel = os.path.join(pasta_saida, f"relatorio_validacao_scr_{ts}.xlsx")
        try:
            df = pd.DataFrame(resultados_total)
            with pd.ExcelWriter(saida_excel, engine="xlsxwriter") as writer:
                df.to_excel(writer, sheet_name="Validação", index=False)
                ws = writer.sheets["Validação"]
                ws.set_column(0, 0, 42)
                ws.set_column(1, 1, 16)
                ws.set_column(2, 2, 10)
                ws.set_column(3, 3, 110)
            logger.info(f"Relatório salvo em: {saida_excel}")
        except Exception as e:
            logger.warning(f"Falha ao salvar relatório de validação: {e}")

    logger.info("Validação finalizada.")


def bacen_envia_sta(
    cancel_flag: threading.Event,
    pasta_arquivos: str,
    credenciais_sta: dict | None,
    caminho_chromedriver: str,
    pasta_saida: str | None = None,
    login_timeout_secs: int = 120,
    tamanho_lote: int = 2,
    max_mb_por_lote: int | None = 150,
    cod_tipo_arquivo: str = "94",
    refresh_every: int = 0,
    app_logger=None,
    progress=None,
):
    logger = app_logger or logging.getLogger(__name__)
    _ = credenciais_sta  # mantido por compatibilidade
    if not os.path.isdir(pasta_arquivos):
        logger.error(f"Pasta dos arquivos inválida: {pasta_arquivos}")
        return
    if not (caminho_chromedriver and os.path.isfile(caminho_chromedriver)):
        logger.error("ChromeDriver inválido. Configure em Config → Configurações.")
        return

    if pasta_saida is None:
        pasta_saida = pasta_arquivos
    os.makedirs(pasta_saida, exist_ok=True)
    pasta_enviados = os.path.join(pasta_arquivos, "Enviados")
    os.makedirs(pasta_enviados, exist_ok=True)

    zips = sorted([f for f in os.listdir(pasta_arquivos) if f.lower().endswith(".zip")])
    if not zips:
        logger.info("Nenhum arquivo .zip encontrado para envio.")
        return

    try:
        tamanho_lote = int(tamanho_lote) if tamanho_lote else 2
    except Exception:
        tamanho_lote = 2

    max_bytes_lote = None
    try:
        if max_mb_por_lote:
            max_mb_por_lote = int(max_mb_por_lote)
            if max_mb_por_lote > 0:
                max_bytes_lote = max_mb_por_lote * 1024 * 1024
    except Exception:
        max_mb_por_lote = None
        max_bytes_lote = None

    logger.info(
        f"Arquivos a enviar: {len(zips)} (limite itens/lote={tamanho_lote}, limite MB/lote={max_mb_por_lote or 'sem limite'})."
    )

    def _hash_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(4096), b""):
                h.update(blk)
        return h.hexdigest()

    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-extensions")
        service = Service(caminho_chromedriver)
        driver = webdriver.Chrome(service=service, options=options)
    except WebDriverException as e:
        logger.error(f"Falha ao iniciar ChromeDriver: {e}")
        return

    def _abrir_tela_envio_e_aguardar_login() -> bool:
        driver.get("https://sta.bcb.gov.br/sta/envioArquivos?2")
        logger.info(f"Aguarde o login manual no STA (tempo: {login_timeout_secs}s)…")
        try:
            WebDriverWait(driver, int(login_timeout_secs)).until(
                EC.element_to_be_clickable((By.ID, "fileInputButton"))
            )
            logger.info("Login detectado. Iniciando envios…")
            return True
        except Exception:
            logger.error("Não detectei o botão 'Novo arquivo' dentro do tempo. Verifique o login/SSO/URL.")
            return False

    def _verificar_hash_gerado():
        try:
            v = driver.find_element(By.ID, "hash").get_attribute("value")
            return v if v else None
        except Exception:
            return None

    def _forcar_selecao_tipo(value_str: str) -> bool:
        try:
            sel = Select(WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "tipoArquivo"))))
            sel.select_by_value(str(value_str))
            return True
        except Exception:
            pass
        try:
            driver.execute_script(
                """
                var el = document.getElementById('tipoArquivo');
                if (el) {
                    el.value = arguments[0];
                    var evt = new Event('change', { bubbles: true });
                    el.dispatchEvent(evt);
                    if (window.jQuery) { jQuery(el).trigger('change'); }
                }
                """,
                str(value_str),
            )
            cur = driver.execute_script("return document.getElementById('tipoArquivo')?.value || '';")
            return str(cur) == str(value_str)
        except Exception:
            return False

    def _xpath_literal(s: str) -> str:
        if "'" not in s:
            return f"'{s}'"
        if '"' not in s:
            return f'"{s}"'
        parts = s.split("'")
        return "concat(" + ", \"'\", ".join([f"'{p}'" for p in parts]) + ")"

    def _enviar_zip(caminho_arquivo: str):
        nome_arquivo = os.path.basename(caminho_arquivo)
        btn = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "fileInputButton")))
        btn.click()
        try:
            driver.execute_script("document.getElementById('novoArquivo').classList.remove('dadosArquivoHidden');")
        except Exception:
            pass
        try:
            upload = WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "novoArquivo")))
            if not upload.is_displayed():
                driver.execute_script("document.getElementById('novoArquivo').style.display='block';")
            upload.send_keys(caminho_arquivo)
        except Exception as e:
            logger.warning(f"[{nome_arquivo}] Erro ao anexar o arquivo: {e}")
            return None

        time.sleep(5)
        if not _forcar_selecao_tipo(cod_tipo_arquivo):
            logger.warning(f"[{nome_arquivo}] Não consegui selecionar tipo {cod_tipo_arquivo}.")

        hash_val = None
        hash_wait_secs = 120
        try:
            size_mb = os.path.getsize(caminho_arquivo) / (1024 * 1024)
            hash_wait_secs = int(min(600, max(120, size_mb * 2)))
        except Exception:
            pass
        try:
            WebDriverWait(driver, hash_wait_secs).until(
                lambda d: (d.find_element(By.ID, "hash").get_attribute("value") or "").strip() != ""
            )
            hash_val = _verificar_hash_gerado()
        except Exception:
            hash_val = _verificar_hash_gerado()
        if not hash_val:
            manual = _hash_sha256(caminho_arquivo)
            try:
                WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, "hashManualBtn"))).click()
            except Exception:
                pass
            try:
                fld = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "hash")))
                if fld.is_enabled():
                    fld.clear()
                    fld.send_keys(manual)
            except Exception as e:
                logger.warning(f"[{nome_arquivo}] Falha ao preencher hash manual: {e}")

        try:
            WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "confirmarBtn"))).click()
        except TimeoutException:
            logger.warning(f"[{nome_arquivo}] Botão Confirmar indisponível.")
            return None

        protocolo = ""
        try:
            nome_lit = _xpath_literal(nome_arquivo)
            row_xpath = f"//table[@id='uploads']//tbody//tr[td[contains(normalize-space(), {nome_lit})]]"
            WebDriverWait(driver, 180).until(lambda d: d.find_elements(By.XPATH, row_xpath))
            rows = driver.find_elements(By.XPATH, row_xpath)
            if rows:
                tds = rows[-1].find_elements(By.TAG_NAME, "td")
                if tds:
                    protocolo = tds[0].text.strip()
        except Exception:
            protocolo = ""

        if not protocolo:
            try:
                xpath_proto = "//table[@id='uploads']//tbody//tr//td[1]"
                before = len(driver.find_elements(By.XPATH, xpath_proto))
                WebDriverWait(driver, 180).until(
                    lambda d: len(d.find_elements(By.XPATH, xpath_proto)) > before
                    or (d.find_elements(By.XPATH, xpath_proto) and d.find_elements(By.XPATH, xpath_proto)[-1].text.strip() != "")
                )
                protos = driver.find_elements(By.XPATH, xpath_proto)
                protocolo = protos[-1].text.strip() if protos else ""
            except Exception:
                protocolo = ""

        if not protocolo:
            logger.warning(f"[{nome_arquivo}] Nenhum protocolo encontrado após envio.")
            return None

        try:
            fechar = driver.find_element(By.XPATH, "//button[@data-dismiss='modal']")
            fechar.click()
            time.sleep(2)
        except Exception:
            pass

        logger.info(f"[OK] {nome_arquivo} → Protocolo: {protocolo}")
        return protocolo

    enviados = 0
    registros = []
    total_arquivos = len(zips) or 1
    try:
        if not _abrir_tela_envio_e_aguardar_login():
            return

        infos = []
        for nome_zip in zips:
            path_zip = os.path.join(pasta_arquivos, nome_zip)
            try:
                sz = os.path.getsize(path_zip)
            except Exception:
                sz = 0
            infos.append((nome_zip, sz))
        size_map = {n: s for n, s in infos}

        def _chunk_lotes(itens, max_itens, max_bytes=None):
            lotes, atual, acum = [], [], 0
            for nome, sz in itens:
                if max_bytes and sz > max_bytes:
                    logger.warning(f"[{nome}] excede limite de {max_mb_por_lote} MB; será enviado sozinho.")
                if not atual:
                    atual, acum = [nome], sz
                    continue
                if len(atual) >= max_itens or (max_bytes and (acum + sz) > max_bytes):
                    lotes.append(atual)
                    atual, acum = [nome], sz
                else:
                    atual.append(nome)
                    acum += sz
            if atual:
                lotes.append(atual)
            return lotes

        lotes = _chunk_lotes(infos, tamanho_lote, max_bytes_lote)
        total_lotes = len(lotes)
        for idx, lote in enumerate(lotes, start=1):
            if cancel_flag.is_set():
                logger.info("Envio cancelado pelo usuário.")
                break

            lote_bytes = sum(size_map.get(name, 0) for name in lote)
            logger.info(f"Processando lote {idx}/{total_lotes} (itens={len(lote)}, ~{lote_bytes/1048576:.1f} MB): {lote}")

            for nome_zip in lote:
                if cancel_flag.is_set():
                    break

                path_zip = os.path.join(pasta_arquivos, nome_zip)
                tamanho_b = os.path.getsize(path_zip)
                sha = _hash_sha256(path_zip)

                protocolo = _enviar_zip(path_zip)
                if protocolo:
                    registros.append(
                        {
                            "Arquivo": nome_zip,
                            "Protocolo": protocolo,
                            "DataHoraEnvio": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "Tamanho_Bytes": tamanho_b,
                            "SHA256": sha,
                            "Lote": idx,
                        }
                    )
                    enviados += 1
                    if refresh_every and int(refresh_every) > 0 and enviados % int(refresh_every) == 0:
                        logger.info("Atualizando a página para manter a sessão responsiva…")
                        driver.refresh()
                        try:
                            WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "fileInputButton")))
                        except Exception:
                            pass

                if progress is not None:
                    try:
                        progress(int(enviados * 100 / total_arquivos))
                    except Exception:
                        pass

            if idx < total_lotes:
                logger.info("Pausa entre lotes…")
                time.sleep(10)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_xlsx = os.path.join(pasta_saida, f"relatorio_protocolos_STA_{ts}.xlsx")
        try:
            df = pd.DataFrame(registros, columns=["Arquivo", "Protocolo", "DataHoraEnvio", "Tamanho_Bytes", "SHA256", "Lote"])
            with pd.ExcelWriter(out_xlsx, engine="xlsxwriter") as writer:
                df.to_excel(writer, sheet_name="Protocolos", index=False)
                ws = writer.sheets["Protocolos"]
                ws.autofilter(0, 0, len(df), len(df.columns) - 1)
                ws.set_column(0, 0, 40)
                ws.set_column(1, 1, 28)
                ws.set_column(2, 2, 20)
                ws.set_column(3, 3, 16)
                ws.set_column(4, 4, 66)
                ws.set_column(5, 5, 8)
            logger.info(f"Relatório salvo em: {out_xlsx}")
        except Exception as e:
            logger.error(f"Falha ao salvar relatório Excel: {e}")

    finally:
        try:
            tempo_espera_final_seg = 10 * 60
            logger.info(
                f"Aguardando {tempo_espera_final_seg/60:.0f} minutos para garantir conclusão das transmissões no STA antes de encerrar o navegador..."
            )
            time.sleep(tempo_espera_final_seg)
            driver.quit()
        except Exception:
            pass



def extrair_status_aceitacao_historico(driver, app_logger=None):
    """
    Extrai da tabela "Histórico" do detalhe do protocolo a última linha que
    represente aceite/rejeição do arquivo pelo STA/SCR.

    Exemplo esperado no HTML:
        <tr class="fundoPadraoAClaro2">
            <td>16/04/2026 09:16:00</td>
            <td>Arquivo aceito</td>
            <td>ARQUIVO VALIDADO</td>
            <td>deinf.s-scr2</td>
        </tr>

    Retorna:
        data_aceite_rejeicao, status_aceite, descricao_aceite, responsavel_aceite
    """
    logger = app_logger or logging.getLogger(__name__)

    retorno_vazio = {
        "Data Aceite/Rejeição": "",
        "Status Aceite": "",
        "Descrição Aceite": "",
        "Responsável Aceite": "",
    }

    try:
        # Captura as linhas após o cabeçalho "Histórico".
        # Como o STA usa tabelas simples, a estratégia mais robusta é varrer
        # todas as linhas com <td> e procurar os estados finais desejados.
        linhas = driver.find_elements(By.XPATH, "//tr[td]")

        candidatos = []
        for linha in linhas:
            try:
                tds = linha.find_elements(By.TAG_NAME, "td")
                textos = [td.text.strip() for td in tds]

                if len(textos) < 3:
                    continue

                data_hora = textos[0]
                estado = textos[1]
                descricao = textos[2]
                responsavel = textos[3] if len(textos) >= 4 else ""

                estado_low = estado.lower()
                descricao_low = descricao.lower()

                eh_linha_final = (
                    "arquivo aceito" in estado_low
                    or "arquivo rejeitado" in estado_low
                    or "arquivo validado" in descricao_low
                    or "arquivo não validado" in descricao_low
                    or "arquivo nao validado" in descricao_low
                )

                if eh_linha_final:
                    candidatos.append(
                        {
                            "Data Aceite/Rejeição": data_hora,
                            "Status Aceite": estado,
                            "Descrição Aceite": descricao,
                            "Responsável Aceite": responsavel,
                        }
                    )
            except StaleElementReferenceException:
                continue
            except Exception:
                continue

        if candidatos:
            # Usa o último candidato, pois o histórico costuma estar em ordem cronológica.
            return candidatos[-1]

        return retorno_vazio

    except Exception as e:
        logger.info(f"Não foi possível extrair status de aceite/rejeição no histórico: {e}")
        return retorno_vazio

def bacen_retorna_protocolos_sta(
    cancel_flag: threading.Event,
    caminho_excel_protocolos: str,
    pasta_saida: str,
    credenciais_sta: dict | None,
    caminho_chromedriver: str,
    app_logger=None,
    progress=None,
):
    logger = app_logger or logging.getLogger(__name__)
    _ = credenciais_sta  # mantido por compatibilidade

    if not os.path.isfile(caminho_excel_protocolos):
        logger.error(f"Planilha de protocolos não encontrada: {caminho_excel_protocolos}")
        return
    if not os.path.isdir(pasta_saida):
        try:
            os.makedirs(pasta_saida, exist_ok=True)
        except Exception as e:
            logger.error(f"Não foi possível criar a pasta de saída: {e}")
            return
    if not (caminho_chromedriver and os.path.isfile(caminho_chromedriver)):
        logger.error("ChromeDriver inválido. Configure em Config → Configurações.")
        return

    try:
        dfp = pd.read_excel(caminho_excel_protocolos)
    except Exception as e:
        logger.error(f"Falha ao ler planilha de protocolos: {e}")
        return

    cols_lower = {str(c).strip().lower(): c for c in dfp.columns}
    col = None
    for cand in ("protocolos", "protocolo"):
        if cand in cols_lower:
            col = cols_lower[cand]
            break
    if not col:
        logger.error("A planilha deve conter a coluna 'Protocolos' (ou 'Protocolo').")
        return

    protocolos = [str(x).strip() for x in dfp[col].tolist() if str(x).strip()]
    if not protocolos:
        logger.info("Nenhum protocolo encontrado na planilha.")
        return

    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-extensions")
        service = Service(caminho_chromedriver)
        driver = webdriver.Chrome(service=service, options=options)
    except WebDriverException as e:
        logger.error(f"Falha ao iniciar ChromeDriver: {e}")
        return

    resultados = []
    total = len(protocolos) or 1
    try:
        driver.get("https://sta.bcb.gov.br/sta/")
        logger.info("Abra o STA e faça o login (se necessário). Aguardando a página principal…")
        try:
            WebDriverWait(driver, 600).until(EC.presence_of_element_located((By.NAME, "protocolos")))
            logger.info("Login detectado. Iniciando consultas…")
        except TimeoutException:
            logger.error("Tempo esgotado aguardando login no STA.")
            return

        def _with_retry(find_callable, clicks=False, max_tries=2):
            last_err = None
            for _ in range(max_tries):
                try:
                    el = find_callable()
                    if clicks:
                        el.click()
                        return True
                    return el
                except StaleElementReferenceException as e:
                    last_err = e
                    time.sleep(1)
            if last_err:
                raise last_err

        for i, protocolo in enumerate(protocolos, start=1):
            if cancel_flag.is_set():
                logger.info("Consulta cancelada pelo usuário.")
                return
            logger.info(f"[{i}/{len(protocolos)}] Consultando protocolo: {protocolo}")
            try:
                campo = _with_retry(
                    lambda: WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.NAME, "protocolos")))
                )
                campo.clear()
                campo.send_keys(protocolo)
                _with_retry(
                    lambda: WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.ID, "id8"))),
                    clicks=True,
                )
                _with_retry(
                    lambda: WebDriverWait(driver, 15).until(
                        EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), '/') and contains(text(), ':')]"))
                    ),
                    clicks=True,
                )

                data_criado = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.XPATH, "//td[contains(text(), 'Criado:')]/following-sibling::td/strong"))
                ).text.strip()

                try:
                    nome_enviado = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located(
                            (By.XPATH, "//span[contains(text(), '.zip') or contains(text(), '.json') or contains(text(), '.xml')]")
                        )
                    ).text.strip()
                except Exception:
                    nome_enviado = "Nenhum arquivo .zip/.json/.xml encontrado"
                    logger.info(f"Protocolo {protocolo}: nenhum arquivo encontrado (.zip/.json/.xml).")

                estado_atual = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.XPATH, "//td[contains(text(), 'Estado atual:')]/following-sibling::td//span"))
                ).text.strip()

                dados_aceite = extrair_status_aceitacao_historico(driver, app_logger=logger)

                try:
                    span_prot = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.XPATH, f"//span[contains(normalize-space(), 'Protocolo {protocolo}')]"))
                    )
                    texto_span = span_prot.text.strip()
                    prefixo = f"Protocolo {protocolo} - "
                    tipo_doc = texto_span[len(prefixo):].strip() if texto_span.startswith(prefixo) else texto_span
                except Exception:
                    tipo_doc = ""
                    logger.info(f"Protocolo {protocolo}: não foi possível capturar o tipo do documento.")

                resultados.append(
                    {
                        "Protocolo": protocolo,
                        "Criado": data_criado,
                        "Arquivo Enviado": nome_enviado,
                        "Estado Atual": estado_atual,
                        "Tipo Documento": tipo_doc,
                        "Data Aceite/Rejeição": dados_aceite.get("Data Aceite/Rejeição", ""),
                        "Status Aceite": dados_aceite.get("Status Aceite", ""),
                        "Descrição Aceite": dados_aceite.get("Descrição Aceite", ""),
                        "Responsável Aceite": dados_aceite.get("Responsável Aceite", ""),
                    }
                )
                logger.info(
                    f"Protocolo {protocolo} — Criado: {data_criado} — Estado: {estado_atual} — "
                    f"Tipo: {tipo_doc} — Aceite: {dados_aceite.get('Status Aceite', '')} "
                    f"{dados_aceite.get('Data Aceite/Rejeição', '')} "
                    f"{dados_aceite.get('Descrição Aceite', '')}"
                )

                _with_retry(
                    lambda: WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.NAME, "btnVoltar"))),
                    clicks=True,
                )
            except Exception as e:
                logger.warning(f"Erro ao consultar protocolo {protocolo}: {type(e).__name__} — {e}")

            if progress is not None:
                try:
                    progress(int(i * 100 / total))
                except Exception:
                    pass

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_xlsx = os.path.join(pasta_saida, f"Consulta_protocolos_STA_{ts}.xlsx")
        try:
            pd.DataFrame(resultados).to_excel(out_xlsx, index=False)
            logger.info(f"Resultados salvos em: {out_xlsx}")
        except Exception as e:
            logger.error(f"Falha ao salvar Excel de resultados: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
