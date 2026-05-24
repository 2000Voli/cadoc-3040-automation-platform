

import os
import sys
import json
import time
import threading
import logging
import re
import queue
import tempfile
import shutil
import subprocess
import platform
import locale
import inspect
from pathlib import Path
from datetime import datetime
from functools import lru_cache

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import xml.etree.ElementTree as ET



try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.service import Service
    from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# Evitar dependências opcionais do pandas
os.environ.setdefault("PANDAS_NO_ARROW", "1")
os.environ.setdefault("PANDAS_NO_BOTTLENECK", "1")
os.environ.setdefault("PANDAS_NO_NUMEXPR", "1")




def _snapshot_fidcs_to_temp(app_self=None) -> Path:
    """
    Cria um snapshot temporário dos dados FIDC para uso em processos externos.
    
    Args:
        app_self: Instância da aplicação com registry FIDC
        
    Returns:
        Path: Caminho para o arquivo temporário criado
        
    Raises:
        OSError: Se não conseguir criar arquivo temporário
        PermissionError: Se não tiver permissão para escrever
    """
    rows = []
    
    # 1) Tenta usar registry da GUI
    try:
        if app_self is not None and hasattr(app_self, "fidc_reg") and hasattr(app_self.fidc_reg, "all"):
            rows = list(app_self.fidc_reg.all())
            logging.info(f"Carregados {len(rows)} registros do registry da GUI")
    except AttributeError as e:
        logging.warning(f"Registry da GUI não disponível: {e}")
    except Exception as e:
        logging.error(f"Erro ao acessar registry da GUI: {e}")

    # 2) Fallback: arquivo fidcs_db.json
    if not rows:
        try:
            rows = _fidcs_db_load()
            logging.info(f"Carregados {len(rows)} registros do arquivo fidcs_db.json")
        except FileNotFoundError:
            logging.warning("Arquivo fidcs_db.json não encontrado")
        except json.JSONDecodeError as e:
            logging.error(f"Erro ao decodificar JSON do fidcs_db.json: {e}")
        except Exception as e:
            logging.error(f"Erro inesperado ao carregar fidcs_db.json: {e}")

    # 3) Cria arquivo temporário
    try:
        tmp = tempfile.NamedTemporaryFile(prefix="fidcs_snapshot_", suffix=".json", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        
        # Escreve dados no arquivo temporário
        tmp_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info(f"Snapshot criado em: {tmp_path}")
        return tmp_path
        
    except OSError as e:
        logging.error(f"Erro ao criar arquivo temporário: {e}")
        raise
    except PermissionError as e:
        logging.error(f"Sem permissão para criar arquivo temporário: {e}")
        raise
    except Exception as e:
        logging.error(f"Erro inesperado ao criar snapshot: {e}")
        raise



def resource_path(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base.joinpath(*parts))


def _parse_geometry_str(geo: str):
    if not geo:
        return None
    m = re.match(r"^(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?$", geo.strip())
    if not m:
        return None
    try:
        w, h = int(m.group(1)), int(m.group(2))
        x, y = int(m.group(3) or 0), int(m.group(4) or 0)
        return w, h, x, y
    except Exception:
        return None


def _default_geometry(root, min_size=(900, 600), frac=(0.78, 0.82), margin=60):
    """Calcula tamanho inicial proporcional à tela, respeitando mínimos."""
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = int(sw * frac[0])
        h = int(sh * frac[1])
        max_w = max(min_size[0], sw - margin)
        max_h = max(min_size[1], sh - margin)
        w = max(min_size[0], min(w, max_w))
        h = max(min_size[1], min(h, max_h))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        return f"{w}x{h}+{x}+{y}"
    except Exception:
        return f"{min_size[0]}x{min_size[1]}"


def _clamp_geometry_to_screen(root, geo: str, min_size=(900, 600), margin=40, fallback=None):
    """Limita geometria salva à tela atual para evitar janela fora da área visível."""
    try:
        parsed = _parse_geometry_str(geo)
        if not parsed:
            return fallback
        w, h, x, y = parsed
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        max_w = max(min_size[0], sw - margin)
        max_h = max(min_size[1], sh - margin)
        w = max(min_size[0], min(w, max_w))
        h = max(min_size[1], min(h, max_h))
        x = max(0, min(x, max(0, sw - w)))
        y = max(0, min(y, max(0, sh - h)))
        return f"{w}x{h}+{x}+{y}"
    except Exception:
        return fallback


def _best_effort_maximize(root):
    """Tenta maximizar (ou ocupar quase toda a tela)."""
    try:
        root.state("zoomed")
        return
    except Exception:
        pass
    try:
        root.attributes("-zoomed", True)
        return
    except Exception:
        pass
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        margin = 8
        root.geometry(f"{max(200, sw - margin)}x{max(200, sh - margin)}+0+0")
    except Exception:
        pass


def _maybe_maximize_if_small(root, min_frac=0.9):
    """Se a janela ocupar menos que uma fração da tela, tenta maximizar."""
    try:
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = max(1, root.winfo_width()), max(1, root.winfo_height())
        if w < sw * min_frac or h < sh * min_frac:
            _best_effort_maximize(root)
    except Exception:
        pass




# deixando a GUI bonitinha (ajuda do gepeto)
def _tune_windows_dpi_and_fonts(root: tk.Tk):

    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # per-monitor DPI awareness
    except Exception:
        pass

    try:
        root.tk.call('tk', 'scaling', 1.2)  # 1.0=100%, 1.2=120% 
        import tkinter.font as tkfont
        tkfont.nametofont("TkDefaultFont").configure(size=10)
        tkfont.nametofont("TkTextFont").configure(size=10)
        tkfont.nametofont("TkHeadingFont").configure(size=11, weight="bold")
        tkfont.nametofont("TkMenuFont").configure(size=10)
    except Exception:
        pass


THEME_COLORS = {
    "bg": "#F5F7FA",          # fundo global (60%)
    "card": "#FFFFFF",        # superfícies (cards, áreas internas)
    "fg": "#0F172A",          # texto principal
    "subfg": "#475569",       # texto secundário
    "border": "#E2E8F0",      # bordas leves

    "primary": "#0F2A56",     # azul escuro (30%) — títulos, abas, botões secundários filled
    "primary_hover": "#0C2146",
    "primary_active": "#081830",

    "accent": "#3B82F6",      # azul claro (10%) — CTA, foco, progressbar
    "accent_hover": "#5A96F7",
    "accent_active": "#2563EB",

    "sel_bg": "#E8F0FE",      # seleção/foco claro
    "sel_fg": "#0F172A",
}

def _safe_style_config(style, name, **opts):
    try:
        style.configure(name, **opts)
    except Exception:
        pass

def _safe_style_map(style, name, **maps):
    try:
        style.map(name, **maps)
    except Exception:
        pass

def apply_theme(root) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        # segue com o tema atual
        pass

    c = THEME_COLORS

    # Fundo global
    try:
        root.configure(bg=c["bg"])
    except Exception:
        pass

    # Base
    for t in (
        "TFrame","TNotebook","TNotebook.Tab","TLabelframe","TLabelframe.Label",
        "TLabel","TCheckbutton","TRadiobutton","TEntry","TCombobox","TPanedwindow",
        "TMenubutton","TScrollbar"
    ):
        _safe_style_config(style, t, background=c["bg"])
    _safe_style_config(style, "TLabel", foreground=c["fg"])
    _safe_style_config(style, "TEntry", fieldbackground="#FFFFFF", foreground=c["fg"])
    _safe_style_config(style, "TCombobox", fieldbackground="#FFFFFF", foreground=c["fg"])

    # LabelFrame como "card"
    _safe_style_config(style, "TLabelframe", background=c["card"], bordercolor=c["border"])
    _safe_style_config(style, "TLabelframe.Label", background=c["card"], foreground=c["primary"])

    # Notebook / Abas
    _safe_style_config(style, "TNotebook", background=c["bg"], bordercolor=c["border"])
    _safe_style_config(style, "TNotebook.Tab", background=c["bg"], foreground=c["primary"], padding=(14, 8))
    _safe_style_map(
        style, "TNotebook.Tab",
        background=[("selected", c["card"]), ("!selected", c["bg"]), ("active", c["sel_bg"])],
        foreground=[("selected", c["primary"]), ("!selected", c["primary"])],
    )

    # Botões
    # Default: "outline" azul escuro
    _safe_style_config(
        style, "TButton",
        background=c["bg"], foreground=c["primary"], bordercolor=c["primary"],
        relief="flat", padding=(12, 8)
    )
    _safe_style_map(
        style, "TButton",
        background=[("active", c["sel_bg"])],
        foreground=[("disabled", "#9CA3AF"), ("!disabled", c["primary"])]
    )

    # Primário (CTA) – azul claro preenchido
    _safe_style_config(
        style, "Primary.TButton",
        background=c["accent"], foreground="#FFFFFF", bordercolor=c["accent"],
        relief="flat", padding=(12, 8)
    )
    _safe_style_map(
        style, "Primary.TButton",
        background=[("active", c["accent_hover"]), ("pressed", c["accent_active"])],
        foreground=[("disabled", "#E5E7EB"), ("!disabled", "#FFFFFF")]
    )

    # Secundário (filled) – azul escuro
    _safe_style_config(
        style, "Secondary.TButton",
        background=c["primary"], foreground="#FFFFFF", bordercolor=c["primary"],
        relief="flat", padding=(12, 8)
    )
    _safe_style_map(
        style, "Secondary.TButton",
        background=[("active", c["primary_hover"]), ("pressed", c["primary_active"])],
        foreground=[("disabled", "#E5E7EB"), ("!disabled", "#FFFFFF")]
    )

    # Progressbar (trilho claro, barra com azul claro)
    _safe_style_config(
        style, "Accent.Horizontal.TProgressbar",
        troughcolor=c["border"], background=c["accent"],
        lightcolor=c["accent"], darkcolor=c["accent"]
    )

    # Treeview
    _safe_style_config(
        style, "Custom.Treeview",
        background="#FFFFFF", fieldbackground="#FFFFFF",
        foreground=c["fg"], rowheight=24, bordercolor=c["border"]
    )
    _safe_style_map(
        style, "Custom.Treeview",
        background=[("selected", c["sel_bg"])],
        foreground=[("selected", c["sel_fg"])]
    )
    _safe_style_config(style, "Custom.Treeview.Heading", background=c["primary"], foreground="#FFFFFF", relief="flat")

    # Scrollbar discreta
    _safe_style_config(style, "TScrollbar", background=c["bg"], troughcolor=c["border"])

    return style

def promote_primary(*buttons: ttk.Button):
    """Torna os botões passados 'Primários' (CTA) sem mudar o resto do app."""
    for b in buttons:
        try:
            b.configure(style="Primary.TButton")
        except Exception:
            pass

def skin_text_widget(txt: tk.Text):
    """Aplica skin no Text do log (coeso com o tema)."""
    c = THEME_COLORS
    try:
        txt.configure(
            bg="#FFFFFF", fg=c["fg"],
            insertbackground=c["accent"],   # cor do cursor de inserção
            selectbackground=c["sel_bg"], selectforeground=c["sel_fg"],
            highlightthickness=1, highlightbackground=c["border"], highlightcolor=c["accent"]
        )
    except Exception:
        pass

def skin_treeview(tv: ttk.Treeview):
    """Aplica o estilo Custom.Treeview e habilita 'zebra' opcional."""
    try:
        tv.configure(style="Custom.Treeview")
    except Exception:
        pass
    # zebra opcional
    try:
        tv.tag_configure("odd", background="#FAFBFC")
        tv.tag_configure("even", background="#FFFFFF")
    except Exception:
        pass





def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def _safe_move(src: str | Path, dest_dir: str | Path) -> Path:

    src = Path(src)
    dest_dir = Path(dest_dir)
    _ensure_dir(dest_dir)

    target = dest_dir / src.name
    if target.exists():
        ts = time.strftime("%Y%m%d_%H%M%S")
        stem, suf = src.stem, src.suffix
        i = 1
        while True:
            candidate = dest_dir / f"{stem}_{ts}-{i}{suf}"
            if not candidate.exists():
                target = candidate
                break
            i += 1

    shutil.move(str(src), str(target))
    return target


def _parse_kv_pairs(text: str) -> dict:

    res = {}
    if not text:
        return res
    for part in re.split(r'[;,]\s*', text.strip()):
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1].strip()
        res[k] = v
    return res


def _only_digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def normalize_cnpj(cnpj: str) -> str:
    d = _only_digits(cnpj)
    if len(d) == 14:
        return f"{d[0:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"
    # deixa como veio se não tiver 14 dígitos
    return cnpj or ""

def raiz_cnpj(cnpj: str) -> str:
    d = _only_digits(cnpj)
    if len(d) == 14:
        # raiz “XX.XXX.XXX”
        return f"{d[0:2]}.{d[2:5]}.{d[5:8]}"
    return ""

# Mapeia valores possíveis de LEGADO vindos da planilha para os três permitidos
def map_legado(raw: str) -> str:
    t = (raw or "").strip().upper()
    # equivalências
    if t in {"D", "DRIVE"}:
        return "DRIVE"
    if t in {"F", "FROMTIS"}:
        return "FROMTIS"
    if t in {"D/F", "HÍBRIDO", "HIBRIDO", "HÍBRIDO ", "HÍBRIDO/"}:
        return "HÍBRIDO"
    # fallback: retorna como veio (será validado depois)
    return t

ALLOWED_LEGADOS = {"FROMTIS", "DRIVE", "HÍBRIDO"}

def parse_data_br(data_str: str) -> str:
    """
    Converte data brasileira (dd/mm/aaaa) para formato ISO (aaaa-mm-dd).
    
    Args:
        data_str: Data em formato brasileiro ou ISO
        
    Returns:
        str: Data em formato ISO ou string vazia se inválida
        
    Examples:
        >>> parse_data_br('15/03/2024')
        '2024-03-15'
        >>> parse_data_br('15/03/24')
        '2024-03-15'
        >>> parse_data_br('2024-03-15')
        '2024-03-15'
    """
    if not data_str:
        return ""
    
    data_str = data_str.strip()
    
    # Lista de formatos suportados (ordem de prioridade)
    formatos = [
        "%d/%m/%Y",    # 15/03/2024
        "%d/%m/%y",    # 15/03/24
        "%d-%m-%Y",    # 15-03-2024
        "%Y-%m-%d",    # 2024-03-15 (já ISO)
        "%Y/%m/%d",    # 2024/03/15
        "%d.%m.%Y",    # 15.03.2024
    ]
    
    for formato in formatos:
        try:
            data_obj = datetime.strptime(data_str, formato)
            return data_obj.date().isoformat()
        except ValueError:
            continue
    
    # Se chegou aqui, não conseguiu parsear
    logging.warning(f"Formato de data não reconhecido: '{data_str}'")
    return ""






def _detect_xml_encoding(path: Path, default="ISO-8859-1") -> str:

    try:
        with open(path, "rb") as f:
            head = f.read(256)
        m = re.search(br'encoding=[\"\']([A-Za-z0-9_\-]+)[\"\']', head)
        if m:
            return m.group(1).decode("ascii", "ignore")
    except Exception:
        pass
    return default


def _has_any_xml_recursive(folder: str) -> bool:

    if not os.path.isdir(folder):
        return False
    for _root, _dirs, files in os.walk(folder):
        if any(f.lower().endswith(".xml") for f in files):
            return True
    return False



@lru_cache(maxsize=128)
def raiz_cnpj(cnpj: str) -> str:
    """
    Extrai a raiz CNPJ (8 primeiros dígitos) de um CNPJ.
    
    Args:
        cnpj: CNPJ em qualquer formato (com ou sem formatação)
        
    Returns:
        str: Raiz do CNPJ (8 dígitos) ou string vazia se inválido
        
    Examples:
        >>> raiz_cnpj('34.197.588/0001-37')
        '34197588'
        >>> raiz_cnpj('34197588000137')
        '34197588'
    """
    if not cnpj:
        return ""
    
    try:
        # Remove todos os caracteres não numéricos
        digits = _only_digits(cnpj)
        
        # Retorna os primeiros 8 dígitos se houver pelo menos 8
        if len(digits) >= 8:
            return digits[:8]
        
        # Se tiver menos de 8 dígitos, retorna o que tem
        return digits if digits else ""
        
    except Exception as e:
        logging.warning(f"Erro ao processar CNPJ '{cnpj}': {e}")
        return ""

def _FIDC_DB_PATH() -> Path:
    try:
        return FIDC_DB_PATH
    except NameError:
        return Path(BASE_DIR) / "fidcs_db.json"

def _fidcs_db_load_safe() -> list[dict]:
    """
    Tenta usar o _fidcs_db_load() do seu projeto; se não houver, carrega direto o fidcs_db.json.
    """
    try:
        return _fidcs_db_load()
    except Exception:
        pass
    p = _FIDC_DB_PATH()
    if p.exists():
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    return []

def build_fidc_lookup() -> dict:
    """
    Retorna um dicionário:
      { 'RAIZ8': {'id': <ID>, 'legado': <LEGADO>, 'fundos': <nome opcional>, ... }, ... }
    A base vem do Cadastro de FIDCs (fidcs_db.json).
    """
    rows = _fidcs_db_load_safe()
    lk = {}
    for r in rows:
        # colunas esperadas do cadastro: id, legado, fundos, cnpj, raiz_cnpj etc.
        # prioridade: usar raiz_cnpj, se estiver vazio, derivar do cnpj.
        raiz = r.get("raiz_cnpj") or raiz_cnpj(r.get("cnpj", ""))
        raiz8 = raiz_cnpj(raiz)
        if not raiz8:
            continue
        lk[raiz8] = {
            "id": r.get("id", ""),
            "legado": r.get("legado", ""),
            "fundos": r.get("fundos", ""),
            "carteira": r.get("carteira", ""),
            "cnpj": r.get("cnpj", ""),
        }
    return lk





# UX + IO seguro

class Tooltip:

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        if self.tip:
            return
        try:
            x, y, _, _ = self.widget.bbox("insert") if hasattr(self.widget, "bbox") else (0, 0, 0, 0)
        except Exception:
            x = y = 0
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + 20
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=self.text, bg="#111827", fg="#F9FAFB",
                 padx=8, pady=4, relief="solid", borderwidth=1).pack()
        self.tip.geometry(f"+{x}+{y}")

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None




def atomic_write_text(text: str, out_path: str, encoding="utf-8"):

    tmpdir = tempfile.mkdtemp(prefix="tmpwrite_")
    tmp = Path(tmpdir) / (Path(out_path).name + ".tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, out_path)  # atômico no Windows 10+ e Unix
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def atomic_write_bytes(data: bytes, out_path: str):

    tmpdir = tempfile.mkdtemp(prefix="tmpwrite_")
    tmp = Path(tmpdir) / (Path(out_path).name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, out_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def rotate_backups(folder: Path, stem: str, ext: str, keep: int = 10):

    try:
        files = sorted(folder.glob(f"{stem}_backup_*{ext}"),
                       key=lambda p: p.stat().st_mtime,
                       reverse=True)
        for p in files[keep:]:
            p.unlink(missing_ok=True)
    except Exception:
        pass

def timed(fn):

    def wrapper(*a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            logger.info(f"{fn.__name__} concluído em {time.perf_counter()-t0:.1f}s")
    return wrapper


def _confirm_overwrite(self, path: str) -> bool:
    try:
        if not path:
            return False
        import os
        from tkinter import messagebox
        if os.path.isfile(path):
            return messagebox.askyesno(
                APP_NAME,
                f"O arquivo já existe:\n\n{path}\n\nDeseja sobrescrever?"
            )
        return True
    except Exception:
        return True


class FIDCRegistry:

    def __init__(self, path: Path):
        self.path = Path(path)
        self._items: list[dict] = []
        self.load()


    def load(self):
        try:
            if self.path.exists():
                self._items = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                self._items = []
        except Exception:
            self._items = []

    def save(self):
        try:
            self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Falha ao salvar cadastro FIDCs: {e}")

    #  Acesso
    def all(self) -> list[dict]:
        return list(self._items)

    def get_by_id(self, fid: int) -> dict | None:
        for it in self._items:
            if int(it.get("id", -1)) == int(fid):
                return it
        return None


    def get_by_cnpj_root(self, raiz: str) -> dict | None:

        d = _only_digits(str(raiz or ""))
        if len(d) < 8:
            return None
        raiz8 = d[:8]

        for it in self._items:
            cnpj_it = _only_digits(str(it.get("cnpj") or ""))
            if len(cnpj_it) >= 8 and cnpj_it.startswith(raiz8):
                return it

            raiz_it = _only_digits(str(it.get("raiz_cnpj") or ""))
            if raiz_it == raiz8:
                return it

        return None


    def upsert(self, item: dict):
        """Insere se ID não existir; atualiza se já existir."""
        fid = int(item["id"])
        existing = self.get_by_id(fid)
        if existing:
            existing.update(item)
        else:
            self._items.append(item)
        self.save()

    def delete(self, fid: int) -> bool:
        n0 = len(self._items)
        self._items = [x for x in self._items if int(x.get("id", -1)) != int(fid)]
        if len(self._items) != n0:
            self.save()
            return True
        return False

    # Importação/Exportação
    def import_from_excel(self, excel_path: str) -> tuple[int, list[str]]:
        """
        Lê a planilha com colunas:
        ID, Carteira, CNPJ, RAIZ CNPJ, FUNDOS, LEGADO, TP, METOD, Data esperada pelo Bacen
        Faz normalização e valida LEGADO.
        Retorna (importados, avisos)
        """
        df = pd.read_excel(excel_path)
        # normaliza nomes de colunas
        ren = {
            "ID": "id",
            "Carteira": "carteira",
            "CNPJ": "cnpj",
            "RAIZ CNPJ": "raiz_cnpj",
            "FUNDOS": "fundos",
            "LEGADO": "legado",
            "TP": "tp",
            "METOD": "metod",
            "Data esperada pelo Bacen": "data_esp",
        }
        # tenta casar mesmo se tiver variações de caixa
        df.columns = [c.strip() for c in df.columns]
        cols_lower = {c.lower(): c for c in df.columns}
        missing = []
        for k in ren.keys():
            if k not in df.columns:
                # tenta procurar case-insensitive
                if k.lower() in cols_lower:
                    pass
                else:
                    missing.append(k)
        if missing:
            raise ValueError(f"Colunas ausentes na planilha: {', '.join(missing)}")

        # aplica renome (robusto a case)
        to_rename = {}
        for src, dst in ren.items():
            if src in df.columns:
                to_rename[src] = dst
            else:
                to_rename[cols_lower[src.lower()]] = dst
        df = df.rename(columns=to_rename)

        avisos: list[str] = []
        imported = 0
        for _, row in df.iterrows():
            try:
                fid = int(str(row.get("id")).strip())
            except Exception:
                avisos.append(f"ID inválido: {row.get('id')!r}. Linha ignorada.")
                continue

            cnpj_fmt = normalize_cnpj(str(row.get("cnpj") or ""))
            raiz = str(row.get("raiz_cnpj") or "").strip() or raiz_cnpj(cnpj_fmt)
            fundos = str(row.get("fundos") or "").strip()

            legado = map_legado(row.get("legado"))
            if legado not in ALLOWED_LEGADOS:
                avisos.append(f"LEGADO inválido para ID {fid}: {row.get('legado')!r}. Esperado: {', '.join(sorted(ALLOWED_LEGADOS))}.")
                # ainda assim deixa salvar para o usuário corrigir depois:
                # legado = legado

            tp = str(row.get("tp") or "").strip()
            metod = str(row.get("metod") or "").strip()
            data_esp = parse_data_br(str(row.get("data_esp") or ""))

            item = {
                "id": fid,
                "carteira": str(row.get("carteira") or "").strip(),
                "cnpj": cnpj_fmt,
                "raiz_cnpj": raiz,
                "fundos": fundos,
                "legado": legado,
                "tp": tp,
                "metod": metod,
                "data_esp": data_esp,
            }
            self.upsert(item)
            imported += 1

        return imported, avisos

    def export_to_excel(self, excel_path: str):
        """Exporta o cadastro para Excel com os mesmos nomes de colunas do layout de importação."""
        rows = []
        for it in self._items:
            rows.append({
                "ID": it.get("id"),
                "Carteira": it.get("carteira", ""),
                "CNPJ": it.get("cnpj", ""),
                "RAIZ CNPJ": it.get("raiz_cnpj", ""),
                "FUNDOS": it.get("fundos", ""),
                "LEGADO": it.get("legado", ""),
                "TP": it.get("tp", ""),
                "METOD": it.get("metod", ""),
                "Data esperada pelo Bacen": it.get("data_esp", ""),
            })
        df = pd.DataFrame(rows)
        df.to_excel(excel_path, index=False)






#ajuda do gepeto
class TkQueueLogHandler(logging.Handler):

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            msg = self.format(record)
            self.q.put_nowait(msg)
        except Exception:
            pass


APP_NAME = "Fechamento - 3040 FIDCs"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
CONFIG_FILE = BASE_DIR / "config.json"

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
FIDC_DB_PATH = DATA_DIR / "fidcs.json"



from logging.handlers import RotatingFileHandler

logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(
    LOG_DIR / "app.log",
    maxBytes=2_000_000,   # ~2MB por arquivo
    backupCount=5,        # mantém até 5 arquivos antigos
    encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)



# Regras do Arrumeitor


RULES_ARRUMEITOR = [
    {"id": "r1", "label": "PercIndx 100.00 / Indx 11 → PercIndx 0.00",
     "condicoes": {"PercIndx": "100.00", "Indx": "11"}, "substituicoes": {"PercIndx": "0.00", "Indx": "11"}},
    {"id": "r2", "label": "CEP 00000000 → 01311200",
     "condicoes": {"CEP": "00000000"}, "substituicoes": {"CEP": "01311200"}},
    {"id": "r3", "label": "TelResp 1031381957 → 31381957",
     "condicoes": {"TelResp": "1031381957"}, "substituicoes": {"TelResp": "31381957"}},
    {"id": "r4", "label": "PercIndx 0.0000000 / Indx 99 → 100.0000000",
     "condicoes": {"PercIndx": "0.0000000", "Indx": "99"}, "substituicoes": {"PercIndx": "100.0000000", "Indx": "99"}},
    {"id": "r5", "label": "PercIndx 0.0000000 / Indx 31 → 100.0000000",
     "condicoes": {"PercIndx": "0.0000000", "Indx": "31"}, "substituicoes": {"PercIndx": "100.0000000", "Indx": "31"}},
    {"id": "r6", "label": "Localiz 00000 → 10058",
     "condicoes": {"Localiz": "00000"}, "substituicoes": {"Localiz": "10058"}},
    {"id": "r7", "label": "TelResp 11031381957 → 31381957",
     "condicoes": {"TelResp": "11031381957"}, "substituicoes": {"TelResp": "31381957"}},
    {"id": "r8", "label": "PercIndx 1.0000000 / Indx 11 → 0.00",
     "condicoes": {"PercIndx": "1.0000000", "Indx": "11"}, "substituicoes": {"PercIndx": "0.00", "Indx": "11"}},
    {"id": "r9", "label": "PercIndx 0.0000000 / Indx 32 → 100.0000000",
     "condicoes": {"PercIndx": "0.0000000", "Indx": "32"}, "substituicoes": {"PercIndx": "100.0000000", "Indx": "32"}},
    # regex
    {"id": "rgx1", "type": "regex", "label": "Excluir <Cli> vazio",
     "pattern": r"<Cli\b[^>]*?>\s*</Cli>", "repl": "", "flags": ["MULTILINE", "DOTALL"]},
]

'''DEFAULT_CONFIG = {
    "pastas": {"entrada": str(BASE_DIR), "saida": str(BASE_DIR)},
    "caminhos": {"validador_bacen": "", "sta_user": "", "sta_pass": "", "ultimo_mc": "", "chrome_driver": ""},
    "ui": {"limpar_log_auto": False, "atributos_tags_sel": []},
    "ajustes": {"arrumeitor_sel": [], "arrumeitor_custom": []},
   
}'''



DEFAULT_CONFIG = {
    "pastas": {"entrada": str(BASE_DIR), "saida": str(BASE_DIR)},
    "caminhos": {"validador_bacen": "", "sta_user": "", "sta_pass": "", "ultimo_mc": "", "chrome_driver": ""},
    "ui": {"limpar_log_auto": False, "atributos_tags_sel": [], "repetir_por_inf": False},
    "ajustes": {"arrumeitor_sel": [], "arrumeitor_custom": []},
}



# Excel helpers (split + safe write + formato padrão)

import tempfile

APP_VERSION = "v11.1"  # ajustar aqui a cada nova versão


# FIDC DB helpers
import json
from datetime import datetime

FIDC_DB_PATH = Path(BASE_DIR) / "fidcs_db.json"


def _fidcs_db_load() -> list[dict]:
    try:
        if FIDC_DB_PATH.exists():
            return json.loads(FIDC_DB_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Falha ao carregar FIDCs: {e}")
    return []

def _fidcs_db_save(rows: list[dict]) -> None:
    try:
        FIDC_DB_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Falha ao salvar FIDCs: {e}")

def _fidcs_db_backup() -> Path | None:
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bk = FIDC_DB_PATH.with_name(f"{FIDC_DB_PATH.stem}_backup_{ts}{FIDC_DB_PATH.suffix}")
        if FIDC_DB_PATH.exists():
            bk.write_text(FIDC_DB_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        return bk
    except Exception as e:
        logger.warning(f"Não foi possível criar backup dos FIDCs: {e}")
        return None

def fidcs_clear_all() -> Path | None:
    """
    Zera a base de FIDCs (gera backup antes).
    Retorna o caminho do backup (ou None se não foi possível).
    """
    bk = _fidcs_db_backup()
    _fidcs_db_save([])  # zera
    return bk


def _fidc_update_buttons_state(self, total_rows: int):
    """Habilita/desabilita botões da aba FIDCs conforme houver dados selecionáveis."""
    btns = getattr(self, "_fidc_btns", {})
    has_rows = total_rows > 0


    for key in ("export", "edit", "del", "clear_all"):
        if key in btns:
            btns[key].config(state=(tk.NORMAL if has_rows else tk.DISABLED))




def _fidc_guess_columns(self, cols_in, wanted):
    """
    Tenta mapear os títulos 'wanted' para as colunas reais do arquivo (cols_in),
    aceitando variações simples de acento/maiúsculas.
    Retorna dict: wanted_name -> real_col_name
    """
    import unicodedata

    def norm(s):
        s = (s or "").strip()
        s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
        return s.lower()

    idx = {norm(c): c for c in cols_in}
    wanted_norm = {w: norm(w) for w in wanted}

    # sinônimos comuns
    aliases = {
        "id": ["id"],
        "carteira": ["carteira", "portfolio"],
        "cnpj": ["cnpj"],
        "raiz cnpj": ["raiz cnpj", "raiz_cnpj", "raizcnpj", "raiz"],
        "fundos": ["fundos", "fundo", "nome do fundo", "fundo/veiculo"],
        "legado": ["legado", "sistema", "origem"],
        "tp": ["tp", "tipo", "tipo plano", "tipo produto"],
        "metod": ["metod", "metodo", "metód", "metodologia"],
        "data esperada pelo bacen": ["data esperada pelo bacen", "data esperada", "dt esperada bacen", "data_bacen"],
    }
    # mapeia
    out = {}
    for w, w_norm in wanted_norm.items():
        # tentativa 1: igual
        if w_norm in idx:
            out[w] = idx[w_norm]
            continue
        # tentativa 2: pelas aliases
        hit = None
        for alias in aliases.get(w.lower(), []):
            a_norm = norm(alias)
            if a_norm in idx:
                hit = idx[a_norm]
                break
        if hit:
            out[w] = hit

    return out

def _fidc_normalize_row(self, r: dict) -> dict:
    """Normaliza valores para as chaves esperadas pela TreeView."""
    # strings limpas
    def s(x): 
        return (str(x).strip() if x is not None and str(x).strip().lower() not in ("nan","none","") else "")

    # CNPJ: mantém máscara se já vier, senão apenas dígitos
    def only_digits(x): 
        return "".join(ch for ch in x if ch.isdigit())

    out = {
        "id": s(r.get("id")),
        "carteira": s(r.get("carteira")),
        "cnpj": s(r.get("cnpj")),
        "raiz_cnpj": s(r.get("raiz_cnpj")),
        "fundos": s(r.get("fundos")),
        "legado": self._fidc_coerce_legado(s(r.get("legado"))),
        "tp": s(r.get("tp")),
        "metod": s(r.get("metod")),
        "data_esp": self._fidc_parse_date(s(r.get("data_esp"))),
    }

    # se raiz_cnpj vazio mas tem cnpj, tenta derivar (8 primeiros dígitos)
    if not out["raiz_cnpj"] and out["cnpj"]:
        dig = only_digits(out["cnpj"])
        if len(dig) >= 8:
            out["raiz_cnpj"] = f"{int(dig[:8]):,}".replace(",", ".")  # formata como 8.888.888 (igual seu exemplo)

    return out

def _fidc_coerce_legado(self, val: str) -> str:
    """Normaliza LEGADO para FROMTIS / DRIVE / HÍBRIDO (ou vazio)."""
    v = (val or "").strip().upper()
    # aceita abreviações
    if v in ("F","FROMTIS"):
        return "FROMTIS"
    if v in ("D","DRIVE"):
        return "DRIVE"
    if v in ("H","HIBRIDO","HÍBRIDO","D/F","F/D","D - F","F - D","D/F "):
        return "HÍBRIDO"
    return v  # se vier correto, mantém; senão, deixa visível pra editar depois


def _fidc_save_rows_merge(self, new_rows: list[dict]) -> tuple[int,int]:
    """
    Salva mesclando por ID:
      - se ID novo: adiciona
      - se ID existe: atualiza aquele registro
    Usa self.fidc_reg se existir; senão, JSON via _fidcs_db_load/_fidcs_db_save.
    Retorna (novos, atualizados)
    """
    # carrega base atual
    rows = []
    use_registry = False
    try:
        if hasattr(self, "fidc_reg") and hasattr(self.fidc_reg, "all"):
            rows = list(self.fidc_reg.all())
            use_registry = True
        else:
            rows = _fidcs_db_load()
    except Exception:
        rows = []

    # index por ID
    idx = {str(r.get("id","")).strip(): i for i, r in enumerate(rows)}
    added = 0
    updated = 0

    for r in new_rows:
        rid = str(r.get("id","")).strip()
        if rid and rid in idx:
            rows[idx[rid]] = r
            updated += 1
        else:
            rows.append(r)
            added += 1

    # salva
    if use_registry:
        if hasattr(self.fidc_reg, "save"):
            self.fidc_reg.save(rows)
        else:
            _fidcs_db_save(rows)
    else:
        _fidcs_db_save(rows)

    return added, updated



def _fidc_parse_date(self, txt: str) -> str:
    """Tenta normalizar data para dd/mm/aaaa; retorna texto original se não conseguir."""
    if not txt:
        return ""
    from datetime import datetime
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(txt, fmt).strftime("%d/%m/%Y")
        except Exception:
            pass
    return txt  # mantém como veio





def excel_apply_default_format(writer, sheet_name, df, numeric_cols=None, header_color="#1F4E78"):
    wb = writer.book
    ws = writer.sheets[sheet_name]
    header_fmt = wb.add_format({"bold": True, "bg_color": header_color, "font_color": "#FFFFFF", "border": 1, "align":"center"})
    num_fmt = wb.add_format({"num_format": "#,##0.00"})
    # cabeçalho estilizado
    for col_idx, name in enumerate(df.columns):
        ws.write(0, col_idx, name, header_fmt)
        ws.set_column(col_idx, col_idx, max(12, min(28, len(str(name)) + 2)))
    # numéricos
    if numeric_cols:
        for col_idx, name in enumerate(df.columns):
            if name in numeric_cols:
                ws.set_column(col_idx, col_idx, 18, num_fmt)
    # autofiltro
    ws.autofilter(0, 0, max(1, len(df)), max(0, len(df.columns)-1))

def _safe_os_replace(src_tmp, dst_final):
    # grava em tmp e faz replace atômico (evita corromper arquivo se falhar no meio)
    os.makedirs(os.path.dirname(dst_final), exist_ok=True)
    os.replace(src_tmp, dst_final)

def save_excel_safely_with_split(base_out_path: str, sheets: list, max_rows_per_sheet: int = 200_000, meta: dict | None = None):

    import pandas as pd
    from datetime import datetime

    dir_final, nome_final = os.path.dirname(base_out_path), os.path.basename(base_out_path)
    fd, tmp_path = tempfile.mkstemp(prefix="excel_", suffix=".xlsx")
    os.close(fd)

    try:
        with pd.ExcelWriter(tmp_path, engine="xlsxwriter") as writer:
            # meta
            if meta:
                dfm = pd.DataFrame([meta])
                dfm.to_excel(writer, sheet_name="_meta", index=False)
                # esconder meta
                try:
                    writer.sheets["_meta"].hide()
                except Exception:
                    pass

            for sheet_name, df, num_cols in sheets:
                if df is None or getattr(df, "empty", False):
                    # cria aba vazia para manter estrutura
                    import pandas as pd
                    df = pd.DataFrame()
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    excel_apply_default_format(writer, sheet_name, df, numeric_cols=num_cols)
                    continue

                # split
                n = len(df)
                if n <= max_rows_per_sheet:
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    excel_apply_default_format(writer, sheet_name, df, numeric_cols=num_cols)
                else:
                    # quebras
                    parts = (n + max_rows_per_sheet - 1) // max_rows_per_sheet
                    for i in range(parts):
                        start = i * max_rows_per_sheet
                        end = min(n, (i+1) * max_rows_per_sheet)
                        df_part = df.iloc[start:end]
                        sn = f"{sheet_name}_p{i+1}"
                        df_part.to_excel(writer, sheet_name=sn, index=False)
                        excel_apply_default_format(writer, sn, df_part, numeric_cols=num_cols)

        # finaliza com replace atômico
        _safe_os_replace(tmp_path, os.path.join(dir_final, nome_final))
        return os.path.join(dir_final, nome_final)
    finally:
        # se der erro antes do replace
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


# Backup rotation helper




# Diagnostics helper

def write_diagnostics(out_folder: str | Path):
    import platform, locale, sys
    from datetime import datetime
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    path = out_folder / f"diagnostico_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{APP_NAME} {APP_VERSION}\n")
            f.write(f"Python: {sys.version}\n")
            f.write(f"OS: {platform.platform()}\n")
            f.write(f"Locale: {locale.getdefaultlocale()}\n")
            f.write(f"Encoding FS: {sys.getfilesystemencoding()}\n")
        logger.info(f"Diagnóstico salvo em: {path}")
    except Exception as e:
        logger.warning(f"Falha ao salvar diagnóstico: {e}")




# Cancel helper
def should_cancel(cancel_flag, counter, step=1000):

    if (counter % step) == 0:
        return cancel_flag.is_set()
    return False




def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))  # atomic em Windows e Unix
    except Exception:
        # se der errado, tenta remover o tmp
        try:
            os.remove(tmp_name)
        except Exception:
            pass
        raise




class AppConfig:
    def __init__(self, path: Path):
        self.path = path
        # deep copy seguro
        self._data = json.loads(json.dumps(DEFAULT_CONFIG))
        self.load()

  

    def load(self):
        if self.path.exists():
            try:
                self._data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                logger.warning("Config inválido. Carregando padrão.")

    def save(self):
        try:
            _atomic_write_text(
                self.path,
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Falha ao salvar config: {e}")



    def get(self, *keys, default=None):
        d = self._data
        for k in keys:
            d = d.get(k, {})
        return d if d != {} else default

    def set(self, value, *keys):
        d = self._data
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value




class TaskRunner:
    def __init__(self, tk_root, ui_on_start, ui_on_finish, ui_on_error, ui_on_progress=None):
        self.tk_root = tk_root
        self._thread = None
        self._cancel_flag = threading.Event()
        self.ui_on_start = ui_on_start
        self.ui_on_finish = ui_on_finish
        self.ui_on_error = ui_on_error
        self.ui_on_progress = ui_on_progress

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, target, *args, **kwargs):
        if self.is_running():
            messagebox.showinfo(APP_NAME, "Já existe uma tarefa em execução. Aguarde terminar.")
            return
        self._cancel_flag.clear()

        def ui_call(fn, *a, **k):
            try:
                self.tk_root.after(0, lambda: fn(*a, **k))
            except Exception:
                pass

        def wrapper():
            try:
                ui_call(self.ui_on_start)

                def progress(pct: int):
                    if self.ui_on_progress is not None:
                        ui_call(self.ui_on_progress, int(pct))

                k = dict(kwargs)
                if self.ui_on_progress is not None and "progress" not in k:
                    k["progress"] = progress

                target(self._cancel_flag, *args, **k)
                ui_call(self.ui_on_finish)
            except Exception as e:
                logger.exception("Erro na execução da tarefa")
                ui_call(self.ui_on_error, e)

        self._thread = threading.Thread(target=wrapper, daemon=True)
        self._thread.start()

    def cancel(self):
        if self.is_running():
            self._cancel_flag.set()
            logger.info("Sinal de cancelamento enviado. A tarefa irá encerrar no próximo ponto seguro.")
        else:
            logger.info("Nenhuma tarefa em execução para cancelar.")



def conciliacao_concilia_saldo_pdd(cancel_flag, app_self, pasta_xmls: str, caminho_mc: str,
                                   pasta_saida: str, nome_aba: str | None = None, progress=None):

    # validações
    if not os.path.isdir(pasta_xmls) or not _has_any_xml_recursive(pasta_xmls):
        logger.error(f"Nenhum XML encontrado (inclusive subpastas) em: {pasta_xmls}")
        return
    if not os.path.isfile(caminho_mc):
        logger.error(f"Planilha MC não encontrada: {caminho_mc}")
        return

    script_path = Path(__file__).resolve()
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = Path(pasta_saida) / f"Conciliacao_XML_vs_MC_por_Raiz_{ts}.xlsx"

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        snap = _snapshot_fidcs_to_temp(app_self=app_self)
        env["FIDCS_DB"] = str(snap)
        logger.info(f"[Conciliação] Base FIDCs (snapshot p/ worker): {snap}")
    except Exception as e:
        logger.warning(f"[Conciliação] Não consegui criar snapshot de FIDCs: {e}")
        try:
            env["FIDCS_DB"] = str(FIDC_DB_PATH)
            logger.info(f"[Conciliação] Tentando base padrão: {FIDC_DB_PATH}")
        except Exception:
            pass

    cmd = [sys.executable, str(script_path), "--worker-concilia",
           "--xml-dir", str(pasta_xmls), "--mc", str(caminho_mc), "--out", str(out)]
    if nome_aba:
        cmd += ["--sheet", str(nome_aba)]

    logger.info(f"Iniciando conciliação (subprocesso). XMLs: {pasta_xmls} | MC: {caminho_mc}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, env=env)

    def reader(pipe):
        for line in iter(pipe.readline, ''):
            logger.info(line.rstrip())
        try:
            pipe.close()
        except Exception:
            pass

    t = threading.Thread(target=reader, args=(proc.stdout,), daemon=True)
    t.start()

    while True:
        rc = proc.poll()
        if rc is not None:
            break
        if cancel_flag.is_set():
            logger.info("Cancelando conciliação…")
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
            finally:
                logger.info("Processo de conciliação encerrado.")
                return
        time.sleep(0.2)

    t.join(timeout=2)
    if proc.returncode == 0:
        logger.info(f"Conciliação concluída. Arquivo gerado: {out}")
    else:
        logger.error(f"Conciliação terminou com código {proc.returncode}.")





# Leitura Saldos e PDD

# Ajuste regional seguro (evita crash em ambientes sem pt_BR)
try:
    locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except locale.Error:
    locale.setlocale(locale.LC_ALL, '')

# Mapeamento de tipo de pessoa (só para ficar bonitinho)
mapa_tipo_pessoa = {
    '1': 'pessoa física - CPF',
    '2': 'pessoa jurídica - CNPJ',
    '3': 'pessoa física no exterior',
    '4': 'pessoa jurídica no exterior',
    '5': 'pessoa física sem CPF',
    '6': 'pessoa jurídica sem CNPJ',
}

estrategia_tp = {
    '1': 'CPF',
    '2': 'CNPJ',
    '3': 'CPF',
    '4': 'CNPJ',
    '5': 'CPF',
    '6': 'CNPJ',
}

def formatar_valor(valor):
    try:
        return locale.format_string('%.2f', float(valor), grouping=True)
    except Exception:
        return valor

def extrair_atributos(caminho_xml, repetir_por_inf=False):

    atributos_extras = []
    tree = ET.parse(caminho_xml)
    root = tree.getroot()

    dtbase = root.attrib.get('DtBase', 'N/A')
    cnpj_arquivo = root.attrib.get('CNPJ', 'N/A')
    
    for cliente in root.findall('Cli'):
        cd_cliente = cliente.attrib.get('Cd', 'N/A')
        tp_pessoa = cliente.attrib.get('Tp', 'N/A')
        portecli = cliente.attrib.get('PorteCli', 'Vazio')
        fatanual = cliente.attrib.get('FatAnual', 'Vazio')
        inirelactcli = cliente.attrib.get('IniRelactCli', 'N/A')

        tipo_id = estrategia_tp.get(tp_pessoa)
        descricao_tp = mapa_tipo_pessoa.get(tp_pessoa, 'Desconhecido')

        for operacao in cliente.findall('Op'):
            if tipo_id == 'CPF':
                dtcli = cd_cliente
            elif tipo_id == 'CNPJ':
                dtcli = operacao.attrib.get('DetCli', 'N/A')
            else:
                dtcli = 'N/A'

            mod = operacao.attrib.get('Mod', 'N/A')
            contrt = operacao.attrib.get('Contrt', 'N/A')
            dtcontr = operacao.attrib.get('DtContr', 'N/A')
            dtvencop = operacao.attrib.get('DtVencOp', 'N/A')
            carac_especial = operacao.attrib.get('CaracEspecial', 'Vazio')
            taxa_efetiva = operacao.attrib.get('TaxEft', 'Vazio')
            dias_atraso = operacao.attrib.get('DiaAtraso', 'Vazio')
            natuop = operacao.attrib.get('NatuOp', 'Vazio')
            vlrproxparcela = operacao.attrib.get('VlrProxParcela', 'N/A')
            dtaproxparcela = operacao.attrib.get('DtaProxParcela', 'N/A')
            qtdparcelas = operacao.attrib.get('QtdParcelas', 'N/A')
            ipoc = operacao.attrib.get('IPOC', 'N/A')
            indx = operacao.attrib.get('Indx', 'N/A')
            percindx = operacao.attrib.get('PercIndx', 'N/A')

            cont_inst_fin = operacao.find('ContInstFinRes4966')
            cart_prov_min = cont_inst_fin.attrib.get('CartProvMin', 'Vazio') if cont_inst_fin is not None else 'Vazio'
            est_inst_fin = cont_inst_fin.attrib.get('EstInstFin', 'Vazio') if cont_inst_fin is not None else 'Vazio'
            tje = cont_inst_fin.attrib.get('TJE', 'Vazio') if cont_inst_fin is not None else 'Vazio'
            clasatfin = cont_inst_fin.attrib.get('ClasAtFin', 'Vazio') if cont_inst_fin is not None else 'Vazio'
            vlrcontbr = cont_inst_fin.attrib.get('VlrContBr', 'Vazio') if cont_inst_fin is not None else 'Vazio'

            try:
                provisao_valor = float(operacao.attrib.get('ProvConsttd', 0.0))
            except Exception:
                provisao_valor = 0.0
            provisao_formatada = formatar_valor(provisao_valor)

            saldo = 0.0
            codigos_venc = set()
            for venc in operacao.findall('Venc'):
                for nome, valor in venc.attrib.items():
                    codigos_venc.add(nome)
                    try:
                        saldo += float(valor)
                    except Exception:
                        continue
            saldo_formatado = formatar_valor(saldo)
            codigos_venc_ordenado = ';'.join(sorted(codigos_venc)) if codigos_venc else 'Vazio'

            infs = operacao.findall('Inf')
            inf_tps = set()
            idents = set()
            pares_cd_valor = []
            for inf in infs:
                tp = inf.attrib.get('Tp')
                if tp:
                    inf_tps.add(tp)
                ident = inf.attrib.get('Ident')
                if ident:
                    idents.add(ident)
                cd_inf = inf.attrib.get('Cd')
                val_inf = inf.attrib.get('Valor')
                if cd_inf is not None or val_inf is not None:
                    pares_cd_valor.append((tp, cd_inf, val_inf))

            gars = operacao.findall('Gar')
            gar_tps = set()
            for gar in gars:
                tp = gar.attrib.get('Tp')
                if tp:
                    gar_tps.add(tp)

            base_row = {
                "Nome do arquivo XML": os.path.basename(caminho_xml),
                "DATA_BASE": dtbase,
                "CNPJ": cnpj_arquivo,
                "TipoPessoaDesc": descricao_tp,
                "CPF/CNPJ": dtcli,
                "Mod": mod if mod else "Vazio",
                "Contrt": contrt,
                "DtContr": dtcontr,
                "DtVencOp": dtvencop,
                "Saldo (Venc)": saldo_formatado,
                "Provisão": provisao_formatada,
                "NatuOp": natuop,
                "Inf Tp": ';'.join(sorted(inf_tps)) if inf_tps else 'Vazio',
                "Ident": ';'.join(sorted(idents)) if idents else 'Vazio',
                "Gar Tp": ';'.join(sorted(gar_tps)) if gar_tps else 'Vazio',
                "CaracEspecial": carac_especial if carac_especial else 'Vazio',
                "CartProvMin": cart_prov_min,
                "EstInstFin": est_inst_fin,
                "TJE": tje,
                "TaxEft": taxa_efetiva,
                "DiasAtraso": dias_atraso,
                "ClasAtFin": clasatfin,
                "VlrContBr": vlrcontbr,
                "PorteCli": portecli,
                "FatAnual": fatanual,
                "IniRelactCli": inirelactcli,
                "DtaProxParcela": dtaproxparcela,
                "VlrProxParcela": vlrproxparcela,
                "QtdParcelas": qtdparcelas,
                "IPOC": ipoc,
                "CodsVenc": codigos_venc_ordenado,
                "Indx": indx,
                "PercIndx": percindx,
            }

            if repetir_por_inf and pares_cd_valor:
                # 1 linha por <Inf>
                for tp, cd_inf, val_inf in pares_cd_valor:
                    row = dict(base_row)
                    row["Inf_Tp_linha"] = tp if tp else "Vazio"
                    row["Inf_Cd"] = cd_inf if cd_inf is not None else "Vazio"
                    row["Inf_Valor"] = val_inf if val_inf is not None else "Vazio"
                    row["Inf_Valor_fmt"] = (
                        formatar_valor(val_inf) if val_inf is not None else "Vazio"
                    )
                    atributos_extras.append(row)
            else:
                # visão agregada (listas compactas)
                cds = [cd for _, cd, _ in pares_cd_valor if cd is not None]
                vals = [val for _, _, val in pares_cd_valor if val is not None]
                vals_fmt = [formatar_valor(val) for val in vals] if vals else []
                row = dict(base_row)
                row["Inf_Cd"] = ';'.join(cds) if cds else "Vazio"
                row["Inf_Valor"] = ';'.join(vals) if vals else "Vazio"
                row["Inf_Valor_fmt"] = ';'.join(vals_fmt) if vals_fmt else "Vazio"
                atributos_extras.append(row)

    # Agregados (mantém colunas compatíveis)
    for agreg in root.findall('Agreg'):
        saldo_agreg = 0.0
        for venc in agreg.findall('Venc'):
            for valor in venc.attrib.values():
                try:
                    saldo_agreg += float(valor)
                except Exception:
                    continue

        try:
            pdd_agreg = float(agreg.attrib.get('ProvConsttd', 0.0))
        except Exception:
            pdd_agreg = 0.0

        atributos_extras.append({
            "Nome do arquivo XML": os.path.basename(caminho_xml),
            "DATA_BASE": dtbase,
            "CNPJ": cnpj_arquivo,
            "TipoPessoaDesc": "AGREGADO",
            "CPF/CNPJ": "AGREGADO",
            "Mod": agreg.attrib.get("Mod", "N/A"),
            "Contrt": "N/A",
            "DtContr": "N/A",
            "DtVencOp": "N/A",
            "Saldo (Venc)": formatar_valor(saldo_agreg),
            "Provisão": formatar_valor(pdd_agreg),
            "NatuOp": "N/A",
            "Inf Tp": "N/A",
            "Ident": "N/A",
            "Gar Tp": "N/A",
            "CaracEspecial": "N/A",
            "CartProvMin": "N/A",
            "EstInstFin": "N/A",
            "TJE": "N/A",
            "TaxEft": "N/A",
            "DiasAtraso": "N/A",
            "ClasAtFin": "N/A",
            "VlrContBr": "N/A",
            "PorteCli": "N/A",
            "FatAnual": "N/A",
            "IniRelactCli": "N/A",
            "DtaProxParcela": "N/A",
            "VlrProxParcela": "N/A",
            "QtdParcelas": "N/A",
            "IPOC": "N/A",
            "CodsVenc": "N/A",
            "Indx": "N/A",
            "PercIndx": "N/A",
            "Inf_Cd": "N/A",
            "Inf_Valor": "N/A",
            "Inf_Valor_fmt": "N/A",
        })

    return atributos_extras


def extrair_inf_cd_valor(caminho_xml):
    linhas = []
    tree = ET.parse(caminho_xml)
    root = tree.getroot()

    dtbase = root.attrib.get('DtBase', 'N/A')
    cnpj_arquivo = root.attrib.get('CNPJ', 'N/A')

    for cliente in root.findall('Cli'):
        cd_cliente = cliente.attrib.get('Cd', 'N/A')
        tp_pessoa = cliente.attrib.get('Tp', 'N/A')
        tipo_id = estrategia_tp.get(tp_pessoa, None)
        ident_cli = cd_cliente if tipo_id == 'CPF' else "N/A"

        for operacao in cliente.findall('Op'):
            if tipo_id == 'CNPJ':
                ident_cli = operacao.attrib.get('DetCli', 'N/A')

            mod = operacao.attrib.get('Mod', 'N/A')
            contrt = operacao.attrib.get('Contrt', 'N/A')
            ipoc = operacao.attrib.get('IPOC', 'N/A')

            for inf in operacao.findall('Inf'):
                tp = inf.attrib.get('Tp', 'Vazio')
                cd = inf.attrib.get('Cd')
                valor = inf.attrib.get('Valor')

                if cd is not None or valor is not None:
                    linhas.append({
                        "Nome do arquivo XML": os.path.basename(caminho_xml),
                        "DATA_BASE": dtbase,
                        "CNPJ": cnpj_arquivo,
                        "CPF/CNPJ": ident_cli,
                        "Mod": mod,
                        "Contrt": contrt,
                        "IPOC": ipoc,
                        "Inf_Tp": tp,
                        "Cd": cd if cd is not None else "Vazio",
                        "Valor": valor if valor is not None else "Vazio",
                        "Valor_fmt": formatar_valor(valor) if valor is not None else "Vazio",
                    })
    return linhas






def carregar_operacoes_por_ipoc_stream(caminho_xml: str):
    logger.info(f"Iniciando leitura do XML ANTERIOR em streaming: {caminho_xml}")

    operacoes = {}
    context = ET.iterparse(caminho_xml, events=("end",))
    for event, elem in context:
        if elem.tag == "Cli":
            cli_attrib = dict(elem.attrib)
            for op in elem.findall("Op"):
                op_attrib = dict(op.attrib)
                ipoc = op_attrib.get("IPOC")
                if not ipoc:
                    continue

                inf_tps = set()
                for inf in op.findall("Inf"):
                    tp = inf.attrib.get("Tp", "")
                    if tp:
                        inf_tps.add(tp)

                operacoes[ipoc] = {
                    "cli_attrib": cli_attrib,
                    "op_attrib": op_attrib,
                    "inf_tps": inf_tps,
                }
                
            if len(operacoes) % 50000 == 0:
                logger.info(f"Processados {len(operacoes)} IPOCs do XML ANTERIOR...")

            elem.clear()
    return operacoes


def coletar_ipocs_atual_stream(caminho_xml: str):
    logger.info(f"Coletando IPOCs do XML ATUAL em streaming: {caminho_xml}")

    ipocs_atual = set()
    context = ET.iterparse(caminho_xml, events=("start", "end"))
    root = None

    for event, elem in context:
        if event == "start" and root is None:
            root = elem
            continue

        if event == "end" and elem.tag == "Op":
            ipoc = elem.attrib.get("IPOC")
            if ipoc:
                ipocs_atual.add(ipoc)

        elif event == "end" and elem.tag == "Cli" and root is not None:

            root.remove(elem)
            
            if len(ipocs_atual) % 50000 == 0:
                logger.info(f"Coletados {len(ipocs_atual)} IPOCs do XML ATUAL...")


    return ipocs_atual


def escolher_tp_saida(qtd: int) -> str | None:
    import tkinter as tk
    from tkinter import Toplevel, Label, Button, Radiobutton, StringVar, messagebox

    root = tk.Tk()
    root.withdraw()

    win = Toplevel(root)
    win.title("Escolha o código de saída (Inf Tp)")
    win.grab_set()

    Label(
        win,
        text=(
            f"Foram encontrados {qtd} contratos desaparecidos.\n"
            f"Escolha abaixo o Tp de saída a ser aplicado:"
        ),
        justify="left"
    ).pack(padx=15, pady=(10, 5), anchor="w")


    escolha_tp = StringVar(master=win, value=None)

    opcoes = [
        ("0301 — saída 0301", "0301"),
        ("0302 — saída 0302", "0302"),
        ("0308 — saída 0308", "0308"),
        ("0399 — saída 0399", "0399"),
    ]

    for texto, valor in opcoes:
        Radiobutton(
            win,
            text=texto,
            variable=escolha_tp,
            value=valor,
            anchor="w",
            justify="left"
        ).pack(padx=20, anchor="w")

    saida = {"tp": None}

    def confirmar():
        val = escolha_tp.get()
        if not val:
            messagebox.showwarning("Selecione uma opção", "Por favor, selecione um Tp antes de continuar.")
            return
        saida["tp"] = val
        win.destroy()

    def cancelar():
        saida["tp"] = None
        win.destroy()

    frame_btn = tk.Frame(win)
    frame_btn.pack(pady=15)

    Button(frame_btn, text="Cancelar", width=12, command=cancelar).pack(side="left", padx=10)
    Button(frame_btn, text="OK", width=12, command=confirmar).pack(side="right", padx=10)

    # Centralizar
    win.update_idletasks()
    w = win.winfo_width()
    h = win.winfo_height()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 2
    win.geometry(f"{w}x{h}+{x}+{y}")

    root.wait_window(win)
    root.destroy()

    return saida["tp"]



def incluir_saidas_no_xml_stream(xml_atual, desaparecidos, anterior, tp_saida):

    import shutil
    import os
    from datetime import datetime
    from pathlib import Path
    from tkinter import messagebox

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(xml_atual)
    backup = out_path.with_name(f"{out_path.stem}_backup_{ts}.xml")
    shutil.copy2(out_path, backup)  # backup do original

    src = str(backup)
    tmp_out = out_path.with_name(f"{out_path.stem}_temp_{ts}.xml")


    cd_to_ipocs = {}
    for ipoc in desaparecidos:
        cd = anterior[ipoc]["cli_attrib"].get("Cd")
        cd_to_ipocs.setdefault(cd, []).append(ipoc)

    with tmp_out.open("w", encoding="utf-8") as f_out:
        context = ET.iterparse(src, events=("start", "end"))
        root_tag = None
        root_attrib = None

        for event, elem in context:
            if event == "start" and root_tag is None:
 
                root_tag = elem.tag
                root_attrib = dict(elem.attrib)
                attrs_str = "".join(f' {k}="{v}"' for k, v in root_attrib.items())
                f_out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
                f_out.write(f"<{root_tag}{attrs_str}>\n")
                continue

            if event == "end" and elem.tag == "Cli":
                cd_cli_atual = elem.attrib.get("Cd")

                if cd_cli_atual in cd_to_ipocs:
                    for ipoc in cd_to_ipocs[cd_cli_atual]:
                        dados = anterior[ipoc]
                        op_attr_original = dados["op_attrib"]
                        op_attr = dict(op_attr_original)


                        for chave in ("ProvConsttd", "DiaAtraso", "VlrProxParcela", "QtdParcelas", "DtaProxParcela"):
                            op_attr.pop(chave, None)

                        nova_op = ET.Element("Op", op_attr)
                        ET.SubElement(nova_op, "Inf", {"Tp": tp_saida})
                        elem.append(nova_op)

     
                    del cd_to_ipocs[cd_cli_atual]

                f_out.write(ET.tostring(elem, encoding="unicode"))
                elem.clear()

            elif event == "end" and elem.tag == root_tag:

                elem.clear()



        for cd_cli, ipocs in cd_to_ipocs.items():
            cli_attrib = anterior[ipocs[0]]["cli_attrib"]
            cli_elem = ET.Element("Cli", cli_attrib)

            for ipoc in ipocs:
                dados = anterior[ipoc]
                op_attr_original = dados["op_attrib"]
                op_attr = dict(op_attr_original)
                for chave in ("ProvConsttd", "DiaAtraso", "VlrProxParcela", "QtdParcelas", "DtaProxParcela"):
                    op_attr.pop(chave, None)
                op_elem = ET.Element("Op", op_attr)
                ET.SubElement(op_elem, "Inf", {"Tp": tp_saida})
                cli_elem.append(op_elem)

            f_out.write(ET.tostring(cli_elem, encoding="unicode"))

        # Fecha a tag raiz
        f_out.write(f"</{root_tag}>\n")

    # Substitui o XML atual pelo temporário
    os.replace(tmp_out, out_path)

    messagebox.showinfo(
        "Fechamento 3040",
        f"Processo concluído.\n"
        f"Backup salvo em:\n{backup}\n\n"
        f"XML atualizado com {len(desaparecidos)} operações de saída (Tp {tp_saida})."
    )


def verificar_saidas(xml_anterior: str, xml_atual: str):

    from tkinter import messagebox
    from pathlib import Path
    from datetime import datetime


    anterior = carregar_operacoes_por_ipoc_stream(xml_anterior)


    ipocs_atual = coletar_ipocs_atual_stream(xml_atual)


    desaparecidos = []
    for ipoc, dados in anterior.items():
        # Ignora operações que já tinham qualquer Inf Tp começando com "03"
        if any(tp.startswith("03") for tp in dados["inf_tps"]):
            continue

        if ipoc not in ipocs_atual:
            desaparecidos.append(ipoc)

    if not desaparecidos:
        messagebox.showinfo(
            "Geração de Saídas",
            "Nenhum contrato da base anterior deixou de existir na base atual.\n"
            "Nenhuma saída será gerada."
        )
        return

    tp_saida = escolher_tp_saida(len(desaparecidos))
    if not tp_saida:
        messagebox.showinfo(
            "Geração de Saídas",
            "Operação cancelada pelo usuário. Nenhuma saída foi incluída."
        )
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    xml_path = Path(xml_atual)
    txt_path = xml_path.with_name(f"{xml_path.stem}_saidas_nao_identificadas_{ts}.txt")
    txt_path.write_text(
        f"Contratos que existiam na base anterior e sumiram na atual\n"
        f"(apenas operações sem saída 03xx na base anterior).\n"
        f"Tp de saída selecionado: {tp_saida}\n\n"
        + "\n".join(desaparecidos),
        encoding="utf-8"
    )

    incluir_saidas_no_xml_stream(xml_atual, desaparecidos, anterior, tp_saida)






def conciliacao_ler_saldo_pdd_xmls(cancel_flag, pasta_xmls: str, pasta_saida: str, progress=None):
    import xml.etree.ElementTree as ET
    import pandas as pd
    from datetime import datetime

    CHUNK_SIZE = 64 * 1024  # 64 KiB

    def _to_float(v):
        try:
            return float(str(v).strip().replace(',', '.'))
        except Exception:
            return 0.0

    def processar_xml_incremental(caminho_xml: str):
        if os.path.getsize(caminho_xml) == 0:
            logger.warning(f"Arquivo vazio, ignorado: {caminho_xml}")
            return None

        saldo_cli = pdd_cli = prejuizo_cli = 0.0
        saldo_agreg = pdd_agreg = prejuizo_agreg = 0.0
        classop_totals = {}

        parser = ET.XMLPullParser(events=("end",))
        try:
            with open(caminho_xml, 'rb') as fh:
                while True:
                    if cancel_flag.is_set():
                        return "__CANCEL__"
                    chunk = fh.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    parser.feed(chunk)
                    for _event, elem in parser.read_events():
                        if elem.tag == "Cli":
                            for operacao in elem.findall('Op'):
                                classop = operacao.attrib.get('ClassOp', elem.attrib.get('ClassOp', 'N/A'))
                                classop_saldo = classop_pdd = classop_prejuizo = 0.0
                                for vencimento in operacao.findall('Venc'):
                                    for vcod, valor in vencimento.attrib.items():
                                        val = _to_float(valor)
                                        if str(vcod).startswith(("v310", "v320", "v330")):
                                            prejuizo_cli += val
                                            classop_prejuizo += val
                                        else:
                                            saldo_cli += val
                                            classop_saldo += val
                                pdd_value = _to_float(operacao.attrib.get('ProvConsttd', 0))
                                pdd_cli += pdd_value
                                classop_pdd += pdd_value
                                if classop not in classop_totals:
                                    classop_totals[classop] = {
                                        'Saldo_Cli': 0.0, 'PDD_Cli': 0.0, 'Prejuizo_Cli': 0.0,
                                        'Saldo_Agreg': 0.0, 'PDD_Agreg': 0.0, 'Prejuizo_Agreg': 0.0
                                    }
                                classop_totals[classop]['Saldo_Cli'] += classop_saldo
                                classop_totals[classop]['PDD_Cli'] += classop_pdd
                                classop_totals[classop]['Prejuizo_Cli'] += classop_prejuizo
                            elem.clear()
                        elif elem.tag == "Agreg":
                            classop = elem.attrib.get('ClassOp', 'N/A')
                            classop_saldo = classop_pdd = classop_prejuizo = 0.0
                            for vencimento in elem.findall('Venc'):
                                for vcod, valor in vencimento.attrib.items():
                                    val = _to_float(valor)
                                    if str(vcod).startswith(("v310", "v320", "v330")):
                                        prejuizo_agreg += val
                                        classop_prejuizo += val
                                    else:
                                        saldo_agreg += val
                                        classop_saldo += val
                            pdd_value = _to_float(elem.attrib.get('ProvConsttd', 0))
                            pdd_agreg += pdd_value
                            classop_pdd += pdd_value
                            if classop not in classop_totals:
                                classop_totals[classop] = {
                                    'Saldo_Cli': 0.0, 'PDD_Cli': 0.0, 'Prejuizo_Cli': 0.0,
                                    'Saldo_Agreg': 0.0, 'PDD_Agreg': 0.0, 'Prejuizo_Agreg': 0.0
                                }
                            classop_totals[classop]['Saldo_Agreg'] += classop_saldo
                            classop_totals[classop]['PDD_Agreg'] += classop_pdd
                            classop_totals[classop]['Prejuizo_Agreg'] += classop_prejuizo
                            elem.clear()
        except ET.ParseError as e:
            logger.error(f"Erro de parsing em {caminho_xml}: {e}")
            return None

        saldo_total = saldo_cli + saldo_agreg
        pdd_total = pdd_cli + pdd_agreg
        prejuizo_total = prejuizo_cli + prejuizo_agreg
        return saldo_total, pdd_total, prejuizo_total, classop_totals

    pasta_xmls = Path(pasta_xmls)
    pasta_saida = Path(pasta_saida)
    xml_files = [f for f in os.listdir(pasta_xmls) if f.lower().endswith('.xml')]
    if not xml_files:
        logger.info("Nenhum XML encontrado na pasta informada.")
        return
    logger.info(f"Iniciando leitura de {len(xml_files)} XML(s)…")

    import pandas as pd
    resumo_rows, detalhado_rows = [], []
    for i, xml_name in enumerate(sorted(xml_files), start=1):
        if cancel_flag.is_set():
            logger.info("Leitura cancelada pelo usuário.")
            return
        caminho_xml = str(pasta_xmls / xml_name)
        logger.info(f"[{i}/{len(xml_files)}] Processando: {xml_name}")
        resultado = processar_xml_incremental(caminho_xml)
        if resultado == "__CANCEL__":  # quando o usuário cancelar
            logger.info("Leitura cancelada pelo usuário.")
            return
        if not resultado:
            logger.warning(f"Ignorado por erro/arquivo vazio: {xml_name}")
            continue
        saldo, pdd, prejuizo, classop_totals = resultado
        resumo_rows.append({"Arquivo": xml_name, "Saldo_Total": saldo, "PDD_Total": pdd, "Prejuizo_Total": prejuizo})
        for classop, tot in classop_totals.items():
            detalhado_rows.append({"Arquivo": xml_name, "ClassOp": classop, **tot})

    if not resumo_rows:
        logger.info("Nenhum dado válido para exportar.")
        return
    if cancel_flag.is_set():
        logger.info("Cancelado antes da exportação do Excel.")
        return

    from datetime import datetime
    df_resumo = pd.DataFrame(resumo_rows)
    df_detalhe = pd.DataFrame(detalhado_rows) if detalhado_rows else pd.DataFrame(columns=[
        "Arquivo","ClassOp","Saldo_Cli","PDD_Cli","Prejuizo_Cli","Saldo_Agreg","PDD_Agreg","Prejuizo_Agreg"
    ])
    out_path = Path(pasta_saida) / f"relatorio_saldos_pdd_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    try:
        with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
            df_resumo.to_excel(writer, sheet_name="Resumo", index=False)
            wb = writer.book
            ws1 = writer.sheets.get("Resumo")
            numfmt = wb.add_format({'num_format': '#,##0.00'})
            if ws1 is not None:
                ws1.set_column('B:D', 18, numfmt)
            if not df_detalhe.empty:
                df_detalhe.to_excel(writer, sheet_name="Por_Arquivo_ClassOp", index=False)
                ws2 = writer.sheets.get("Por_Arquivo_ClassOp")
                if ws2 is not None:
                    ws2.set_column('C:H', 18, numfmt)
    except Exception as e:
        logger.exception(f"Falha ao escrever Excel: {e}")
        raise
    logger.info(f"Relatório gerado: {out_path}")



# Lê tags / atributos específicos
def conciliacao_le_tags_atributos(cancel_flag, pasta_xmls: str, pasta_saida: str, selected_fields: list[str], progress=None):
    import xml.etree.ElementTree as ET
    import pandas as pd
    from datetime import datetime

    # Campos possíveis (mantém a ordem; "Cd" e "Valor na inf (valor formatado da inf)" no FIM)
    ALL_FIELDS = [
        "Nome do arquivo XML", "DATA_BASE", "CNPJ",
        "TipoPessoaDesc", "CPF/CNPJ", "Mod", "Contrt", "DtContr", "DtVencOp", "OrigemRec",
        "Saldo (Venc)", "Provisão", "NatuOp", "Inf Tp", "Ident", "Gar Tp", "CaracEspecial",
        "CartProvMin", "EstInstFin", "TJE", "TaxEft", "DiasAtraso", "ClasAtFin", "VlrContBr", "PorteCli",
        "FatAnual", "IniRelactCli", "DtaProxParcela", "VlrProxParcela", "QtdParcelas", "IPOC", "CodsVenc",
        "Cd", "Valor na inf"
    ]

    # Quais colunas devem ir com formato numérico no Excel
    NUMERIC_FIELDS = {
        "Saldo (Venc)", "Provisão", "TaxEft", "DiasAtraso", "VlrContBr",
        "FatAnual", "VlrProxParcela", "QtdParcelas"
    }

    def _smart_float(v):
        """Converte string numérica respeitando separadores pt/US."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        if s == "":
            return None
        if ',' in s and '.' in s:
            if s.rfind(',') > s.rfind('.'):
                s = s.replace('.', '').replace(',', '.')
            else:
                s = s.replace(',', '')
        elif ',' in s:
            s = s.replace('.', '').replace(',', '.')
        try:
            return float(s)
        except Exception:
            return None

    def _fmt_ptbr_num(v):
        """Formata número em pt-BR (1.234,56). Se não for número, retorna texto original."""
        f = _smart_float(v)
        if f is None:
            return v if (v is not None and str(v).strip() != "") else "Vazio"
        # formata em en-US e converte separadores
        s = f"{f:,.2f}"           # ex: 1,234.56
        return s.replace(",", "_").replace(".", ",").replace("_", ".")

    def _row_filter(full_row: dict, wanted: list[str]):
        # garante que, se pedirem "Cd" ou "Valor na inf ..." e não houver, devolvemos "Vazio"
        row = {k: full_row.get(k, "Vazio") for k in wanted}
        for k in wanted:
            if k in NUMERIC_FIELDS:
                row[k] = _smart_float(row.get(k))
        return row

    def _parse_xml_attrs(xml_path: str):
        rows = []
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as e:
            logger.warning(f"Erro ao abrir {os.path.basename(xml_path)}: {e}")
            return rows

        dtbase = root.attrib.get('DtBase', 'N/A')
        cnpj_arquivo = root.attrib.get('CNPJ', 'N/A')

        mapa_tipo_pessoa = {
            '1': 'pessoa física - CPF',
            '2': 'pessoa jurídica - CNPJ',
            '3': 'pessoa física no exterior',
            '4': 'pessoa jurídica no exterior',
            '5': 'pessoa física sem CPF',
            '6': 'pessoa jurídica sem CNPJ',
        }
        estrategia_tp = {'1': 'CPF','2': 'CNPJ','3': 'CPF','4': 'CNPJ','5': 'CPF','6': 'CNPJ'}

        for cliente in root.findall('Cli'):
            cd_cliente = cliente.attrib.get('Cd', 'N/A')
            tp_pessoa = cliente.attrib.get('Tp', 'N/A')
            portecli = cliente.attrib.get('PorteCli', 'Vazio')
            fatanual = cliente.attrib.get('FatAnual', 'Vazio')
            inirelactcli = cliente.attrib.get('IniRelactCli', 'N/A')

            tipo_id = estrategia_tp.get(tp_pessoa)
            descricao_tp = mapa_tipo_pessoa.get(tp_pessoa, 'Desconhecido')

            for operacao in cliente.findall('Op'):
                if tipo_id == 'CPF':
                    dtcli = cd_cliente
                elif tipo_id == 'CNPJ':
                    dtcli = operacao.attrib.get('DetCli', 'N/A')
                else:
                    dtcli = 'N/A'

                mod = operacao.attrib.get('Mod', 'Vazio') or 'Vazio'
                contrt = operacao.attrib.get('Contrt', 'N/A')
                dtcontr = operacao.attrib.get('DtContr', 'N/A')
                dtvencop = operacao.attrib.get('DtVencOp', 'N/A')
                orig_rec = operacao.attrib.get('OrigemRec', 'Vazio')
                carac_especial = operacao.attrib.get('CaracEspecial', 'Vazio')
                taxa_efetiva = operacao.attrib.get('TaxEft', 'Vazio')
                dias_atraso = operacao.attrib.get('DiaAtraso', 'Vazio')
                natuop = operacao.attrib.get('NatuOp', 'Vazio')
                vlrproxparcela = operacao.attrib.get('VlrProxParcela', 'N/A')
                dtaproxparcela = operacao.attrib.get('DtaProxParcela', 'N/A')
                qtdparcelas = operacao.attrib.get('QtdParcelas', 'N/A')
                ipoc = operacao.attrib.get('IPOC', 'N/A')

                cont_inst_fin = operacao.find('ContInstFinRes4966')
                cart_prov_min = cont_inst_fin.attrib.get('CartProvMin', 'Vazio') if cont_inst_fin is not None else 'Vazio'
                est_inst_fin = cont_inst_fin.attrib.get('EstInstFin', 'Vazio') if cont_inst_fin is not None else 'Vazio'
                tje = cont_inst_fin.attrib.get('TJE', 'Vazio') if cont_inst_fin is not None else 'Vazio'
                clasatfin = cont_inst_fin.attrib.get('ClasAtFin', 'Vazio') if cont_inst_fin is not None else 'Vazio'
                vlrcontbr = cont_inst_fin.attrib.get('VlrContBr', 'Vazio') if cont_inst_fin is not None else 'Vazio'

                try:
                    provisao_valor = float(operacao.attrib.get('ProvConsttd', 0.0))
                except ValueError:
                    provisao_valor = 0.0

                # Somatório de Venc
                saldo = 0.0
                codigos_venc = set()
                for venc in operacao.findall('Venc'):
                    for nome, valor in venc.attrib.items():
                        codigos_venc.add(nome)
                        try:
                            saldo += float(valor)
                        except ValueError:
                            continue
                codigos_venc_ordenado = ';'.join(sorted(codigos_venc)) if codigos_venc else 'Vazio'

                
                infs = operacao.findall('Inf')
                inf_tps = sorted({inf.attrib.get('Tp') for inf in infs if inf.attrib.get('Tp')})
                idents = sorted({inf.attrib.get('Ident') for inf in infs if inf.attrib.get('Ident')})

                cds, vals_fmt = [], []
                for inf in infs:
                    cd_inf = inf.attrib.get('Cd')
                    val_inf = inf.attrib.get('Valor')
                    if cd_inf is not None:
                        cds.append(cd_inf)
                    if val_inf is not None:
                        vals_fmt.append(_fmt_ptbr_num(val_inf))

                gars = operacao.findall('Gar')
                gar_tps = sorted({gar.attrib.get('Tp') for gar in gars if gar.attrib.get('Tp')})

                full = {
                    "Nome do arquivo XML": os.path.basename(xml_path),
                    "DATA_BASE": dtbase,
                    "CNPJ": cnpj_arquivo,
                    "TipoPessoaDesc": descricao_tp,
                    "CPF/CNPJ": dtcli,
                    "Mod": mod,
                    "Contrt": contrt,
                    "DtContr": dtcontr,
                    "DtVencOp": dtvencop,
                    "OrigemRec": orig_rec,
                    "Saldo (Venc)": saldo,                 # float
                    "Provisão": provisao_valor,            # float
                    "NatuOp": natuop,
                    "Inf Tp": ';'.join(inf_tps) if inf_tps else 'Vazio',
                    "Ident": ';'.join(idents) if idents else 'Vazio',
                    "Gar Tp": ';'.join(gar_tps) if gar_tps else 'Vazio',
                    "CaracEspecial": carac_especial or 'Vazio',
                    "CartProvMin": cart_prov_min,
                    "EstInstFin": est_inst_fin,
                    "TJE": tje,
                    "TaxEft": taxa_efetiva,
                    "DiasAtraso": dias_atraso,
                    "ClasAtFin": clasatfin,
                    "VlrContBr": vlrcontbr,
                    "PorteCli": portecli,
                    "FatAnual": fatanual,
                    "IniRelactCli": inirelactcli,
                    "DtaProxParcela": dtaproxparcela,
                    "VlrProxParcela": vlrproxparcela,
                    "QtdParcelas": qtdparcelas,
                    "IPOC": ipoc,
                    "CodsVenc": codigos_venc_ordenado,
                    "Cd": ';'.join(cds) if cds else "Vazio",
                    "Valor na inf": ';'.join(vals_fmt) if vals_fmt else "Vazio",
                }
                rows.append(_row_filter(full, selected_fields))

        # Agreg
        for agreg in root.findall('Agreg'):
            saldo_agreg = 0.0
            for venc in agreg.findall('Venc'):
                for valor in venc.attrib.values():
                    try:
                        saldo_agreg += float(valor)
                    except ValueError:
                        continue
            try:
                pdd_agreg = float(agreg.attrib.get('ProvConsttd', 0.0))
            except ValueError:
                pdd_agreg = 0.0

            full_ag = {
                "Nome do arquivo XML": os.path.basename(xml_path),
                "DATA_BASE": dtbase,
                "CNPJ": cnpj_arquivo,
                "TipoPessoaDesc": "AGREGADO",
                "CPF/CNPJ": "AGREGADO",
                "Mod": agreg.attrib.get("Mod", "N/A"),
                "Contrt": "N/A",
                "DtContr": "N/A",
                "DtVencOp": "N/A",
                "OrigemRec": "N/A",
                "Saldo (Venc)": saldo_agreg,
                "Provisão": pdd_agreg,
                "NatuOp": "N/A",
                "Inf Tp": "N/A",
                "Ident": "N/A",
                "Gar Tp": "N/A",
                "CaracEspecial": "N/A",
                "CartProvMin": "N/A",
                "EstInstFin": "N/A",
                "TJE": "N/A",
                "TaxEft": "N/A",
                "DiasAtraso": "N/A",
                "ClasAtFin": "N/A",
                "VlrContBr": "N/A",
                "PorteCli": "N/A",
                "FatAnual": "N/A",
                "IniRelactCli": "N/A",
                "DtaProxParcela": "N/A",
                "VlrProxParcela": "N/A",
                "QtdParcelas": "N/A",
                "IPOC": "N/A",
                "CodsVenc": "N/A",
                # Agregado não tem par Cd/Valor de Inf
                "Cd": "N/A",
                "Valor na inf": "N/A",
            }
            rows.append(_row_filter(full_ag, selected_fields))

        return rows


    pasta_xmls = Path(pasta_xmls)
    pasta_saida = Path(pasta_saida)

    if not selected_fields:
        logger.info("Nenhum atributo selecionado. Operação cancelada.")
        return

    xml_files = [f for f in os.listdir(pasta_xmls) if f.lower().endswith('.xml')]
    if not xml_files:
        logger.info("Nenhum XML encontrado na pasta informada.")
        return


    for extra in ["Cd", "Valor na inf"]:
        if extra in selected_fields and extra not in ALL_FIELDS:
            ALL_FIELDS.append(extra)

    logger.info(f"Lendo atributos de {len(xml_files)} XML(s)…")
    all_rows = []
    for i, xml_name in enumerate(sorted(xml_files), start=1):
        if cancel_flag.is_set():
            logger.info("Leitura cancelada pelo usuário.")
            return
        caminho_xml = str(pasta_xmls / xml_name)
        logger.info(f"[{i}/{len(xml_files)}] {xml_name}")
        all_rows.extend(_parse_xml_attrs(caminho_xml))

    if cancel_flag.is_set():
        logger.info("Cancelado antes da exportação do Excel.")
        return
    if not all_rows:
        logger.info("Nenhum dado coletado para exportar.")
        return

    df = pd.DataFrame(all_rows, columns=selected_fields)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out = pasta_saida / f"atributos_tags_{ts}.xlsx"

    with pd.ExcelWriter(out, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name="Tags_espec", index=False)
        wb = writer.book
        ws = writer.sheets["Tags_espec"]
        # Larguras + formatos numéricos
        for col_idx, col_name in enumerate(df.columns):
            if col_name in NUMERIC_FIELDS:
                numfmt = wb.add_format({'num_format': '#,##0.00'})
                ws.set_column(col_idx, col_idx, 18, numfmt)
            else:
                ws.set_column(col_idx, col_idx, 22 if col_name in ("Cd", "Valor na inf") else 18)
        if not df.empty and len(df.columns) > 0:
            ws.autofilter(0, 0, len(df), len(df.columns)-1)

    logger.info(f"Relatório gerado: {out}")



# Arrumeitor


def _load_func(module_name: str, func_name: str):
    """Carrega função de módulo extraído mantendo compatibilidade."""
    module = __import__(module_name, fromlist=[func_name])
    return getattr(module, func_name)


def ajustes_arrumeitor(cancel_flag: threading.Event, pasta_xmls: str, pasta_saida: str, regras_condicionais: list[dict] | None = None, progress=None):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_ajustes_arrumeitor", "ajustes_arrumeitor")(
        cancel_flag,
        pasta_xmls,
        pasta_saida,
        regras_condicionais,
        RULES_ARRUMEITOR,
        _detect_xml_encoding,
        logger,
        progress,
    )





def ajustes_ajusta_inicio_relacionamento(cancel_flag: threading.Event, pasta_xmls: str, pasta_saida: str, progress=None):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_ajustes_relacionamento", "ajustes_ajusta_inicio_relacionamento")(
        cancel_flag,
        pasta_xmls,
        pasta_saida,
        _detect_xml_encoding,
        logger,
        progress,
    )

def ajustes_filtrar_operacoes_por_contratos(
    cancel_flag: threading.Event,
    caminho_xml: str,
    caminho_excel: str,
    pasta_saida: str,
    inf_tp: str,  # "0301" | "0302" | "0399"
    progress=None
):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_ajustes_filtro_saida", "ajustes_filtrar_operacoes_por_contratos")(
        cancel_flag,
        caminho_xml,
        caminho_excel,
        pasta_saida,
        inf_tp,
        _detect_xml_encoding,
        logger,
        progress,
    )



def ajustes_incluir_carac_especial(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    caminho_excel: str,
    valor_alvo: str,
    progress=None
):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_ajustes_carac_especial", "ajustes_incluir_carac_especial")(
        cancel_flag,
        pasta_xmls,
        caminho_excel,
        valor_alvo,
        logger,
        progress,
    )



def ajustes_renomear_xmls_por_fidc(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    fidc_reg: FIDCRegistry,
    prefixo: str = "NC",
    progress=None,
):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_ajustes_renomear_nc", "ajustes_renomear_xmls_por_fidc")(
        cancel_flag,
        pasta_xmls,
        fidc_reg,
        prefixo,
        logger,
        progress,
    )




def bacen_valida_xmls(cancel_flag: threading.Event, pasta_xmls: str, caminho_validador: str, pasta_saida: str, progress=None):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_bacen_sta", "bacen_valida_xmls")(
        cancel_flag, pasta_xmls, caminho_validador, pasta_saida, logger, progress
    )



def bacen_envia_sta(
    cancel_flag: threading.Event,
    pasta_arquivos: str,
    credenciais_sta: dict | None,   
    caminho_chromedriver: str,
    pasta_saida: str | None = None,
    login_timeout_secs: int = 120,
    tamanho_lote: int = 2,
    max_mb_por_lote: int | None = 150,
    cod_tipo_arquivo: str = "94",  # 3040
    refresh_every: int = 0,         # refresh a cada N envios (0 desativa)
    progress=None,
):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_bacen_sta", "bacen_envia_sta")(
        cancel_flag,
        pasta_arquivos,
        credenciais_sta,
        caminho_chromedriver,
        pasta_saida,
        login_timeout_secs,
        tamanho_lote,
        max_mb_por_lote,
        cod_tipo_arquivo,
        refresh_every,
        logger,
        progress,
    )




def bacen_retorna_protocolos_sta(cancel_flag: threading.Event,
                                 caminho_excel_protocolos: str,
                                 pasta_saida: str,
                                 credenciais_sta: dict | None,
                                 caminho_chromedriver: str, progress=None):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_bacen_sta", "bacen_retorna_protocolos_sta")(
        cancel_flag,
        caminho_excel_protocolos,
        pasta_saida,
        credenciais_sta,
        caminho_chromedriver,
        logger,
        progress,
    )





# Validação de composição do IPOC
def ajustes_valida_composicao_ipoc(
    cancel_flag: threading.Event,
    pasta_xmls: str,
    pasta_saida: str,
    progress=None,
):
    """Wrapper mantido para compatibilidade durante refatoração modular."""
    return _load_func("fechamento_ajustes_ipoc", "ajustes_valida_composicao_ipoc")(
        cancel_flag, pasta_xmls, pasta_saida, logger, progress
    )


# App tk (gui com ajuda do gepeto)
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        _tune_windows_dpi_and_fonts(self)
        self.title(APP_NAME)
        self.minsize(900, 600)
        self.cfg = AppConfig(CONFIG_FILE)
        self.fidc_reg = FIDCRegistry(FIDC_DB_PATH)

        # restaura geometria salva (ou usa padrão)
        default_geo = _default_geometry(self)
        try:
            geo = self.cfg.get("ui", "geometry", default="")
            geo = _clamp_geometry_to_screen(self, geo, fallback=default_geo)
            self.geometry(geo or default_geo)
        except Exception:
            self.geometry(default_geo)
        try:
            self.after(120, lambda: _maybe_maximize_if_small(self))
        except Exception:
            pass

        # flags/handles para fechamento limpo
        self._closing = False
        self._drain_after_id = None  # id do after do logger

        # handler de fechar: salva geometria e fecha
        self.protocol("WM_DELETE_WINDOW", self._remember_geometry_on_close)

        # constrói UI
        self._build_menu()
        self._build_layout()
        self._apply_theme()
        self._wire_logging()
        self.task_runner = TaskRunner(
            self, self._on_task_start, self._on_task_finish, self._on_task_error
        )

        # atalhos globais
        try:
            self.bind_all("<Control-l>", lambda e: self._clear_log())
            self.bind_all("<Alt-1>", lambda e: self.nb.select(self.tab_conc))
            self.bind_all("<Alt-2>", lambda e: self.nb.select(self.tab_ajus))
            self.bind_all("<Alt-3>", lambda e: self.nb.select(self.tab_bacen))
            self.bind("<Escape>", lambda e: self._cmd_cancel())
        except Exception:
            pass

        logger.info(f"{APP_NAME} iniciado.")


    def _remember_geometry_on_close(self):

        if getattr(self, "_closing", False):
            return
        self._closing = True

        # Cancela o after do logger (se ativo)
        try:
            if self._drain_after_id is not None:
                self.after_cancel(self._drain_after_id)
                self._drain_after_id = None
        except Exception:
            pass

        # Cancela tarefa em execução (se houver)
        try:
            if getattr(self, "task_runner", None) and self.task_runner.is_running():
                self.task_runner.cancel()
        except Exception:
            pass

        # Salva geometria
        try:
            self.cfg.set(self.geometry(), "ui", "geometry")
            self.cfg.save()
        except Exception:
            pass

        # Fecha a janela (NÃO reconstrua UI aqui)
        try:
            self.destroy()
        except Exception:
            pass


    def _apply_theme(self):
        self.style = apply_theme(self)
        try:
            self.progress.configure(style="Accent.Horizontal.TProgressbar")
        except Exception:
            pass
        try:
            skin_text_widget(self.txt_log)
        except Exception:
            pass

    def _mark_primary(self, *btns: ttk.Button):
        promote_primary(*btns)


    def _build_menu(self):
        menubar = tk.Menu(self)

        m_arquivo = tk.Menu(menubar, tearoff=0)
        # abre no Explorer (Windows) para evitar bloqueios do webbrowser em UNC
        m_arquivo.add_command(label="Abrir pasta de logs", command=lambda: os.startfile(str(LOG_DIR)))
        m_arquivo.add_separator()
        m_arquivo.add_command(label="Sair", command=self.destroy)

        m_config = tk.Menu(menubar, tearoff=0)
        m_config.add_command(label="Configurações…", command=self._open_config_dialog)
        m_config.add_command(label="Testar ambiente", command=self._cmd_testar_ambiente)

        menubar.add_cascade(label="Arquivo", menu=m_arquivo)
        menubar.add_cascade(label="Config", menu=m_config)
        self.config_menu = m_config

        self.configure(menu=menubar)



    def _fidc_update_buttons_state(self, total: int | None = None):
        """Habilita/Desabilita botões da aba FIDCs conforme há linhas e/ou seleção."""
        import tkinter as tk
    
        try:
            tree = getattr(self, "fidc_tree", None)
            if not tree or not tree.winfo_exists():
                return
    
            # total de registros na grid (se não veio por parâmetro, conta no Treeview)
            if total is None:
                total = len(tree.get_children())
    
            # quantos itens estão selecionados
            sel_count = len(tree.selection())
    
            # estados
            state_any = tk.NORMAL if total > 0 else tk.DISABLED   # depende de existir ao menos 1 registro
            state_sel = tk.NORMAL if sel_count > 0 else tk.DISABLED  # depende de seleção
    
            # botões que dependem de haver qualquer registro
            for btn in (getattr(self, "btn_fidc_export", None),
                        getattr(self, "btn_fidc_clear", None)):
                if btn:
                    btn.config(state=state_any)
    
            # botões que dependem de haver seleção
            for btn in (getattr(self, "btn_fidc_edit", None),
                        getattr(self, "btn_fidc_delete", None)):
                if btn:
                    btn.config(state=state_sel)
    
        except Exception:
            pass

    def _build_layout(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tab_conc = ttk.Frame(self.nb)
        self.tab_ajus = ttk.Frame(self.nb)
        self.tab_bacen = ttk.Frame(self.nb)
        self.tab_fidcs = ttk.Frame(self.nb)
        self.nb.add(self.tab_conc, text="Conciliação")
        self.nb.add(self.tab_ajus, text="Ajustes de Fechamento")
        self.nb.add(self.tab_bacen, text="BACEN")
        self.nb.add(self.tab_fidcs, text="Cadastro de FIDCs")

        self._build_tab_conc(self.tab_conc)
        self._build_tab_ajus(self.tab_ajus)
        self._build_tab_bacen(self.tab_bacen)
        self._build_tab_fidcs(self.tab_fidcs)

        # LOG (expansível) + toolbar
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill=tk.X, padx=6, pady=(6, 0))
        ttk.Button(log_toolbar, text="Limpar log", command=self._clear_log).pack(side=tk.RIGHT)

        self.txt_log = tk.Text(log_frame, height=8, wrap=tk.WORD)
        yscroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=yscroll.set)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # status (sem progressbar)
        status = ttk.Frame(self)
        status.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.status_label = ttk.Label(status, text="Pronto")
        self.status_label.pack(side=tk.LEFT)

        # novo: abrir último arquivo gerado
        self.btn_open_last = ttk.Button(
            status, text="Abrir último arquivo", command=self._open_last_output, state=tk.DISABLED
        )
        self.btn_open_last.pack(side=tk.RIGHT, padx=(6, 0))

        self.btn_cancel = ttk.Button(status, text="Cancelar", command=self._cmd_cancel, state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.RIGHT, padx=(6, 0))

        # onde vamos guardar o caminho detectado
        self._last_output_path = None


    def _on_task_progress(self, pct: int):
        try:
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=100)
            pct = max(0, min(100, int(pct)))
            self.progress["value"] = pct
            self.percent_label.config(text=f"{pct}%")
        except Exception:
            pass

    def _wire_logging(self):
        self.log_queue = queue.Queue()
        ui_fmt = logging.Formatter("%(asctime)s — %(message)s", datefmt="%H:%M:%S")
        self.ui_handler = TkQueueLogHandler(self.log_queue)
        self.ui_handler.setFormatter(ui_fmt)
        logger.addHandler(self.ui_handler)
        # armazena id do after para cancelar no fechamento
        self._drain_after_id = self.after(80, self._drain_log_queue)

    def _drain_log_queue(self):
        if self._closing:
            return  # não drenar nada se estamos fechando
        try:
            updated = False
            while True:
                msg = self.log_queue.get_nowait()
                if not self.winfo_exists():
                    return

                self.txt_log.configure(state=tk.NORMAL)
                self.txt_log.insert(tk.END, msg + "\n")
                self.txt_log.see(tk.END)
                self.txt_log.configure(state=tk.DISABLED)

                m = re.search(r"(Relatório gerado:|Resultados salvos em:|Arquivo gerado:\s*)(.+)$", msg, re.IGNORECASE)
                if m:
                    path = m.group(2).strip()
                    if path:
                        self._last_output_path = path
                        updated = True
        except queue.Empty:
            pass
        except Exception:
            return

        try:
            if self.winfo_exists():
                try:
                    self.btn_open_last.config(state=(tk.NORMAL if self._last_output_path else tk.DISABLED))
                except Exception:
                    pass
                # reagenda e guarda o id
                self._drain_after_id = self.after(80, self._drain_log_queue)
        except tk.TclError:
            return

    def _clear_log(self, also_file: bool = False):
        try:
            self.txt_log.configure(state=tk.NORMAL)
            self.txt_log.delete("1.0", tk.END)
            self.txt_log.configure(state=tk.DISABLED)
        except tk.TclError:
            pass
        if also_file:
            try:
                (LOG_DIR / "app.log").write_text("", encoding="utf-8")
                logger.info("Arquivo de log limpo.")
            except Exception as e:
                logger.error(f"Falha ao limpar arquivo de log: {e}")

    def _open_last_output(self):
        path = self._last_output_path
        if not path:
            return
        try:
            if os.path.isdir(path):
                os.startfile(path)
            elif os.path.isfile(path):
                os.startfile(path)
            else:
                folder = os.path.dirname(path)
                if folder and os.path.isdir(folder):
                    os.startfile(folder)
        except Exception as e:
            messagebox.showwarning(APP_NAME, f"Não foi possível abrir:\n{path}\n\n{e}")


    def _build_tab_conc(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        b1 = ttk.Button(f, text="Conciliação FIDCs", command=self._cmd_concilia_saldo_pdd)
        b2 = ttk.Button(f, text="Saldo e PDD dos XMLs", command=self._cmd_ler_saldo_pdd)
        b3 = ttk.Button(f, text="Relatório de tags específicas", command=self._cmd_le_tags_atributos)

        for i, w in enumerate((b1, b2, b3)):
            w.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
        self._mark_primary(b1)  # CTA

        try:
            Tooltip(b1, "Compara saldos e PDD: planilha MC x XMLs 3040 e gera Excel de conciliação.")
            Tooltip(b2, "Lê saldos e PDD dos XMLs e exporta um Excel com resumo e detalhes por ClassOp.")
            Tooltip(b3, "Extrai atributos específicos de <Cli>/<Op> (colunas que você escolher) para Excel.")
        except Exception:
            pass

        f.grid_columnconfigure(0, weight=1)
        
    def _build_tab_fidcs(self, parent):
        import tkinter as tk
        from tkinter import ttk
    
        # CONTÊINER DA ABA
        f = ttk.Frame(parent)
        f.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
    
        # Toolbar (linha dos botões) 
        toolbar = ttk.Frame(f)
        toolbar.pack(fill=tk.X, pady=(0, 8))
    
        btn_import = ttk.Button(toolbar, text="Importar planilha…", command=self._cmd_fidcs_import)
        btn_import.pack(side=tk.LEFT)
    
        btn_export = ttk.Button(toolbar, text="Exportar planilha…", command=self._cmd_fidcs_export)
        btn_export.pack(side=tk.LEFT, padx=(6, 0))
    
        ttk.Separator(toolbar, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=8)
    
        btn_add = ttk.Button(toolbar, text="Adicionar", command=self._cmd_fidc_add)
        btn_add.pack(side=tk.LEFT)
    
        btn_edit = ttk.Button(toolbar, text="Editar", command=self._cmd_fidc_edit)
        btn_edit.pack(side=tk.LEFT, padx=(6, 0))
    
        btn_del = ttk.Button(toolbar, text="Remover", command=self._cmd_fidc_delete)
        btn_del.pack(side=tk.LEFT, padx=(6, 0))
    
        # Botão EXCLUIR TODOS (lado direito)
        btn_clear = ttk.Button(toolbar, text="Excluir todos…", command=self._cmd_fidcs_excluir_todos)
        btn_clear.pack(side=tk.RIGHT)
    
        try:
            Tooltip(btn_clear, "Apaga todos os registros da base.\nUm backup .json é criado automaticamente.")
        except Exception:
            pass
    
        #  Tree + Scrollbar 
        # Usar sempre o MESMO frame 'f' como pai, para manter layout
        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill=tk.BOTH, expand=True)
    
        cols = ("id","carteira","cnpj","raiz_cnpj","fundos","legado","tp","metod","data_esp")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=12)
        self.fidc_tree = tree  # guarda na instância
    
        headers = {
            "id":"ID", "carteira":"Carteira", "cnpj":"CNPJ", "raiz_cnpj":"RAIZ CNPJ",
            "fundos":"FUNDOS", "legado":"LEGADO", "tp":"TP", "metod":"METOD", "data_esp":"Data esperada pelo Bacen"
        }
        widths = {"id":70,"carteira":110,"cnpj":170,"raiz_cnpj":110,"fundos":200,"legado":90,"tp":60,"metod":70,"data_esp":160}
    
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=widths[c], anchor="w")
    
        yscroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
    
        # Packing da tabela e do scroll
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
    
        try:
            skin_treeview(tree)
        except Exception:
            pass
    
        # Carrega dados ao entrar
        self._fidc_refresh_tree()




    def _cmd_fidcs_excluir_todos(self):
        from tkinter import messagebox
        from datetime import datetime
        import json
        import inspect
    
        if not messagebox.askyesno(
            APP_NAME,
            "Tem certeza que deseja EXCLUIR TODOS os FIDCs?\n"
            "Um backup .json será criado automaticamente.",
            icon="warning"
        ):
            return
    
        rows_atual = []
        usou_registry = False
        try:
            if hasattr(self, "fidc_reg") and hasattr(self.fidc_reg, "all"):
                rows_atual = list(self.fidc_reg.all())
                usou_registry = True
        except Exception:
            pass
    
        if not rows_atual:
            try:
                rows_atual = _fidcs_db_load()  # fallback JSON
            except Exception:
                rows_atual = []
    
        backup_dir = Path(BASE_DIR) / "backups_fidcs"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"fidcs_backup_{ts}.json"
    
        try:
            backup_file.write_text(json.dumps(rows_atual, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[FIDCs] Backup criado: {backup_file}")
        except Exception as e:
            logger.error(f"[FIDCs] Falha ao criar backup: {e}")
    
        def _call_save_safely(reg, payload=None):
            """Chama reg.save com 0 ou 1 parâmetro conforme a assinatura; se não existir, ignora."""
            try:
                if not hasattr(reg, "save"):
                    return
                sig = inspect.signature(reg.save)

                params = [p for p in sig.parameters.values()]
                
                if len(params) == 0:
                   
                    reg.save()
                else:
                    
                    if payload is not None:
                        reg.save(payload)
                    else:
                        
                        reg.save()
            except TypeError:
                # assinatura não bateu: tente a outra forma
                try:
                    if payload is not None:
                        reg.save(payload)
                    else:
                        reg.save()
                except Exception:
                    pass
            except Exception:
                pass
    
        def _clear_registry(reg):
            """Tenta várias formas de limpar o registry."""
            # 1) clear_all()
            if hasattr(reg, "clear_all") and callable(getattr(reg, "clear_all")):
                reg.clear_all()
                return True
            # 2) clear()
            if hasattr(reg, "clear") and callable(getattr(reg, "clear")):
                reg.clear()
                return True
            # 3) set_all([])
            if hasattr(reg, "set_all") and callable(getattr(reg, "set_all")):
                reg.set_all([])
                return True
            # 4) replace / replace_all
            if hasattr(reg, "replace_all") and callable(getattr(reg, "replace_all")):
                reg.replace_all([])
                return True
            if hasattr(reg, "replace") and callable(getattr(reg, "replace")):
                reg.replace([])
                return True
            
            for attr in ("items", "rows", "data", "_items", "_rows", "_data"):
                if hasattr(reg, attr):
                    try:
                        setattr(reg, attr, [])
                        return True
                    except Exception:
                        pass
            # 6) método add + reset manual 
            return False
    
      
        try:
            if usou_registry:
                ok = _clear_registry(self.fidc_reg)
                if ok:
                    # Após limpar, tente salvar da forma compatível
                    # Se a API exigir payload, passe lista vazia; se não, chame sem args.
                    _call_save_safely(self.fidc_reg, payload=[])
                else:
                    # Sem API clara para limpar: zere via JSON fallback.
                    _fidcs_db_save([])
            else:
                _fidcs_db_save([])
    
            logger.info("[FIDCs] Todos os registros foram excluídos.")
            self._fidc_refresh_tree()
            messagebox.showinfo(APP_NAME, f"Registros excluídos com sucesso.\nBackup salvo em:\n{backup_file}")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Falha ao excluir registros: {e}")
    
    



    def _fidc_open_edit_dialog(self, mode: str, fid: int | None = None):
        """Dialog de adicionar/editar. LEGADO restrito a FROMTIS/DRIVE/HÍBRIDO."""
        win = tk.Toplevel(self)
        win.title("Cadastrar FIDC" if mode=="add" else f"Editar FIDC {fid}")
        win.transient(self); win.grab_set()
        frm = ttk.Frame(win, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        labels = ["ID","Carteira","CNPJ","RAIZ CNPJ","FUNDOS","LEGADO","TP","METOD","Data esperada pelo Bacen"]
        vars_ = {k: tk.StringVar() for k in labels}

        grid = ttk.Frame(frm); grid.pack(fill=tk.BOTH, expand=True)
        for i, k in enumerate(labels):
            ttk.Label(grid, text=k+":").grid(row=i, column=0, sticky="w", pady=3)
            if k == "LEGADO":
                cb = ttk.Combobox(grid, textvariable=vars_[k], values=sorted(ALLOWED_LEGADOS), state="readonly")
                cb.grid(row=i, column=1, sticky="ew", pady=3)
            else:
                ent = ttk.Entry(grid, textvariable=vars_[k])
                ent.grid(row=i, column=1, sticky="ew", pady=3)
        grid.grid_columnconfigure(1, weight=1)

        # pré-preenche no modo editar
        if mode == "edit" and fid is not None:
            it = self.fidc_reg.get_by_id(fid)
            if not it:
                messagebox.showerror(APP_NAME, "Registro não encontrado.")
                win.destroy(); return
            vars_["ID"].set(str(it.get("id","")))
            vars_["Carteira"].set(it.get("carteira",""))
            vars_["CNPJ"].set(it.get("cnpj",""))
            vars_["RAIZ CNPJ"].set(it.get("raiz_cnpj",""))
            vars_["FUNDOS"].set(it.get("fundos",""))
            vars_["LEGADO"].set(it.get("legado",""))
            vars_["TP"].set(it.get("tp",""))
            vars_["METOD"].set(it.get("metod",""))
            # mostra data no formato dd/mm/aaaa para o usuário
            d = it.get("data_esp","")
            try:
                d_ui = datetime.fromisoformat(d).strftime("%d/%m/%Y") if d else ""
            except Exception:
                d_ui = d or ""
            vars_["Data esperada pelo Bacen"].set(d_ui)

        # botões
        row_btn = ttk.Frame(frm); row_btn.pack(fill=tk.X, pady=(8,0))
        def salvar():
            try:
                fid_val = int(vars_["ID"].get().strip())
            except Exception:
                messagebox.showwarning(APP_NAME, "ID inválido (precisa ser numérico).")
                return

            cnpj_fmt = normalize_cnpj(vars_["CNPJ"].get())
            raiz = (vars_["RAIZ CNPJ"].get() or "").strip() or raiz_cnpj(cnpj_fmt)
            legado = vars_["LEGADO"].get().strip().upper()
            if legado not in ALLOWED_LEGADOS:
                messagebox.showwarning(APP_NAME, f"LEGADO inválido. Use um de: {', '.join(sorted(ALLOWED_LEGADOS))}.")
                return

            data_iso = parse_data_br(vars_["Data esperada pelo Bacen"].get())

            item = {
                "id": fid_val,
                "carteira": (vars_["Carteira"].get() or "").strip(),
                "cnpj": cnpj_fmt,
                "raiz_cnpj": raiz,
                "fundos": (vars_["FUNDOS"].get() or "").strip(),
                "legado": legado,
                "tp": (vars_["TP"].get() or "").strip(),
                "metod": (vars_["METOD"].get() or "").strip(),
                "data_esp": data_iso,
            }
            self.fidc_reg.upsert(item)
            self._fidc_refresh_tree()
            win.destroy()

        ttk.Button(row_btn, text="Salvar", command=salvar).pack(side=tk.RIGHT)
        ttk.Button(row_btn, text="Cancelar", command=win.destroy).pack(side=tk.RIGHT, padx=(0,6))

    def _build_tab_fidcs(self, parent):
        import tkinter as tk
        from tkinter import ttk
    
        f = ttk.Frame(parent)
        f.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
    
        # Toolbar
        toolbar = ttk.Frame(f)
        toolbar.pack(fill=tk.X, pady=(0, 8))
    
        ttk.Button(toolbar, text="Importar planilha…", command=self._cmd_fidcs_import).pack(side=tk.LEFT)
    
        self.btn_fidc_export = ttk.Button(toolbar, text="Exportar planilha…", command=self._cmd_fidcs_export)
        self.btn_fidc_export.pack(side=tk.LEFT, padx=(6, 0))
    
        ttk.Separator(toolbar, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=8)
    
        self.btn_fidc_add = ttk.Button(toolbar, text="Adicionar", command=self._cmd_fidc_add)
        self.btn_fidc_add.pack(side=tk.LEFT)
    
        self.btn_fidc_edit = ttk.Button(toolbar, text="Editar", command=self._cmd_fidc_edit)
        self.btn_fidc_edit.pack(side=tk.LEFT, padx=(6, 0))
    
        self.btn_fidc_delete = ttk.Button(toolbar, text="Remover", command=self._cmd_fidc_delete)
        self.btn_fidc_delete.pack(side=tk.LEFT, padx=(6, 0))
    
        self.btn_fidc_clear = ttk.Button(toolbar, text="Excluir todos…", command=self._cmd_fidcs_excluir_todos)
        self.btn_fidc_clear.pack(side=tk.RIGHT)
    
        try:
            Tooltip(self.btn_fidc_clear, "Apaga todos os registros da base.\nUm backup .json é criado automaticamente.")
        except Exception:
            pass
    
        # Tree + Scroll
        tree_frame = ttk.Frame(f)
        tree_frame.pack(fill=tk.BOTH, expand=True)
    
        cols = ("id","carteira","cnpj","raiz_cnpj","fundos","legado","tp","metod","data_esp")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=12)
        self.fidc_tree = tree
    
        headers = {
            "id":"ID", "carteira":"Carteira", "cnpj":"CNPJ", "raiz_cnpj":"RAIZ CNPJ",
            "fundos":"FUNDOS", "legado":"LEGADO", "tp":"TP", "metod":"METOD", "data_esp":"Data esperada pelo Bacen"
        }
        widths = {"id":70,"carteira":110,"cnpj":170,"raiz_cnpj":110,"fundos":200,"legado":90,"tp":60,"metod":70,"data_esp":160}
        for c in cols:
            tree.heading(c, text=headers[c])
            tree.column(c, width=widths[c], anchor="w")
    
        yscroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
    
        try:
            skin_treeview(tree)
        except Exception:
            pass
    
        # Atualiza botões quando a seleção muda
        tree.bind("<<TreeviewSelect>>", lambda e: self._fidc_update_buttons_state())
    
        # Carrega dados e ajusta estado inicial
        self._fidc_refresh_tree()
        self._fidc_update_buttons_state()
    

    def _fidc_refresh_tree(self):
        import tkinter as tk
    
        tree = getattr(self, "fidc_tree", None)
        if not tree or not tree.winfo_exists():
            return
    
        # Limpa linhas
        for i in tree.get_children():
            tree.delete(i)
    
        # Fonte dos dados: registry se existir; senão JSON
        try:
            if hasattr(self, "fidc_reg") and hasattr(self.fidc_reg, "all"):
                rows = list(self.fidc_reg.all())
            else:
                rows = _fidcs_db_load()
        except Exception:
            rows = []
    
        # Insere linhas
        for it in rows:
            vals = (
                it.get("id",""),
                it.get("carteira",""),
                it.get("cnpj",""),
                it.get("raiz_cnpj",""),
                it.get("fundos",""),
                it.get("legado",""),
                it.get("tp",""),
                it.get("metod",""),
                it.get("data_esp",""),
            )
            tree.insert("", tk.END, values=vals)
    
        # Ajusta estado dos botões com base no total
        self._fidc_update_buttons_state(len(rows))
   

    

    def _fidc_get_selection_id(self) -> int | None:
            tree = getattr(self, "fidc_tree", None)
            if not tree:
                return None
            sel = tree.selection()
            if not sel:
                return None
            vals = tree.item(sel[0], "values") or ()
            if not vals:
                return None
            try:
                return int(vals[0])
            except Exception:
                return None
    
    def _cmd_fidcs_import(self):
            excel_path = self._ask_file("Selecione a planilha de FIDCs (Excel)", self.cfg.get("pastas","entrada"),
                                        patterns=("Planilhas Excel", "*.xlsx *.xls *.xlsm"))
            if not excel_path:
                return
            try:
                imported, avisos = self.fidc_reg.import_from_excel(excel_path)
                self._fidc_refresh_tree()
                logger.info(f"Importação concluída. Registros importados/atualizados: {imported}.")
                for a in avisos:
                    logger.warning(f"[IMPORT] {a}")
            except Exception as e:
                messagebox.showerror(APP_NAME, f"Falha ao importar: {e}")
    
    def _cmd_fidcs_export(self):
            out = filedialog.asksaveasfilename(
                title="Salvar cadastro em Excel",
                initialdir=self._valid_initialdir(self.cfg.get("pastas","saida")),
                defaultextension=".xlsx",
                filetypes=(("Excel", "*.xlsx"), ("Todos", "*.*")),
            )
            if not out:
                return
            try:
                self.fidc_reg.export_to_excel(out)
                logger.info(f"Exportado: {out}")
            except Exception as e:
                messagebox.showerror(APP_NAME, f"Falha ao exportar: {e}")
    
    def _cmd_fidc_add(self):
            self._fidc_open_edit_dialog(mode="add")
    
    def _cmd_fidc_edit(self):
            fid = self._fidc_get_selection_id()
            if fid is None:
                messagebox.showinfo(APP_NAME, "Selecione um registro para editar.")
                return
            self._fidc_open_edit_dialog(mode="edit", fid=fid)
    
    def _cmd_fidc_delete(self):
            fid = self._fidc_get_selection_id()
            if fid is None:
                messagebox.showinfo(APP_NAME, "Selecione um registro para remover.")
                return
            if messagebox.askyesno(APP_NAME, f"Remover o ID {fid}?"):
                ok = self.fidc_reg.delete(fid)
                if ok:
                    self._fidc_refresh_tree()
                else:
                    messagebox.showwarning(APP_NAME, "Registro não encontrado.")
    
    def _fidc_open_edit_dialog(self, mode: str, fid: int | None = None):
            """Dialog de adicionar/editar. LEGADO restrito a FROMTIS/DRIVE/HÍBRIDO."""
            win = tk.Toplevel(self)
            win.title("Cadastrar FIDC" if mode=="add" else f"Editar FIDC {fid}")
            win.transient(self); win.grab_set()
            frm = ttk.Frame(win, padding=10)
            frm.pack(fill=tk.BOTH, expand=True)
    
            labels = ["ID","Carteira","CNPJ","RAIZ CNPJ","FUNDOS","LEGADO","TP","METOD","Data esperada pelo Bacen"]
            vars_ = {k: tk.StringVar() for k in labels}
    
            grid = ttk.Frame(frm); grid.pack(fill=tk.BOTH, expand=True)
            for i, k in enumerate(labels):
                ttk.Label(grid, text=k+":").grid(row=i, column=0, sticky="w", pady=3)
                if k == "LEGADO":
                    cb = ttk.Combobox(grid, textvariable=vars_[k], values=sorted(ALLOWED_LEGADOS), state="readonly")
                    cb.grid(row=i, column=1, sticky="ew", pady=3)
                else:
                    ent = ttk.Entry(grid, textvariable=vars_[k])
                    ent.grid(row=i, column=1, sticky="ew", pady=3)
            grid.grid_columnconfigure(1, weight=1)
    
            # pré-preenche no modo editar
            if mode == "edit" and fid is not None:
                it = self.fidc_reg.get_by_id(fid)
                if not it:
                    messagebox.showerror(APP_NAME, "Registro não encontrado.")
                    win.destroy(); return
                vars_["ID"].set(str(it.get("id","")))
                vars_["Carteira"].set(it.get("carteira",""))
                vars_["CNPJ"].set(it.get("cnpj",""))
                vars_["RAIZ CNPJ"].set(it.get("raiz_cnpj",""))
                vars_["FUNDOS"].set(it.get("fundos",""))
                vars_["LEGADO"].set(it.get("legado",""))
                vars_["TP"].set(it.get("tp",""))
                vars_["METOD"].set(it.get("metod",""))
                # mostra data no formato dd/mm/aaaa para o usuário
                d = it.get("data_esp","")
                try:
                    d_ui = datetime.fromisoformat(d).strftime("%d/%m/%Y") if d else ""
                except Exception:
                    d_ui = d or ""
                vars_["Data esperada pelo Bacen"].set(d_ui)
    
            # botões
            row_btn = ttk.Frame(frm); row_btn.pack(fill=tk.X, pady=(8,0))
            def salvar():
                try:
                    fid_val = int(vars_["ID"].get().strip())
                except Exception:
                    messagebox.showwarning(APP_NAME, "ID inválido (precisa ser numérico).")
                    return
    
                cnpj_fmt = normalize_cnpj(vars_["CNPJ"].get())
                raiz = (vars_["RAIZ CNPJ"].get() or "").strip() or raiz_cnpj(cnpj_fmt)
                legado = vars_["LEGADO"].get().strip().upper()
                if legado not in ALLOWED_LEGADOS:
                    messagebox.showwarning(APP_NAME, f"LEGADO inválido. Use um de: {', '.join(sorted(ALLOWED_LEGADOS))}.")
                    return
    
                data_iso = parse_data_br(vars_["Data esperada pelo Bacen"].get())
    
                item = {
                    "id": fid_val,
                    "carteira": (vars_["Carteira"].get() or "").strip(),
                    "cnpj": cnpj_fmt,
                    "raiz_cnpj": raiz,
                    "fundos": (vars_["FUNDOS"].get() or "").strip(),
                    "legado": legado,
                    "tp": (vars_["TP"].get() or "").strip(),
                    "metod": (vars_["METOD"].get() or "").strip(),
                    "data_esp": data_iso,
                }
                self.fidc_reg.upsert(item)
                self._fidc_refresh_tree()
                win.destroy()
    
            ttk.Button(row_btn, text="Salvar", command=salvar).pack(side=tk.RIGHT)
            ttk.Button(row_btn, text="Cancelar", command=win.destroy).pack(side=tk.RIGHT, padx=(0,6))
    




    def _build_tab_ajus(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        b1 = ttk.Button(f, text="Arrumeitor", command=self._cmd_arrumeitor)
        b2 = ttk.Button(f, text="Configurar Arrumeitor…", command=self._open_arrumeitor_rules_dialog)
        b3 = ttk.Button(f, text="Início de relacionamento", command=self._cmd_ajusta_inicio_rel)
        b4 = ttk.Button(f, text="Geração de saídas (0301/0302/0308/0399)", command=self._cmd_xml_com_saida_por_contrato)
        b5 = ttk.Button(f, text="Inclusão de característica especial", command=self._cmd_inclusao_carac_especial)
        b6 = ttk.Button(f, text="Renomear XMLs para NC<ID>", command=self._cmd_renomear_xmls_nc)
        b7 = ttk.Button(f, text="Valida composição do IPOC", command=self._cmd_valida_composicao_ipoc)
        

        for i, w in enumerate((b1, b2, b3, b4, b5, b6, b7)):
            w.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
        self._mark_primary(b1)  # CTA do Arrumeitor

        try:
            Tooltip(b1, "Aplica correções pontuais nos XMLs (regras padrão + personalizadas).")
            Tooltip(b2, "Gerencie quais regras do Arrumeitor serão aplicadas e crie regras próprias.")
            Tooltip(b3, "Define IniRelactCli como a menor DtContr de cada cliente (com backup).")
            Tooltip(b4, "Gera saídas no XML atual")
            Tooltip(b4, "Gera um novo XML contendo apenas as operações dos contratos listados na planilha, "
                 "acrescentando <Inf Tp='0301/0302/0308/0399'> conforme sua escolha.")
            Tooltip(b5, "Inclui uma característica especial (ex.: 19, 22, 23) nos IPOCs listados em uma planilha, ajustando os XMLs da pasta.")
            Tooltip(b6, "Renomeia os XMLs 3040 para o padrão NC<ID>, usando o CNPJ da raiz e os FIDCs cadastrados.")
            Tooltip(b7, "Valida a composição do IPOC por operação e gera relatório de divergências.")
        except Exception:
            pass

        f.grid_columnconfigure(0, weight=1)

    def _build_tab_bacen(self, parent):
        f = ttk.Frame(parent)
        f.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        b1 = ttk.Button(f, text="Validador SCR", command=self._cmd_valida_bacen)
        b2 = ttk.Button(f, text="Envia zips ao STA", command=self._cmd_envia_sta)
        b3 = ttk.Button(f, text="Retorno de protocolos dos envios", command=self._cmd_protocolos_sta)
        for i, w in enumerate((b1, b2, b3)):
            w.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
        self._mark_primary(b2)  # CTA "Envia STA"

        try:
            Tooltip(b1, "Valida os XMLs no Validador SCR do BACEN (requer Java e pastas lib/classes).")
            Tooltip(b2, "Envia arquivos .zip pelo STA (login manual). Gera relatório de protocolos.")
            Tooltip(b3, "Consulta o status dos protocolos no STA a partir de uma planilha.")
        except Exception:
            pass

        f.grid_columnconfigure(0, weight=1)

 
    def _valid_initialdir(self, initial: str | None) -> str:
        """Garante que o initialdir dos diálogos seja uma PASTA existente."""
        if not initial:
            return str(BASE_DIR)
        initial = os.path.normpath(initial)
        # se vier um arquivo, usa a pasta
        if os.path.isfile(initial):
            initial = os.path.dirname(initial)
        return initial if os.path.isdir(initial) else str(BASE_DIR)

    def _ask_dir(self, title: str, initial: str) -> str:
        path = filedialog.askdirectory(
            title=title,
            initialdir=self._valid_initialdir(initial),
            mustexist=True
        )
        return path or ""

    def _ask_file(self, title: str, initial: str, patterns=("Planilhas Excel", "*.xlsx *.xls *.xlsm")) -> str:
        path = filedialog.askopenfilename(
            title=title,
            initialdir=self._valid_initialdir(initial),
            filetypes=[patterns, ("Todos arquivos", "*.*")]
        )
        return path or ""


    def _cmd_concilia_saldo_pdd(self):
        pasta_xmls = self._ask_dir("Selecione a pasta com os XMLs (3040)", self.cfg.get("pastas", "entrada"))
        if not pasta_xmls:
            return
        mc_path = self._ask_file(
            "Selecione a planilha MC",
            self.cfg.get("caminhos", "ultimo_mc", default="")
        )
        if not mc_path:
            return
        saida = self._ask_dir("Selecione a pasta de SAÍDA", self.cfg.get("pastas", "saida"))
        if not saida:
            return
        nome_aba = None

        self.cfg.set(pasta_xmls, "pastas", "entrada")
        self.cfg.set(saida, "pastas", "saida")
        self.cfg.set(mc_path, "caminhos", "ultimo_mc")
        self.cfg.save()

        self.task_runner.start(conciliacao_concilia_saldo_pdd, self, pasta_xmls, mc_path, saida, nome_aba)

    def _cmd_ler_saldo_pdd(self):
        pasta_xmls = self._ask_dir("Selecione a pasta com os XMLs", self.cfg.get("pastas", "entrada"))
        if not pasta_xmls:
            return
        saida = self._ask_dir("Selecione a pasta de SAÍDA", self.cfg.get("pastas", "saida"))
        if not saida:
            return

        self.cfg.set(pasta_xmls, "pastas", "entrada")
        self.cfg.set(saida, "pastas", "saida")
        self.cfg.save()

        # tarefa: Excel com Resumo e Por_Arquivo_ClassOp
        self.task_runner.start(conciliacao_ler_saldo_pdd_xmls, pasta_xmls, saida)

    def _cmd_le_tags_atributos(self):
        import tkinter as tk
        from tkinter import ttk, messagebox

        pasta_xmls = self._ask_dir("Selecione a pasta com os XMLs", self.cfg.get("pastas", "entrada"))
        if not pasta_xmls:
            return
        saida = self._ask_dir("Selecione a pasta de SAÍDA", self.cfg.get("pastas", "saida"))
        if not saida:
            return

        ALL_FIELDS = ["Nome do arquivo XML", "DATA_BASE", "CNPJ","TipoPessoaDesc", "CPF/CNPJ", "Mod", "Contrt", "DtContr", "DtVencOp", "OrigemRec",
                      "Saldo (Venc)", "Provisão", "NatuOp", "Inf Tp", "Ident", "Gar Tp", "CaracEspecial",
                      "CartProvMin", "EstInstFin", "TJE", "TaxEft", "DiasAtraso", "ClasAtFin", "VlrContBr", "PorteCli",
                      "FatAnual", "IniRelactCli", "DtaProxParcela", "VlrProxParcela", "QtdParcelas", "IPOC", "CodsVenc",
                      "Cd", "Valor na inf"
                      ]

        Blocos = {
            "Básico": [
                "Nome do arquivo XML", "DATA_BASE", "CNPJ",
                "TipoPessoaDesc", "CPF/CNPJ", "Mod", "Contrt", "DtContr", "DtVencOp", "OrigemRec",
                "Saldo (Venc)", "Provisão", "NatuOp", "Inf Tp", "Ident", "Gar Tp"
            ],
            "Inf adc": [
                "Nome do arquivo XML", "DATA_BASE", "CNPJ",
                "CPF/CNPJ", "Contrt", "NatuOp", "Inf Tp", "Cd", "Ident", "Valor na inf"
            ],
            "Completo": list(ALL_FIELDS),
        }

        prev = self.cfg.get("ui", "atributos_tags_sel", default=None)
        if isinstance(prev, list) and prev:
            selected = set([f for f in prev if f in ALL_FIELDS])
        else:
            selected = set(Blocos["Básico"])

        win = tk.Toplevel(self)
        win.title("Selecionar atributos")
        win.transient(self)
        win.grab_set()

        outer = ttk.Frame(win, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(outer)
        top.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top, text="Buscar:").pack(side=tk.LEFT)
        var_q = tk.StringVar()
        ent_q = ttk.Entry(top, textvariable=var_q, width=28)
        ent_q.pack(side=tk.LEFT, padx=(6, 12))

        ttk.Label(top, text="Bloco:").pack(side=tk.LEFT)
        var_Bloco = tk.StringVar(value="Básico")
        cb_Bloco = ttk.Combobox(top, textvariable=var_Bloco, values=list(Blocos.keys()), state="readonly", width=14)
        cb_Bloco.pack(side=tk.LEFT, padx=(6, 12))

        def apply_Bloco(*_):
            Bloco = Blocos.get(var_Bloco.get(), [])
            for f in ALL_FIELDS:
                vars_by_field[f].set(f in Bloco)
            refresh_selected_from_vars()

        cb_Bloco.bind("<<ComboboxSelected>>", apply_Bloco)

        body = ttk.Frame(outer)
        body.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(body, borderwidth=0, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        frame = ttk.Frame(canvas)

        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        vars_by_field: dict[str, tk.BooleanVar] = {}
        chk_by_field: dict[str, ttk.Checkbutton] = {}

        def refresh_selected_from_vars():
            nonlocal selected
            selected = {f for f, v in vars_by_field.items() if v.get()}

        def matches_query(name: str, q: str) -> bool:
            q = (q or "").strip().lower()
            if not q:
                return True
            return q in name.lower()

        def render_checkboxes():
            for child in frame.winfo_children():
                child.destroy()
            ordered = sorted(ALL_FIELDS, key=str.lower)
            q = var_q.get()
            row = 0
            col = 0
            for f in ordered:
                if not matches_query(f, q):
                    continue
                var = vars_by_field.setdefault(f, tk.BooleanVar(value=(f in selected)))
                chk = ttk.Checkbutton(frame, text=f, variable=var)
                chk.grid(row=row, column=col, sticky="w", padx=6, pady=4)
                chk_by_field[f] = chk
                col += 1
                if col >= 2:
                    col = 0
                    row += 1
            for c in range(2):
                frame.grid_columnconfigure(c, weight=1)

        def on_search(*_):
            render_checkboxes()

        ent_q.bind("<KeyRelease>", on_search)

        btns = ttk.Frame(outer)
        btns.pack(fill=tk.X, pady=(8, 0))

        def marcar_todos(v=True):
            for f in ALL_FIELDS:
                vars_by_field[f].set(v)
            refresh_selected_from_vars()

        ttk.Button(btns, text="Selecionar tudo", command=lambda: marcar_todos(True)).pack(side=tk.LEFT)
        ttk.Button(btns, text="Limpar", command=lambda: marcar_todos(False)).pack(side=tk.LEFT, padx=(6, 0))

        result = {"ok": False, "fields": []}

        def on_ok():
            refresh_selected_from_vars()
            if not selected:
                messagebox.showwarning(APP_NAME, "Selecione ao menos um atributo.")
                return
            result["ok"] = True
            result["fields"] = [f for f in ALL_FIELDS if f in selected]
            win.destroy()

        ttk.Button(btns, text="OK", command=on_ok).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancelar", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 6))

        render_checkboxes()

        self.wait_window(win)
        if not result.get("ok"):
            return

        selected_fields = result["fields"]

        self.cfg.set(selected_fields, "ui", "atributos_tags_sel")
        self.cfg.set(pasta_xmls, "pastas", "entrada")
        self.cfg.set(saida, "pastas", "saida")
        self.cfg.save()

        self.task_runner.start(conciliacao_le_tags_atributos, pasta_xmls, saida, selected_fields)

    def _cmd_arrumeitor(self):
        pasta_xmls = self._ask_dir("Selecione a pasta com os XMLs para corrigir", self.cfg.get("pastas", "entrada"))
        if not pasta_xmls:
            return
        saida = self._ask_dir("Selecione a pasta de SAÍDA", self.cfg.get("pastas", "saida"))
        if not saida:
            return
        self.cfg.set(pasta_xmls, "pastas", "entrada")
        self.cfg.set(saida, "pastas", "saida")
        self.cfg.save()

        sel_ids = self.cfg.get("ajustes", "arrumeitor_sel", default=[])
        custom_rules = self.cfg.get("ajustes", "arrumeitor_custom", default=[]) or []
        mapping = {r["id"]: r for r in RULES_ARRUMEITOR}
        regras = [mapping[rid] for rid in sel_ids if rid in mapping]
        for cr in custom_rules:
            if isinstance(cr, dict) and "condicoes" in cr and "substituicoes" in cr:
                regras.append({
                    "id": cr.get("id", ""),
                    "label": cr.get("label", "Custom"),
                    "condicoes": cr["condicoes"],
                    "substituicoes": cr["substituicoes"],
                })

        self.task_runner.start(ajustes_arrumeitor, pasta_xmls, saida, regras)

    def _cmd_ajusta_inicio_rel(self):
        pasta_xmls = self._ask_dir("Selecione a pasta com os XMLs", self.cfg.get("pastas", "entrada"))
        if not pasta_xmls:
            return
        saida = self._ask_dir("Selecione a pasta de SAÍDA", self.cfg.get("pastas", "saida"))
        if not saida:
            return
        self.cfg.set(pasta_xmls, "pastas", "entrada")
        self.cfg.set(saida, "pastas", "saida")
        self.cfg.save()
        self.task_runner.start(ajustes_ajusta_inicio_relacionamento, pasta_xmls, saida)

    
    def _cmd_xml_com_saida_por_contrato(self):
        from tkinter import filedialog, messagebox
    
        # Seleciona XML da base anterior
        xml_anterior = filedialog.askopenfilename(
            title="Selecione o XML da data-base ANTERIOR (para comparação)",
            filetypes=[("Arquivos XML", "*.xml")]
        )
        if not xml_anterior:
            return
    
        # Seleciona XML da base atual
        xml_atual = filedialog.askopenfilename(
            title="Selecione o XML da data-base ATUAL (para inserir saídas)",
            filetypes=[("Arquivos XML", "*.xml")]
        )
        if not xml_atual:
            return
    
        try:
            verificar_saidas(xml_anterior, xml_atual)
        except Exception as e:
            messagebox.showerror(
                "Erro na Geração de Saídas",
                f"Ocorreu um erro durante o processo:\n\n{e}"
            )


    def _cmd_inclusao_carac_especial(self):
        # escolher planilha com IPOCs
        plan = self._ask_file(
            "Selecione a planilha com IPOCs (Excel)",
            self.cfg.get("pastas", "entrada"),
            patterns=("Planilhas Excel", "*.xlsx *.xls *.xlsm")
        )
        if not plan:
            return
    
        # escolher pasta de XMLs
        pasta_xmls = self._ask_dir(
            "Selecione a pasta com os XMLs que receberão a característica especial",
            self.cfg.get("pastas", "entrada")
        )
        if not pasta_xmls:
            return
    
        # pedir o código da característica especial (2 dígitos)
        win = tk.Toplevel(self)
        win.title("Característica especial a incluir")
        win.transient(self)
        win.grab_set()
    
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)
    
        ttk.Label(
            frm,
            text="Informe o código da característica especial (2 dígitos, ex.: 19, 22, 23):"
        ).pack(anchor="w", pady=(0, 6))
    
        var_ce = tk.StringVar(value="22")
        ent = ttk.Entry(frm, textvariable=var_ce, width=8)
        ent.pack(anchor="w")
        ent.focus_set()
    
        ttk.Label(
            frm,
            text="A característica será aplicada aos IPOCs listados na planilha (coluna 'IPOC').",
            foreground="gray"
        ).pack(anchor="w", pady=(4, 0))
    
        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(10, 0))
    
        ok = {"go": False}
    
        def _ok():
            v = (var_ce.get() or "").strip()
            if len(v) != 2 or not v.isdigit():
                messagebox.showerror(
                    "Valor inválido",
                    "Informe exatamente 2 dígitos, por exemplo: 19, 22 ou 23."
                )
                return
            ok["go"] = True
            win.destroy()
    
        ttk.Button(btns, text="OK", command=_ok).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancelar", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 6))
    
        self.wait_window(win)
        if not ok["go"]:
            return
    
        valor_alvo = (var_ce.get() or "").strip()
    
        # gravar pastas usadas nas configs
        self.cfg.set(os.path.dirname(plan), "pastas", "entrada")
        self.cfg.set(pasta_xmls, "pastas", "entrada")
        self.cfg.save()
    
        # disparar o worker em background
        self.task_runner.start(
            ajustes_incluir_carac_especial,
            pasta_xmls,
            plan,
            valor_alvo
        )

    def _cmd_renomear_xmls_nc(self):
        # garante que existe cadastro de FIDCs
        if not hasattr(self, "fidc_reg") or not hasattr(self.fidc_reg, "all"):
            messagebox.showwarning(APP_NAME, "Cadastro de FIDCs não disponível.")
            return
        if not self.fidc_reg.all():
            messagebox.showwarning(APP_NAME, "Nenhum FIDC cadastrado. Cadastre primeiro na aba 'FIDCs cadastrados'.")
            return

        pasta_xmls = self._ask_dir(
            "Selecione a pasta com os XMLs 3040 a renomear",
            self.cfg.get("pastas", "entrada")
        )
        if not pasta_xmls:
            return

        # confirmação
        if not messagebox.askyesno(
            APP_NAME,
            "Os arquivos XML desta pasta serão renomeados para o padrão NC<ID>, "
            "usando o CNPJ da raiz do XML para localizar o FIDC cadastrado.\n\n"
            "Deseja continuar?"
        ):
            return

        # grava última pasta usada nas configs
        self.cfg.set(pasta_xmls, "pastas", "entrada")
        self.cfg.save()

        # dispara o worker em background
        self.task_runner.start(
            ajustes_renomear_xmls_por_fidc,
            pasta_xmls,
            self.fidc_reg,
            "NC"
        )

    def _cmd_valida_composicao_ipoc(self):
        pasta_xmls = self._ask_dir("Selecione a pasta com os XMLs", self.cfg.get("pastas", "entrada"))
        if not pasta_xmls:
            return
        saida = self._ask_dir("Selecione a pasta de SAÍDA", self.cfg.get("pastas", "saida"))
        if not saida:
            return

        self.cfg.set(pasta_xmls, "pastas", "entrada")
        self.cfg.set(saida, "pastas", "saida")
        self.cfg.save()

        self.task_runner.start(ajustes_valida_composicao_ipoc, pasta_xmls, saida)



    def _cmd_valida_bacen(self):
        pasta_xmls = self._ask_dir("Selecione a pasta com os XMLs", self.cfg.get("pastas", "entrada"))
        if not pasta_xmls:
            return
        caminho_validador = self.cfg.get("caminhos", "validador_bacen", default="")
        if not caminho_validador:
            messagebox.showwarning(APP_NAME, "Configure o caminho do Validador (menu Config → Configurações).")
            return
        saida = self._ask_dir("Selecione a pasta de SAÍDA/Validados", self.cfg.get("pastas", "saida"))
        if not saida:
            return
        self.task_runner.start(bacen_valida_xmls, pasta_xmls, caminho_validador, saida)

    
        
    def _cmd_envia_sta(self):

        pasta_arqs = self._ask_dir("Selecione a pasta dos ZIPs para envio STA", self.cfg.get("pastas", "entrada"))
        if not pasta_arqs:
            return
    
        cdrv = self.cfg.get("caminhos", "chrome_driver", default="")
        if not cdrv:
            messagebox.showwarning(APP_NAME, "Configure o caminho do ChromeDriver em Config → Configurações.")
            return
    
        saida = self._ask_dir("Selecione a pasta de SAÍDA para o relatório", self.cfg.get("pastas", "saida"))
        if not saida:
            saida = None  # relatório opcional

        # lembrar último código usado, default 104 (3040)
        cod_default = str(self.cfg.get("sta", "cod_tipo_arquivo", default="104") or "104")
        lote_default = str(self.cfg.get("sta", "tamanho_lote", default="2") or "2")
        max_mb_default = str(self.cfg.get("sta", "max_mb_lote", default="150") or "150")

        #  diálogo: confirmar código e parâmetros de lote
        win = tk.Toplevel(self)
        win.title("Envio STA")
        win.transient(self)
        win.grab_set()
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Confirme o código do tipo de arquivo para o envio no STA:").pack(anchor="w")
        ttk.Label(frm, text="Dica: para 3040 o código normalmente é 103. Verifique se o BACEN mudou (pressione ctrl + U na tela de envio do STA para consultar o código).",
                  foreground="#b22222").pack(anchor="w", pady=(0,6))  # lembrete em vermelho discreto

        var_cod = tk.StringVar(value=cod_default)
        ent = ttk.Entry(frm, textvariable=var_cod, width=10, justify="center")
        ent.pack(anchor="w")
        ent.focus_set()

        # ajuda visual
        ttk.Label(frm, text="O código deve ter 3 dígitos (ex.: 105).").pack(anchor="w", pady=(6,0))

        frame_lotes = ttk.Frame(frm)
        frame_lotes.pack(fill=tk.X, pady=(10,0))
        ttk.Label(frame_lotes, text="Itens por lote:").grid(row=0, column=0, sticky="w")
        var_lote = tk.StringVar(value=lote_default)
        ttk.Entry(frame_lotes, textvariable=var_lote, width=6, justify="center").grid(row=0, column=1, sticky="w", padx=(6, 14))
        ttk.Label(frame_lotes, text="Tamanho máx. por lote (MB):").grid(row=0, column=2, sticky="w")
        var_mb = tk.StringVar(value=max_mb_default)
        ttk.Entry(frame_lotes, textvariable=var_mb, width=8, justify="center").grid(row=0, column=3, sticky="w", padx=(6, 0))

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(10,0))

        decided = {"ok": False}
        def _ok():
            cod = var_cod.get().strip()
            if not re.fullmatch(r"\d{2}", cod):
                messagebox.showwarning(APP_NAME, "Informe um código válido com 3 dígitos (ex.: 105).")
                return
            try:
                lote_val = int(var_lote.get().strip() or "0")
                if lote_val <= 0:
                    raise ValueError
            except Exception:
                messagebox.showwarning(APP_NAME, "Informe um tamanho de lote (itens) maior que zero.")
                return
            try:
                mb_val = int(var_mb.get().strip() or "0")
                if mb_val <= 0:
                    raise ValueError
            except Exception:
                messagebox.showwarning(APP_NAME, "Informe um limite de MB por lote maior que zero.")
                return
            decided["ok"] = True
            win.destroy()

        ttk.Button(btns, text="Enviar", command=_ok).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancelar", command=win.destroy).pack(side=tk.RIGHT, padx=(0,6))
    
        self.wait_window(win)
        if not decided["ok"]:
            return

        cod_tipo_arquivo = var_cod.get().strip()
        tamanho_lote = int(var_lote.get().strip())
        max_mb_lote = int(var_mb.get().strip())


        # Definir a pasta "Enviados" 
        from pathlib import Path
        enviados_dir = str((Path(pasta_arqs) / "Enviados").resolve())
        # grava no config para relembrar depois
        self.cfg.set(enviados_dir, "pastas", "enviados_sta")

    
        # persistir escolhas feitas
        self.cfg.set(pasta_arqs, "pastas", "entrada")
        if saida:
            self.cfg.set(saida, "pastas", "saida")
        self.cfg.set(cod_tipo_arquivo, "sta", "cod_tipo_arquivo")
        self.cfg.set(str(tamanho_lote), "sta", "tamanho_lote")
        self.cfg.set(str(max_mb_lote), "sta", "max_mb_lote")
        self.cfg.save()

        # dispara o envio
        self.task_runner.start(
            bacen_envia_sta,
            pasta_arqs,          # pasta_arquivos
            None,                # credenciais_sta (ignorado)
            cdrv,                # caminho_chromedriver
            saida,               # pasta_saida
            login_timeout_secs=120,
            tamanho_lote=tamanho_lote,
            max_mb_por_lote=max_mb_lote,
            cod_tipo_arquivo=cod_tipo_arquivo,
            refresh_every=0,
        )


    def _cmd_protocolos_sta(self):
        excel_path = self._ask_file("Selecione a planilha de Protocolos (Excel)",
                                    self.cfg.get("pastas", "entrada"),
                                    patterns=("Planilhas Excel", "*.xlsx *.xls *.xlsm"))
        if not excel_path:
            return
        saida = self._ask_dir("Selecione a pasta de SAÍDA para salvar protocolos",
                              self.cfg.get("pastas", "saida"))
        if not saida:
            return
        cred = self._get_sta_credentials()
        cdriver = self.cfg.get("caminhos", "chrome_driver", default="")
        if not cdriver or not os.path.isfile(cdriver):
            messagebox.showwarning(APP_NAME, "Configure o ChromeDriver em Config → Configurações.")
            return
        self.cfg.set(saida, "pastas", "saida")
        self.cfg.save()
        self.task_runner.start(bacen_retorna_protocolos_sta, excel_path, saida, cred, cdriver)


    def _on_task_start(self):
        try:
            if self.cfg.get("ui", "limpar_log_auto", default=False):
                self._clear_log()
        except Exception:
            pass
        self.status_label.config(text="Executando…")
        if hasattr(self, "btn_cancel"):
            self.btn_cancel.config(state=tk.NORMAL)

    def _on_task_finish(self):
        self.status_label.config(text="Pronto" if not (getattr(self.task_runner, "_cancel_flag", None) and self.task_runner._cancel_flag.is_set()) else "Cancelado")
        if hasattr(self, "btn_cancel"):
            self.btn_cancel.config(state=tk.DISABLED)

    def _on_task_error(self, err: Exception):
        self.status_label.config(text="Erro")
        if hasattr(self, "btn_cancel"):
            self.btn_cancel.config(state=tk.DISABLED)
        messagebox.showerror(APP_NAME, f"Erro ao executar: {err}")

    def _cmd_cancel(self):
        if self.task_runner and self.task_runner.is_running():
            self.task_runner.cancel()
            self.status_label.config(text="Cancelando…")
            logger.info("Solicitado cancelamento pelo usuário.")

    def _cmd_testar_ambiente(self):
        ok = True

        # Validador BACEN
        vp = self.cfg.get("caminhos", "validador_bacen", default="")
        if not (vp and os.path.isdir(vp) and os.path.isdir(os.path.join(vp, "lib")) and os.path.isdir(os.path.join(vp, "classes"))):
            logger.warning("[TESTE] Validador: pasta inválida ou faltam 'lib' e 'classes'.")
            ok = False
        else:
            logger.info("[TESTE] Validador: OK")

        # ChromeDriver
        cdrv = self.cfg.get("caminhos", "chrome_driver", default="")
        if not (cdrv and os.path.isfile(cdrv)):
            logger.warning("[TESTE] ChromeDriver: caminho inválido.")
            ok = False
        else:
            logger.info("[TESTE] ChromeDriver: OK")

        # Java (necessário para validador bacen)
        try:
            import subprocess
            r = subprocess.run(["java", "-version"], capture_output=True, text=True)
            first = (r.stderr or r.stdout).splitlines()[0] if (r.stderr or r.stdout) else "Java não respondeu"
            logger.info("[TESTE] Java: " + first)
        except Exception:
            logger.warning("[TESTE] Java: não encontrado no PATH.")
            ok = False

        logger.info("[TESTE] Resultado geral: " + ("OK" if ok else "ATENÇÃO — ver itens acima"))


    def _open_config_dialog(self):
        win = tk.Toplevel(self)
        win.title("Configurações")
        win.transient(self)
        win.grab_set()
        win.geometry("560x360")

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        # Caminho Validador Bacen
        ttk.Label(frm, text="Validador BACEN — pasta-raiz (contém 'lib' e 'classes')").grid(row=0, column=0, sticky="w")
        var_validador = tk.StringVar(value=self.cfg.get("caminhos", "validador_bacen", default=""))
        e_val = ttk.Entry(frm, textvariable=var_validador)
        e_val.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        b_val = ttk.Button(
            frm,
            text="Procurar…",
            command=lambda: var_validador.set(
                self._ask_dir(
                    "Selecione a PASTA-RAIZ do Validador (contém 'lib' e 'classes')",
                    self.cfg.get("caminhos", "validador_bacen", default="")
                )
            )
        )
        b_val.grid(row=1, column=1, sticky="ew")

        # ChromeDriver
        ttk.Separator(frm).grid(row=9, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(frm, text="ChromeDriver (chromedriver.exe)").grid(row=10, column=0, sticky="w")
        var_chromedriver = tk.StringVar(value=self.cfg.get("caminhos", "chrome_driver", default=""))
        e_cdrv = ttk.Entry(frm, textvariable=var_chromedriver)
        e_cdrv.grid(row=11, column=0, sticky="ew", padx=(0, 6))
        b_cdrv = ttk.Button(
            frm,
            text="Procurar…",
            command=lambda: var_chromedriver.set(
                self._ask_file("Selecione o ChromeDriver (chromedriver.exe)",
                               self.cfg.get("caminhos", "chrome_driver", default=""),
                               patterns=("ChromeDriver", "chromedriver.exe"))
            )
        )
        b_cdrv.grid(row=11, column=1, sticky="ew")

        # Opções de Log
        ttk.Separator(frm).grid(row=7, column=0, columnspan=2, sticky="ew", pady=8)
        var_clear_on_start = tk.BooleanVar(value=bool(self.cfg.get("ui", "limpar_log_auto", default=False)))
        chk = ttk.Checkbutton(frm, text="Limpar log ao iniciar nova tarefa", variable=var_clear_on_start)
        chk.grid(row=8, column=0, columnspan=2, sticky="w")

        frm.grid_columnconfigure(0, weight=1)

        def salvar():
            self.cfg.set(var_chromedriver.get(), "caminhos", "chrome_driver")
            self.cfg.set(var_validador.get(), "caminhos", "validador_bacen")
            self.cfg.set(bool(var_clear_on_start.get()), "ui", "limpar_log_auto")
            self.cfg.save()
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btns, text="Salvar", command=salvar).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancelar", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 6))


    def _open_arrumeitor_rules_dialog(self):
        win = tk.Toplevel(self)
        win.title("Configurar Arrumeitor")
        win.transient(self)
        win.grab_set()
        win.geometry("820x520")

        root = ttk.Frame(win, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        # Coluna esquerda: Regras padrão
        left = ttk.Labelframe(root, text="Regras padrão")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        sel_ids = set(self.cfg.get("ajustes", "arrumeitor_sel", default=[]) or [])
        vars_by_id = {}
        for i, rule in enumerate(RULES_ARRUMEITOR):
            var = tk.BooleanVar(value=(rule["id"] in sel_ids))
            vars_by_id[rule["id"]] = var
            ttk.Checkbutton(left, text=rule["label"], variable=var).grid(
                row=i, column=0, sticky="w", padx=6, pady=4
            )
        left.grid_columnconfigure(0, weight=1)

        # Coluna direita: Regras personalizadas
        right = ttk.Labelframe(root, text="Regras personalizadas")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree = ttk.Treeview(right, columns=("label", "cond", "sub"), show="headings", height=9)
        tree.heading("label", text="Título")
        tree.heading("cond", text="Condições")
        tree.heading("sub", text="Substituições")
        tree.column("label", width=160, anchor="w")
        tree.column("cond", width=230, anchor="w")
        tree.column("sub", width=230, anchor="w")
        tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        skin_treeview(tree)

        custom_rules = self.cfg.get("ajustes", "arrumeitor_custom", default=[]) or []
        for r in custom_rules:
            titulo = r.get("label", "Custom")
            cond = "; ".join([f"{k}={v}" for k, v in (r.get("condicoes") or {}).items()])
            sub = "; ".join([f"{k}={v}" for k, v in (r.get("substituicoes") or {}).items()])
            tree.insert("", tk.END, values=(titulo, cond, sub))

        form = ttk.Frame(right)
        form.pack(fill=tk.X, padx=6, pady=(0, 6))

        ttk.Label(form, text="Título:").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="Condições (k=v; k2=v2):").grid(row=1, column=0, sticky="w")
        ttk.Label(form, text="Substituições (k=v; k2=v2):").grid(row=2, column=0, sticky="w")

        var_lbl = tk.StringVar()
        var_cond = tk.StringVar()
        var_sub = tk.StringVar()

        e_lbl = ttk.Entry(form, textvariable=var_lbl)
        e_cond = ttk.Entry(form, textvariable=var_cond)
        e_sub = ttk.Entry(form, textvariable=var_sub)
        e_lbl.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        e_cond.grid(row=1, column=1, sticky="ew", padx=(6, 0))
        e_sub.grid(row=2, column=1, sticky="ew", padx=(6, 0))
        form.grid_columnconfigure(1, weight=1)

        def parse_pairs(s: str) -> dict:
            return _parse_kv_pairs(s)

        def add_or_update(update=False):
            titulo = (var_lbl.get() or "Custom").strip()
            cond = parse_pairs(var_cond.get())
            sub = parse_pairs(var_sub.get())
            if not cond or not sub:
                messagebox.showwarning(APP_NAME, "Informe ao menos uma condição e uma substituição (k=v).")
                return
            if update:
                sel = tree.selection()
                if not sel:
                    messagebox.showinfo(APP_NAME, "Selecione uma linha para atualizar.")
                    return
                tree.item(sel[0], values=(titulo,
                                          "; ".join([f"{k}={v}" for k, v in cond.items()]),
                                          "; ".join([f"{k}={v}" for k, v in sub.items()])))
            else:
                tree.insert("", tk.END, values=(titulo,
                                                "; ".join([f"{k}={v}" for k, v in cond.items()]),
                                                "; ".join([f"{k}={v}" for k, v in sub.items()])))

            var_lbl.set("")
            var_cond.set("")
            var_sub.set("")
            e_lbl.focus_set()

        def on_tree_select(_evt=None):
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0], "values")
            if not vals:
                return
            var_lbl.set(vals[0])
            var_cond.set(vals[1])
            var_sub.set(vals[2])

        tree.bind("<<TreeviewSelect>>", on_tree_select)

        btns_row = ttk.Frame(right)
        btns_row.pack(fill=tk.X, padx=6, pady=(0, 6))
        ttk.Button(btns_row, text="Adicionar", command=lambda: add_or_update(False)).pack(side=tk.LEFT)
        ttk.Button(btns_row, text="Atualizar", command=lambda: add_or_update(True)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns_row, text="Remover", command=lambda: [tree.delete(i) for i in tree.selection()]).pack(side=tk.LEFT, padx=(6, 0))

        footer = ttk.Frame(win)
        footer.pack(fill=tk.X, padx=10, pady=(6, 10))

        def salvar():
            sel_ids_new = [rid for rid, var in vars_by_id.items() if var.get()]
            new_custom = []
            for iid in tree.get_children():
                titulo, cond_txt, sub_txt = tree.item(iid, "values")
                new_custom.append({
                    "id": "",
                    "label": titulo or "Custom",
                    "condicoes": _parse_kv_pairs(cond_txt),
                    "substituicoes": _parse_kv_pairs(sub_txt),
                })
            self.cfg.set(sel_ids_new, "ajustes", "arrumeitor_sel")
            self.cfg.set(new_custom, "ajustes", "arrumeitor_custom")
            self.cfg.save()
            win.destroy()
            logger.info("Configurações do Arrumeitor salvas.")

        ttk.Button(footer, text="Salvar", command=salvar).pack(side=tk.RIGHT)
        ttk.Button(footer, text="Cancelar", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 6))


    def _get_sta_credentials(self):
        user = self.cfg.get("caminhos", "sta_user", default="")
        pwd = self.cfg.get("caminhos", "sta_pass", default="")
        cdrv = self.cfg.get("caminhos", "chrome_driver", default="")
        if user and pwd:
            return {"user": user, "password": pwd, "chromedriver": cdrv}
        return None



if __name__ == "__main__":
    import os, json, re
    
    def _only_digits(s: str) -> str:
        import re
        return re.sub(r"\D+", "", str(s or ""))
    
    def raiz_cnpj(s: str) -> str:
        d = _only_digits(s)
        # pega até 8 primeiros e completa com zeros à esquerda
        return d[:8].zfill(8)

    def _worker_FIDC_DB_PATH() -> Path:
        p = os.environ.get("FIDCS_DB","")
        if p:
            return Path(p)
        return Path(__file__).resolve().parent / "fidcs_db.json"
    
    def build_fidc_lookup() -> dict:

        path = _worker_FIDC_DB_PATH()
        try:
            rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            rows = []
    
        lk = {}
        for r in rows or []:
            # aceita chaves com variações de nome
            raiz_val = r.get("raiz_cnpj") or r.get("RAIZ CNPJ") or r.get("raiz_CNPJ") or r.get("Raiz_CNPJ") or r.get("raiz") or ""
            if not raiz_val:
                raiz_val = r.get("cnpj", "")
                raiz_val = str(raiz_val).strip()
            key = raiz_cnpj(raiz_val)
            if not key:
                continue
            lk[key] = {
                "id": str(r.get("id", "")).strip(),
                "legado": (r.get("legado", "") or r.get("LEGADO", "") or "").strip(),
                "fundos": (r.get("fundos", "") or r.get("FUNDOS", "") or "").strip(),
                "carteira": (r.get("carteira", "") or r.get("Carteira", "") or "").strip(),
                "cnpj": (r.get("cnpj", "") or r.get("CNPJ", "") or "").strip(),
            }
        return lk
    
    if "--worker-concilia" in sys.argv:
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--worker-concilia"]
        import argparse
        import pandas as pd
        import xml.etree.ElementTree as ET




        def parse_brl_to_float(x):
            if x is None or (isinstance(x, float) and pd.isna(x)):
                return 0.0
            if isinstance(x, (int, float)):
                return float(x)
            s = str(x).strip()
            if s == "" or s.lower() in {"nan", "none"}:
                return 0.0
            neg = s.startswith("(") and s.endswith(")")
            if neg:
                s = s[1:-1]
            s = s.replace(" ", "").replace(".", "").replace(",", ".")
            allowed = set("0123456789.-")
            s = "".join(ch for ch in s if ch in allowed)
            try:
                v = float(s)
                return -v if neg else v
            except Exception:
                return 0.0

        def processar_xml_incremental(caminho_xml):
            if os.path.getsize(caminho_xml) == 0:
                return 0.0, 0.0, 0.0
            saldo_cli = pdd_cli = prejuizo_cli = 0.0
            saldo_agreg = pdd_agreg = prejuizo_agreg = 0.0
            try:
                for event, elem in ET.iterparse(caminho_xml, events=("start", "end")):
                    if event == "end" and elem.tag == "Cli":
                        for operacao in elem.findall('Op'):
                            for vencimento in operacao.findall('Venc'):
                                for vcod, valor in vencimento.attrib.items():
                                    valor_f = float(valor)
                                    if str(vcod).startswith(("v310", "v320", "v330")):
                                        prejuizo_cli += valor_f
                                    else:
                                        saldo_cli += valor_f
                            pdd_cli += float(operacao.attrib.get('ProvConsttd', 0))
                        elem.clear()
                    elif event == "end" and elem.tag == "Agreg":
                        for vencimento in elem.findall('Venc'):
                            for vcod, valor in vencimento.attrib.items():
                                valor_f = float(valor)
                                if str(vcod).startswith(("v310", "v320", "v330")):
                                    prejuizo_agreg += valor_f
                                else:
                                    saldo_agreg += valor_f
                        pdd_agreg += float(elem.attrib.get('ProvConsttd', 0))
                        elem.clear()
                saldo_total = saldo_cli + saldo_agreg
                pdd_total = pdd_cli + pdd_agreg
                prejuizo_total = prejuizo_cli + prejuizo_agreg
                return saldo_total, pdd_total, prejuizo_total
            except ET.ParseError:
                return 0.0, 0.0, 0.0

        def extrair_dtbase_e_cnpj_arquivo(caminho_xml):
            try:
                root = ET.parse(caminho_xml).getroot()
                return root.attrib.get('DtBase', ''), root.attrib.get('CNPJ', '')
            except ET.ParseError:
                return "", ""

        def somar_por_raiz_xml(diretorio_xml: str, fidc_lookup: dict):

            agreg = {}  # por raiz
            detalhe_rows = []  # por arquivo
        
            def _get_info(raiz8: str):
                info = fidc_lookup.get(raiz8) or {}
                return (
                    str(info.get("id", "")).strip(),
                    (info.get("legado", "") or "").strip(),
                    (info.get("fundos", "") or "").strip(),
                )
        
            for dirpath, _, files in os.walk(diretorio_xml):
                for nome in files:
                    if not nome.lower().endswith(".xml"):
                        continue
                    caminho = os.path.join(dirpath, nome)
        
                    # extrai DtBase e CNPJ do arquivo
                    dtbase, cnpj = extrair_dtbase_e_cnpj_arquivo(caminho)
                    raiz = raiz_cnpj(cnpj)
        
                    # soma valores do arquivo
                    saldo, pdd, prejuizo = processar_xml_incremental(caminho)
        
                    # detalhe por arquivo
                    id_, legado, fundos = _get_info(raiz)
                    detalhe_rows.append({
                        "NomeArquivo": nome,
                        "Raiz_CNPJ": raiz,
                        "ID": id_,
                        "LEGADO": legado,
                        "FUNDOS": fundos,
                        "Saldo_XML": saldo,
                        "PDD_XML": pdd,
                        "Prejuizo_XML": prejuizo,
                        "DtBase_arquivo": dtbase or "",
                    })
        
                    # agregado por raiz
                    if raiz not in agreg:
                        agreg[raiz] = {
                            "Raiz_CNPJ": raiz,
                            "Saldo_XML": 0.0,
                            "PDD_XML": 0.0,
                            "Prejuizo_XML": 0.0,
                            "DtBase_mais_recente": dtbase or "",
                            # fixa ID/LEGADO/FUNDOS aqui
                            "ID": id_,
                            "LEGADO": legado,
                            "FUNDOS": fundos,
                        }
                    agreg[raiz]["Saldo_XML"] += saldo
                    agreg[raiz]["PDD_XML"] += pdd
                    agreg[raiz]["Prejuizo_XML"] += prejuizo
                    if dtbase and (agreg[raiz]["DtBase_mais_recente"] == "" or dtbase > agreg[raiz]["DtBase_mais_recente"]):
                        agreg[raiz]["DtBase_mais_recente"] = dtbase
        
            import pandas as pd
            df_xml = pd.DataFrame(list(agreg.values()))
            if df_xml.empty:
                df_xml = pd.DataFrame(columns=[
                    "Raiz_CNPJ","Saldo_XML","PDD_XML","Prejuizo_XML",
                    "DtBase_mais_recente","ID","LEGADO","FUNDOS"
                ])
        
            df_por_arquivo = pd.DataFrame(detalhe_rows)
            if df_por_arquivo.empty:
                df_por_arquivo = pd.DataFrame(columns=[
                    "NomeArquivo","Raiz_CNPJ","ID","LEGADO","FUNDOS",
                    "Saldo_XML","PDD_XML","Prejuizo_XML","DtBase_arquivo"
                ])
        
            return df_xml, df_por_arquivo



        def ler_planilha_mc(caminho_planilha, nome_aba=None):
            df = pd.read_excel(caminho_planilha, sheet_name=nome_aba) if nome_aba else pd.read_excel(caminho_planilha)
            cols = {c: str(c).strip().lower() for c in df.columns}
            df.columns = [cols[c] for c in df.columns]
            poss_raiz = [k for k in df.columns if k in {"raiz cgc", "raiz_cgc", "raiz do cgc", "raiz cnpj", "raiz_cnpj"}]
            col_raiz = poss_raiz[0] if poss_raiz else "raiz cgc"
            if col_raiz not in df.columns:
                raise ValueError("Não encontrei a coluna 'Raiz CGC' na planilha.")
            poss_saldo = [k for k in df.columns if k in {"saldo", "vlr saldo", "valor saldo"}]
            col_saldo = poss_saldo[0] if poss_saldo else "saldo"
            if col_saldo not in df.columns:
                raise ValueError("Não encontrei a coluna 'Saldo' na planilha.")
            poss_pdd = [k for k in df.columns if k in {"pdd", "provisao", "provisão"}]
            col_pdd = poss_pdd[0] if poss_pdd else "pdd"
            if col_pdd not in df.columns:
                raise ValueError("Não encontrei a coluna 'PDD' na planilha.")
            df["_Raiz_CNPJ"] = df[col_raiz].map(raiz_cnpj)
            df["_Saldo"] = df[col_saldo].map(parse_brl_to_float)
            df["_PDD"] = df[col_pdd].map(parse_brl_to_float)
            mc = (
                df.groupby("_Raiz_CNPJ", dropna=False)[["_Saldo", "_PDD"]]
                .sum()
                .reset_index()
                .rename(columns={"_Raiz_CNPJ": "Raiz_CNPJ", "_Saldo": "Saldo_MC", "_PDD": "PDD_MC"})
            )
            return mc

        def _classificar_analise(row):
            pdd_mc = float(row.get("PDD_MC", 0.0))
            pdd_xml = float(row.get("PDD_XML", 0.0))
            dif_pdd = float(row.get("Dif_PDD", 0.0))
            saldo_xml = float(row.get("Saldo_XML", 0.0))
            if pdd_mc == 0 and pdd_xml != 0:
                return "Confirmar PDD com risco"
            if dif_pdd != 0:
                limite = abs(saldo_xml) * 0.01
                return "Acima de 1%" if abs(dif_pdd) > limite else "Abaixo de 1%"
            return "Sem diferença"
        
        
        def conciliar_por_arquivo(df_por_arquivo: pd.DataFrame, df_mc: pd.DataFrame) -> pd.DataFrame:

            # junta MC pela raiz
            base = pd.merge(df_por_arquivo, df_mc, on="Raiz_CNPJ", how="left")
        
            # garante numéricos
            for col in ["Saldo_XML", "PDD_XML", "Saldo_MC", "PDD_MC", "Prejuizo_XML"]:
                if col not in base.columns:
                    base[col] = 0.0
            base[["Saldo_XML", "PDD_XML", "Saldo_MC", "PDD_MC", "Prejuizo_XML"]] = \
                base[["Saldo_XML", "PDD_XML", "Saldo_MC", "PDD_MC", "Prejuizo_XML"]].fillna(0.0)
        
            # diferenças + análise
            base["Dif_Saldo"] = base["Saldo_XML"] - base["Saldo_MC"]
            base["Dif_PDD"]   = base["PDD_XML"]   - base["PDD_MC"]
            base["Análise"]   = base.apply(_classificar_analise, axis=1)
        
            # ordena: maiores diferenças primeiro
            base = base.sort_values(by=["Dif_Saldo", "Dif_PDD"], ascending=False, kind="stable").reset_index(drop=True)
        
            # ordem/nomes finais das colunas
            cols_final = [
                "NomeArquivo",
                "Raiz_CNPJ",
                "ID", "LEGADO", "FUNDOS",
                "Saldo_XML", "Saldo_MC", "Dif_Saldo",
                "PDD_XML",   "PDD_MC",   "Dif_PDD",
                "Análise",
                "Prejuizo_XML",
                "DtBase_arquivo",
            ]
            # mantém só as que existem
            cols_final = [c for c in cols_final if c in base.columns]
            return base[cols_final]


        def exportar_excel_por_arquivo(df_final: pd.DataFrame, caminho_saida: str):
            with pd.ExcelWriter(caminho_saida, engine="xlsxwriter") as writer:
                sh = "Conciliacao_por_Arquivo"
                df_final.to_excel(writer, sheet_name=sh, index=False)
        
                wb = writer.book
                ws = writer.sheets[sh]
        
                num_fmt = wb.add_format({"num_format": "#.##0,00"})  # pt-BR
                header_fmt = wb.add_format({
                    "bold": True,
                    "bg_color": "#1F4E78",
                    "font_color": "#FFFFFF",
                    "border": 1,
                    "align": "center",
                    "valign": "vcenter",
                })
        
                # cabeçalhos e larguras
                for col_idx, name in enumerate(df_final.columns):
                    ws.write(0, col_idx, name, header_fmt)
                    ws.set_column(col_idx, col_idx, 18)
        
                # colunas numéricas com formatação
                num_cols = {"Saldo_XML","Saldo_MC","Dif_Saldo","PDD_XML","PDD_MC","Dif_PDD","Prejuizo_XML"}
                for col_idx, name in enumerate(df_final.columns):
                    if name in num_cols:
                        ws.set_column(col_idx, col_idx, 18, num_fmt)
        
                # filtro
                ws.autofilter(0, 0, len(df_final), len(df_final.columns) - 1)
        
                # realce por texto na coluna Análise (se existir)
                try:
                    col_analise = df_final.columns.get_loc("Análise")
                    fmt_ok   = wb.add_format({"font_color": "#305496"})
                    fmt_warn = wb.add_format({"font_color": "#9C0006"})
                    fmt_attn = wb.add_format({"font_color": "#7F6000"})
                    ws.conditional_format(1, col_analise, len(df_final), col_analise,
                                          {"type":"text","criteria":"containing","value":"Abaixo de 1%","format":fmt_ok})
                    ws.conditional_format(1, col_analise, len(df_final), col_analise,
                                          {"type":"text","criteria":"containing","value":"Acima de 1%","format":fmt_warn})
                    ws.conditional_format(1, col_analise, len(df_final), col_analise,
                                          {"type":"text","criteria":"containing","value":"Confirmar PDD com risco","format":fmt_attn})
                except Exception:
                    pass


        ap = argparse.ArgumentParser()
        ap.add_argument('--xml-dir', required=True)
        ap.add_argument('--mc', required=True)
        ap.add_argument('--out', required=True)
        ap.add_argument('--sheet', default=None)
        args = ap.parse_args()

        try:
            print("Lendo e somando XMLs por raiz do CNPJ...", flush=True)
            try:
                fidc_lookup = build_fidc_lookup()
            except Exception:
                fidc_lookup = {}
            df_xml, df_por_arquivo = somar_por_raiz_xml(args.xml_dir, fidc_lookup)
            print("Lendo planilha MC...", flush=True)
            df_mc = ler_planilha_mc(args.mc, args.sheet)
            print("Conciliando por arquivo...", flush=True)
            df_final = conciliar_por_arquivo(df_por_arquivo, df_mc)
            print(f"Exportando para: {args.out}", flush=True)
            exportar_excel_por_arquivo(df_final, args.out)
            print("Conciliação concluída.", flush=True)
            sys.exit(0)
        except SystemExit:
            raise
        except Exception as e:
            print(f"ERRO: {e}", flush=True)
            sys.exit(1)
    else:
        try:
            app = App()
            app.mainloop()
        except Exception:
            logger.exception("Falha fatal no aplicativo.")
            raise
