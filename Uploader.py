from __future__ import annotations

import argparse
import contextlib
import io
import itertools
import json
import logging
import mimetypes
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import qrcode
from PIL import Image, ImageOps

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# ════════════════════════════════════════════════════════════════════
#  Chemins et constantes
# ════════════════════════════════════════════════════════════════════
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

TOOLS_DIR = BASE_DIR / "tools"
DOWNLOAD_DIR = BASE_DIR / "downloads"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"
SETTINGS_FILE = BASE_DIR / "settings.json"
LOG_FILE = BASE_DIR / "app.log"
FFMPEG_META = TOOLS_DIR / "ffmpeg.json"

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
LOCAL_NAME = "yt-dlp.exe" if IS_WIN else "yt-dlp"
YTDLP_PATH = TOOLS_DIR / LOCAL_NAME

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DRIVE_FOLDER_NAME = "YouTube Audio"
DRIVE_API_URL = "https://console.cloud.google.com/apis/library/drive.googleapis.com"
CLOUD_PROJECT_URL = "https://console.cloud.google.com/projectcreate"
CLOUD_CONSENT_URL = "https://console.cloud.google.com/auth/overview"
CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
CLOUD_AUDIENCE_URL = "https://console.cloud.google.com/auth/audience"
CLOUD_CLIENT_URL = "https://console.cloud.google.com/auth/clients/create"
CLOUD_BRANDING_URL = "https://console.cloud.google.com/auth/branding"
GITHUB_SIGNUP_URL = "https://github.com/signup"
GITHUB_NEW_REPO_URL = "https://github.com/new"
APP_NAME = "SAJJEEP-Uploader"
SITE_REPO = "SAJJEEP-Uploader"

YTDLP_API = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
YTDLP_DL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/{asset}"
DENO_DL = "https://github.com/denoland/deno/releases/latest/download/{asset}"
DENO_API = "https://api.github.com/repos/denoland/deno/releases/latest"
FFMPEG_ASSET = "ffmpeg-master-latest-win64-gpl.zip"
FFMPEG_URL = f"https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/{FFMPEG_ASSET}"
FFMPEG_API = "https://api.github.com/repos/yt-dlp/FFmpeg-Builds/releases/latest"
FFMPEG_MAX_AGE_DAYS = 30

STRATEGIES = [
    ("standard", []),
    ("android_vr", ["--extractor-args", "youtube:player_client=android_vr"]),
    ("web_embedded", ["--extractor-args", "youtube:player_client=web_embedded"]),
    ("tv", ["--extractor-args", "youtube:player_client=tv"]),
]

FATAL_MARKERS = [
    "private video", "video has been removed", "has been terminated",
    "members-only", "join this channel", "copyright", "is not a valid url",
    "unsupported url", "no space left", "ffmpeg not found",
    "ffmpeg is not installed", "premieres in", "live event will begin",
]

# Messages du journal : « en cours », puis conclusion qui remplace la ligne.
TASK_TEXT = {
    #              en cours                  réussi                          échoué                       inchangé
    "download": ("{n} : téléchargement…", "{n} : téléchargement réussi", "{n} : téléchargement échoué", None),
    "update":   ("{n} : mise à jour…",    "{n} : mise à jour réussie",    "{n} : non mis à jour",       "{n} : déjà à jour"),
}

# Documents / image finale
RENDER_DPI = 200
PREVIEW_SIZE = (300, 390)
DOC_TYPES = [
    ("Documents", "*.pdf *.docx *.doc *.odt *.rtf"),
    ("PDF", "*.pdf"),
    ("Word", "*.docx *.doc"),
    ("Tous les fichiers", "*.*"),
]
EXPORT_TYPES = {".png": "PNG", ".pdf": "PDF", ".docx": "Word"}

# Couleurs (noir / gris / blanc)
BG = "#0d0d0d"
PANEL = "#171717"
BORDER = "#2a2a2a"
FG = "#f2f2f2"
MUTED = "#9a9a9a"
BTN = "#2b2b2b"
BTN_HOVER = "#3a3a3a"
OK_COLOR = "#d9d9d9"
ERR_COLOR = "#ff8a8a"

# ════════════════════════════════════════════════════════════════════
#  Arguments, logging, debug
# ════════════════════════════════════════════════════════════════════
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--quiet", action="store_true")
_parser.add_argument("--verbose", action="store_true")
ARGS, _unknown = _parser.parse_known_args()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("app")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    try:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    if sys.stdout is not None:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.WARNING if ARGS.quiet else logging.DEBUG if ARGS.verbose else logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    return logger


log = setup_logging()


@contextlib.contextmanager
def step(name: str):
    t0 = time.time()
    log.info("▶ %s", name)
    try:
        yield
    except Exception as e:
        log.error("✘ %s (%.1fs) : %s", name, time.time() - t0, e)
        raise
    else:
        log.info("✔ %s (%.1fs)", name, time.time() - t0)


def _excepthook(exc_type, exc, tb):
    log.critical("Exception non gérée", exc_info=(exc_type, exc, tb))


def _thread_excepthook(args):
    log.critical("Exception non gérée (thread %s)", getattr(args.thread, "name", "?"),
                 exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


sys.excepthook = _excepthook
threading.excepthook = _thread_excepthook

# ════════════════════════════════════════════════════════════════════
#  Utilitaires généraux
# ════════════════════════════════════════════════════════════════════
UA = {"User-Agent": "SAJJEEP-Uploader"}
NO_WINDOW = 0x08000000 if IS_WIN else 0


def tool_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = str(TOOLS_DIR) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_capture(args: list[str], timeout: int = 60) -> tuple[int, str]:
    p = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=tool_env(), creationflags=NO_WINDOW,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def http_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def download_file(url: str, dest: Path, cb=None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, -1
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if cb and total:
                pct = int(done * 100 / total)
                if pct != last:
                    last = pct
                    cb(done / total)
    os.replace(tmp, dest)


def parse_version(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def is_newer(latest: str, local: str) -> bool:
    return bool(latest) and bool(local) and parse_version(latest) > parse_version(local)


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**changes) -> None:
    data = load_settings()
    data.update(changes)
    tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)


def get_drive_folder_name() -> str:
    return (load_settings().get("drive_folder") or DRIVE_FOLDER_NAME).strip() or DRIVE_FOLDER_NAME


def clear_label_image(label) -> None:
    """Retire réellement l'image d'un CTkLabel (configure(image=None) la laisse parfois affichée)."""
    try:
        label.configure(image=None)
    except Exception:
        pass
    try:
        label._label.configure(image="")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════
#  yt-dlp
# ════════════════════════════════════════════════════════════════════
def ytdlp_asset() -> str:
    if IS_WIN:
        return "yt-dlp.exe"
    if IS_MAC:
        return "yt-dlp_macos"
    m = platform.machine().lower()
    return "yt-dlp_linux_aarch64" if m in ("aarch64", "arm64") else "yt-dlp_linux"


def ytdlp_version() -> str:
    if not YTDLP_PATH.exists():
        return ""
    try:
        rc, out = run_capture([str(YTDLP_PATH), "--version"], timeout=30)
        return out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else ""
    except Exception as e:
        log.warning("Lecture de la version yt-dlp impossible : %s", e)
        return ""


def latest_ytdlp_version() -> str:
    try:
        return http_json(YTDLP_API).get("tag_name", "")
    except Exception as e:
        log.warning("Dernière version yt-dlp inconnue : %s", e)
        return ""


# ════════════════════════════════════════════════════════════════════
#  Runtime JavaScript (deno)
# ════════════════════════════════════════════════════════════════════
def find_js_runtime() -> tuple[str, str] | None:
    for name in ("deno", "node", "bun"):
        local = TOOLS_DIR / (name + (".exe" if IS_WIN else ""))
        if local.exists():
            return name, str(local)
        found = shutil.which(name)
        if found:
            return name, found
    return None


def deno_asset() -> str:
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if IS_WIN:
        return "deno-x86_64-pc-windows-msvc.zip"
    if IS_MAC:
        return "deno-aarch64-apple-darwin.zip" if arm else "deno-x86_64-apple-darwin.zip"
    return "deno-aarch64-unknown-linux-gnu.zip" if arm else "deno-x86_64-unknown-linux-gnu.zip"


def install_deno(cb=None) -> None:
    zpath = TOOLS_DIR / "deno.zip"
    download_file(DENO_DL.format(asset=deno_asset()), zpath, cb)
    exe = "deno.exe" if IS_WIN else "deno"
    with zipfile.ZipFile(zpath) as z:
        for m in z.namelist():
            if Path(m).name == exe:
                tmp = TOOLS_DIR / (exe + ".new")
                with z.open(m) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if not IS_WIN:
                    tmp.chmod(0o755)
                os.replace(tmp, TOOLS_DIR / exe)
                break
    zpath.unlink(missing_ok=True)


def deno_local_exists() -> bool:
    return (TOOLS_DIR / ("deno.exe" if IS_WIN else "deno")).exists()


def deno_version() -> str:
    exe = TOOLS_DIR / ("deno.exe" if IS_WIN else "deno")
    if not exe.exists():
        return ""
    try:
        rc, out = run_capture([str(exe), "--version"], timeout=30)
        m = re.search(r"deno\s+(\d+(?:\.\d+)+)", out)
        return m.group(1) if rc == 0 and m else ""
    except Exception as e:
        log.warning("Lecture de la version deno impossible : %s", e)
        return ""


def latest_deno_version() -> str:
    try:
        return http_json(DENO_API).get("tag_name", "")
    except Exception as e:
        log.warning("Dernière version deno inconnue : %s", e)
        return ""


def deno_label() -> str:
    if deno_local_exists():
        v = deno_version()
        return f"deno {v}" if v else "deno (local)"
    js = find_js_runtime()
    if js:
        return f"{js[0]} (système)"
    return "deno absent"


# ════════════════════════════════════════════════════════════════════
#  ffmpeg (géré sous Windows ; système ailleurs)
# ════════════════════════════════════════════════════════════════════
def local_ffmpeg_exists() -> bool:
    return IS_WIN and (TOOLS_DIR / "ffmpeg.exe").exists()


def ffmpeg_available() -> bool:
    return local_ffmpeg_exists() or shutil.which("ffmpeg") is not None


def read_ffmpeg_meta() -> dict:
    try:
        return json.loads(FFMPEG_META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def local_ffmpeg_date() -> datetime | None:
    d = read_ffmpeg_meta().get("build_date")
    try:
        return parse_iso(d) if d else None
    except ValueError:
        return None


def remote_ffmpeg_date() -> datetime | None:
    try:
        for a in http_json(FFMPEG_API).get("assets", []):
            if a.get("name") == FFMPEG_ASSET:
                return parse_iso(a["updated_at"])
    except Exception as e:
        log.warning("Date du dernier build ffmpeg inconnue : %s", e)
    return None


def install_ffmpeg(remote_date: datetime | None, cb=None) -> None:
    zpath = TOOLS_DIR / "ffmpeg.zip"
    download_file(FFMPEG_URL, zpath, cb)
    wanted = {"ffmpeg.exe"}
    extracted: list[str] = []
    try:
        with zipfile.ZipFile(zpath) as z:
            for m in z.namelist():
                name = Path(m).name
                if name in wanted and "/bin/" in m.replace("\\", "/"):
                    with z.open(m) as src, open(TOOLS_DIR / (name + ".new"), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    extracted.append(name)
        if set(extracted) != wanted:
            raise RuntimeError("Archive ffmpeg inattendue (ffmpeg.exe manquant).")
        for name in extracted:
            os.replace(TOOLS_DIR / (name + ".new"), TOOLS_DIR / name)
    finally:
        for name in wanted:
            (TOOLS_DIR / (name + ".new")).unlink(missing_ok=True)
        zpath.unlink(missing_ok=True)
    date = remote_date or datetime.now(timezone.utc)
    FFMPEG_META.write_text(json.dumps({"build_date": date.isoformat()}), encoding="utf-8")


def ffmpeg_label() -> str:
    if IS_WIN and local_ffmpeg_exists():
        d = local_ffmpeg_date()
        return f"ffmpeg {d.strftime('%Y-%m-%d')}" if d else "ffmpeg (local)"
    if shutil.which("ffmpeg"):
        return "ffmpeg (système)"
    return "ffmpeg absent"


# ════════════════════════════════════════════════════════════════════
#  Google Drive
# ════════════════════════════════════════════════════════════════════
class NotConnectedError(Exception):
    pass


def validate_credentials_file(path: Path) -> dict:
    """Retourne la section 'installed' ou lève ValueError (message en français)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Fichier illisible ou ce n'est pas un JSON valide.")
    if not isinstance(data, dict):
        raise ValueError("Format de fichier non reconnu.")
    if data.get("type") == "service_account":
        raise ValueError("C'est une clé de compte de service. Il faut un ID client OAuth de type « Application de bureau ».")
    if "web" in data:
        raise ValueError("Ce fichier est de type « Application Web ». Recréez l'ID client en choisissant « Application de bureau ».")
    inst = data.get("installed")
    if not isinstance(inst, dict):
        raise ValueError("Format non reconnu : la clé « installed » est absente (type « Application de bureau » requis).")
    if not inst.get("client_id") or not inst.get("client_secret"):
        raise ValueError("Le fichier ne contient pas de client_id / client_secret.")
    return inst


def read_client_id(path: Path) -> str | None:
    try:
        return validate_credentials_file(path)["client_id"]
    except (ValueError, OSError):
        return None


def find_downloaded_credentials() -> Path | None:
    """Cherche un client_secret*.json valide dans le dossier Téléchargements."""
    folder = Path.home() / "Downloads"
    try:
        cands = sorted(folder.glob("client_secret*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for p in cands[:5]:
        try:
            validate_credentials_file(p)
            return p
        except ValueError:
            continue
    return None


INDEX_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>SAJJEEP Uploader</title></head>
<body>
<h1>SAJJEEP Uploader</h1>
<p>Application de bureau qui télécharge le son d'une vidéo, l'envoie sur le Google Drive
de l'utilisateur et génère un QR code pour l'écouter en ligne.</p>
<p><a href="privacy.html">Règles de confidentialité</a></p>
</body></html>
"""

PRIVACY_HTML = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Règles de confidentialité – SAJJEEP Uploader</title></head>
<body>
<h1>Règles de confidentialité – SAJJEEP Uploader</h1>
<p>SAJJEEP Uploader s'exécute uniquement sur l'ordinateur de l'utilisateur.</p>
<ul>
<li>L'application demande l'accès « drive.file » : elle ne peut voir et modifier que les fichiers qu'elle a elle-même créés dans votre Google Drive.</li>
<li>Aucune donnée personnelle n'est collectée, stockée sur un serveur, partagée ou vendue. Le développeur n'a accès à aucune de vos données.</li>
<li>Le jeton de connexion est enregistré uniquement sur votre ordinateur (fichier token.json). Vous pouvez le supprimer à tout moment avec le bouton « Se déconnecter », qui révoque aussi l'accès.</li>
<li>Vous pouvez aussi retirer l'accès sur https://myaccount.google.com/permissions.</li>
</ul>
<p>Contact : __EMAIL__</p>
</body></html>
"""


def valid_email(s: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s.strip()))


def site_urls(gh_user: str) -> tuple[str, str, str]:
    """(page d'accueil, page de confidentialité, domaine) du site GitHub Pages."""
    user = gh_user.strip().lower()
    if not user:
        return "", "", ""
    domain = f"{user}.github.io"
    home = f"https://{domain}/{SITE_REPO}/"
    return home, home + "privacy.html", domain


def friendly_google_error(e: BaseException) -> str:
    if isinstance(e, NotConnectedError):
        return "Aucun compte Google connecté. Ouvrez « Compte Google Drive… » pour vous connecter."
    if isinstance(e, FileNotFoundError):
        return "credentials.json est introuvable. Importez-le via « Compte Google Drive… »."
    if isinstance(e, HttpError):
        status = getattr(e.resp, "status", 0)
        text = str(e)
        if status == 403 and any(k in text for k in ("accessNotConfigured", "SERVICE_DISABLED", "has not been used")):
            return "L'API Google Drive n'est pas activée pour ce projet. Activez-la dans la Console Google Cloud."
        if status == 403 and "storageQuotaExceeded" in text:
            return "L'espace de stockage Google Drive est plein."
        if status == 401:
            return "Session Google invalide. Reconnectez-vous via « Compte Google Drive… »."
        return f"Erreur Google Drive (HTTP {status})."
    if isinstance(e, RefreshError):
        return ("La connexion Google a expiré ou a été révoquée. Reconnectez-vous. "
                "(Si cela revient tous les 7 jours, publiez l'application OAuth en « Production ».)")
    if isinstance(e, (TransportError, ConnectionError, TimeoutError)):
        return "Connexion à Google impossible. Vérifiez votre accès Internet."
    text = str(e)
    if "access_denied" in text:
        return "Autorisation refusée dans le navigateur."
    if "timed out" in text.lower():
        return "Délai dépassé : l'autorisation n'a pas été validée à temps."
    return text or e.__class__.__name__


def get_drive_service(interactive: bool = True, force: bool = False):
    creds = None
    if not force and TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as e:
            log.warning("token.json illisible : %s", e)
            creds = None

    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                with step("Rafraîchissement du jeton Google"):
                    creds.refresh(Request())
                TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            except RefreshError:
                log.warning("Jeton refusé par Google : suppression de token.json")
                TOKEN_FILE.unlink(missing_ok=True)
                creds = None
        else:
            creds = None

    if not creds or not creds.valid:
        if not interactive:
            raise NotConnectedError()
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(str(CREDENTIALS_FILE))
        with step("Autorisation Google (navigateur)"):
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(
                port=0, timeout_seconds=300, prompt="select_account consent",
                success_message="Autorisation reçue. Vous pouvez fermer cet onglet et revenir à l'application.",
            )
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_account_info(service) -> dict:
    return service.about().get(fields="user(displayName,emailAddress)").execute().get("user", {})


def disconnect_google() -> None:
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            token = data.get("refresh_token") or data.get("token")
            if token:
                body = urllib.parse.urlencode({"token": token}).encode()
                req = urllib.request.Request(
                    "https://oauth2.googleapis.com/revoke", data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded", **UA},
                )
                urllib.request.urlopen(req, timeout=15).close()
                log.info("Jeton Google révoqué")
        except Exception as e:
            log.warning("Révocation impossible (token supprimé quand même) : %s", e)
    TOKEN_FILE.unlink(missing_ok=True)


def get_or_create_folder(service, name: str) -> str:
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    res = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    return service.files().create(body=meta, fields="id").execute()["id"]


def guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    custom = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
              ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac"}
    return custom.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


# ════════════════════════════════════════════════════════════════════
#  Documents (PDF / Word) et image finale
# ════════════════════════════════════════════════════════════════════
def load_pymupdf():
    try:
        import pymupdf  # type: ignore
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # type: ignore
        return fitz
    except ImportError:
        raise RuntimeError("Le module PyMuPDF est absent : installez-le avec « pip install pymupdf ».")


def find_soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            p = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if p.exists():
                return str(p)
    if IS_MAC:
        p = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if p.exists():
            return str(p)
    return None


def _word_via_com(src: Path, pdf_path: Path) -> None:
    """Convertit en PDF avec Microsoft Word (nécessite pywin32). Lève RuntimeError."""
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        raise RuntimeError("pywin32 absent (pip install pywin32)")

    pythoncom.CoInitialize()          # obligatoire : on est dans un thread
    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")   # instance dédiée
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(src), False, True, False)  # lecture seule, hors « récents »
        doc.ExportAsFixedFormat(str(pdf_path), 17)               # 17 = PDF
    except Exception as e:
        raise RuntimeError(f"Word n'a pas pu convertir le document : {e}")
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()

    if not pdf_path.exists():
        raise RuntimeError("Word n'a produit aucun PDF")


def word_to_pdf(src: Path, out_dir: Path) -> Path:
    """Convertit un document Word en PDF : LibreOffice d'abord, puis Microsoft Word (pywin32)."""
    src = Path(src).resolve()
    out_dir = Path(out_dir).resolve()
    target = out_dir / (src.stem + ".pdf")
    reasons: list[str] = []

    soffice = find_soffice()
    if soffice:
        try:
            rc, out = run_capture(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
                timeout=180)
            log.debug("[LibreOffice] code %s : %s", rc, out.strip())
            if target.exists():
                return target
            reasons.append("LibreOffice n'a produit aucun PDF")
        except Exception as e:
            reasons.append(f"LibreOffice : {e}")
    else:
        reasons.append("LibreOffice introuvable")

    if IS_WIN:
        try:
            _word_via_com(src, target)
            return target
        except RuntimeError as e:
            reasons.append(str(e))
    else:
        reasons.append("Microsoft Word indisponible sur ce système")

    for r in reasons:
        log.warning("Conversion Word : %s", r)
    raise RuntimeError(
        "Conversion Word impossible. Installez Microsoft Word + pywin32 "
        "(pip install pywin32) ou LibreOffice (gratuit), ou exportez le document en PDF.")


def open_document(path: Path):
    """Ouvre un PDF (ou un document Word, converti) et renvoie un document PyMuPDF."""
    fitz = load_pymupdf()
    tmp: Path | None = None
    try:
        pdf_path = path
        if path.suffix.lower() != ".pdf":
            tmp = Path(tempfile.mkdtemp(prefix="sajjeep_"))
            pdf_path = word_to_pdf(path, tmp)
        data = pdf_path.read_bytes()   # lu en mémoire : aucun fichier verrouillé ensuite
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        log.warning("Ouverture PDF impossible : %s", e)
        raise ValueError("Document illisible ou format non pris en charge.")
    if getattr(doc, "needs_pass", False):
        doc.close()
        raise ValueError("Ce document est protégé par un mot de passe.")
    if doc.page_count == 0:
        doc.close()
        raise ValueError("Ce document ne contient aucune page.")
    return doc


def render_pdf_page(doc, index: int, dpi: int = RENDER_DPI) -> Image.Image:
    fitz = load_pymupdf()
    page = doc.load_page(index)
    rect = page.rect
    scale = dpi / 72
    longest = max(rect.width, rect.height) * scale
    if longest > 3200:
        scale *= 3200 / longest
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def make_qr_base(link: str) -> Image.Image:
    """QR code d'un pixel par carré, sans bordure (image « L » : 0 = noir, 255 = blanc)."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=1, border=0)
    qr.add_data(link)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("L")


def compose_image(bg: Image.Image, base: Image.Image, frac: float, cx: float, cy: float,
                  full_width: int | None = None) -> Image.Image:
    """Dessine les carrés noirs du QR sur le document (fond du QR transparent).
    frac = largeur du QR / largeur du document ; (cx, cy) = centre, en fraction de l'image.
    La taille est arrondie à un nombre entier de pixels par carré à la définition finale
    (full_width), pour que l'aperçu et l'image enregistrée soient identiques."""
    W, H = bg.size
    ref = full_width or W
    n = base.width
    cell = max(1, round(frac * ref / n))
    size = max(1, round(cell * n * W / ref))
    mask = ImageOps.invert(base).resize((size, size), Image.NEAREST)   # carrés noirs → 255
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    x = max(0, min(W - size, round(cx * W - size / 2)))
    y = max(0, min(H - size, round(cy * H - size / 2)))
    out = bg.convert("RGBA")
    out.paste(overlay, (x, y), mask)
    return out.convert("RGB")


# ── Export du résultat : PNG, PDF, Word ────────────────────────────
def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def export_png(img: Image.Image, path: Path) -> None:
    img.save(str(path), "PNG")


def export_pdf(img: Image.Image, path: Path, page_pts: tuple[float, float]) -> None:
    """PDF d'une page, à la taille d'origine de la page (en points), image sans perte."""
    fitz = load_pymupdf()
    doc = fitz.open()
    try:
        page = doc.new_page(width=page_pts[0], height=page_pts[1])
        page.insert_image(page.rect, stream=_png_bytes(img))
        doc.save(str(path), deflate=True)
    finally:
        doc.close()


def export_docx(img: Image.Image, path: Path, page_pts: tuple[float, float]) -> None:
    """Document Word d'une page : l'image occupe toute la page."""
    try:
        from docx import Document  # type: ignore
        from docx.shared import Pt  # type: ignore
    except ImportError:
        raise RuntimeError("Module manquant : installez-le avec « pip install python-docx ».")

    page_w, page_h = page_pts
    d = Document()
    s = d.sections[0]
    s.page_width, s.page_height = Pt(page_w), Pt(page_h)
    s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = Pt(0)
    s.header_distance = s.footer_distance = Pt(0)

    p = d.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = pf.space_after = Pt(0)
    pf.line_spacing = 1.0
    # 98,5 % de la page : évite qu'une page blanche s'ajoute à la fin
    p.add_run().add_picture(io.BytesIO(_png_bytes(img)), width=Pt(page_w * 0.985))
    d.save(str(path))


# ════════════════════════════════════════════════════════════════════
#  Boîte de dialogue de confirmation / information
# ════════════════════════════════════════════════════════════════════
class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, master, title: str, message: str, yes: str = "Oui",
                 no: str | None = "Non", on_result=None):
        super().__init__(master)
        self._cb = on_result
        self._done = False
        self.title(title)
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())

        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=FG).pack(padx=24, pady=(20, 6), anchor="w")
        ctk.CTkLabel(self, text=message, text_color=MUTED, wraplength=380,
                     justify="left").pack(padx=24, pady=(0, 16), anchor="w")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=24, pady=(0, 20), anchor="e")
        if no:
            ctk.CTkButton(row, text=no, width=110, fg_color=BTN, hover_color=BTN_HOVER,
                          text_color=FG, command=lambda: self._finish(False)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text=yes, width=110, fg_color=FG, hover_color="#cfcfcf",
                      text_color=BG, command=lambda: self._finish(True)).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", lambda: self._finish(False))

        self.update_idletasks()
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        mw, mh = master.winfo_width(), master.winfo_height()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{mx + (mw - w) // 2}+{my + (mh - h) // 2}")
        self.after(120, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except Exception:
            pass

    def _finish(self, value: bool):
        if self._done:
            return
        self._done = True
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self._cb:
            self._cb(value)


# ════════════════════════════════════════════════════════════════════
#  Popup « Compte Google Drive… »
# ════════════════════════════════════════════════════════════════════
TOTAL_STEPS = 8
LAST_MANUAL = 6      # étapes 1 à 6 : validées par « C'est fait »
STEP_IMPORT = 7
STEP_CONNECT = 8

STEP_TITLES = {
    1: "Créer un projet Google Cloud",
    2: "Activer l'API Google Drive",
    3: "Créer un petit site web gratuit (GitHub)",
    4: "Renseigner l'identité de l'application",
    5: "Publier l'application",
    6: "Créer l'identifiant et le télécharger",
    7: "Importer le fichier téléchargé",
    8: "Connecter votre compte Google",
}

# Étapes « simples » : texte + boutons qui ouvrent la bonne page
SETUP_STEPS = {
    1: ("Un « projet » est l'espace Google où sera rangée la configuration de l'application.\n\n"
        "Dans la page qui s'ouvre : saisissez un nom (par exemple SAJJEEP-Uploader), puis cliquez "
        "sur « Créer ». Connectez-vous à Google si on vous le demande.",
        [("Ouvrir la page « Nouveau projet »", CLOUD_PROJECT_URL)]),
    2: ("Cela autorise l'application à parler à Google Drive.\n\n"
        "Dans la page qui s'ouvre, vérifiez que votre projet est bien sélectionné en haut de la page, "
        "puis cliquez sur le bouton bleu « Activer ». S'il affiche « Gérer », c'est déjà fait.",
        [("Ouvrir la page de l'API Drive", DRIVE_API_URL)]),
    5: ("IMPORTANT : sans cette étape, la connexion s'arrêterait de fonctionner au bout de 7 jours.\n\n"
        "Dans la page qui s'ouvre, sous « État de la publication », cliquez sur « Publier "
        "l'application », puis « Confirmer ». Le statut doit devenir « En production ».\n\n"
        "Si le bouton est grisé, passez la souris dessus : Google indique ce qui manque. Revenez "
        "alors à l'étape 4 (adresses du site et domaine autorisé) et cliquez sur « Enregistrer ».\n\n"
        "Solution de secours : laisser l'état sur « Test », descendre jusqu'à « Utilisateurs tests », "
        "cliquer sur « Add users » et saisir votre adresse Gmail. Dans ce cas, la connexion expire "
        "après 7 jours : il suffira de cliquer à nouveau sur « Se connecter ».",
        [("Ouvrir la page « Audience »", CLOUD_AUDIENCE_URL)]),
    6: ("C'est le fichier qui identifie votre application auprès de Google.\n\n"
        "Dans la page qui s'ouvre :\n"
        "•  Type d'application : « Application de bureau » (surtout pas « Web »)\n"
        "•  Nom : ce que vous voulez\n"
        "•  Cliquez sur « Créer », puis sur « Télécharger le JSON ».\n"
        "Le fichier arrive dans votre dossier Téléchargements.",
        [("Ouvrir « Créer un client »", CLOUD_CLIENT_URL)]),
}

BRANDING_TEXT = (
    "Dans la page qui s'ouvre :\n"
    "•  Si Google propose « Commencer », cliquez dessus. Choisissez l'audience « Externe » et "
    "acceptez les règles.\n"
    "•  Remplissez les champs avec les valeurs ci-dessous (bouton « Copier » puis Ctrl+V).\n"
    "•  Laissez VIDES : le logo et les conditions d'utilisation.\n"
    "•  Domaines autorisés : cliquez sur « Ajouter un domaine » et collez le domaine. Le message "
    "« Domaine manquant » doit disparaître.\n"
    "•  Cliquez sur « Enregistrer » en bas de la page."
)

VALUE_ROWS = [
    ("name", "Nom de l'application"),
    ("support", "E-mail d'assistance utilisateur"),
    ("home", "Page d'accueil de l'application"),
    ("privacy", "Lien vers les règles de confidentialité"),
    ("domain", "Domaine autorisé"),
    ("dev", "Coordonnées du développeur (e-mail)"),
]


class GoogleAccountDialog(ctk.CTkToplevel):
    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("Compte Google Drive")
        self.geometry("640x800")
        self.minsize(600, 620)
        self.configure(fg_color=BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._steps: dict[int, dict] = {}
        self._connect_btns: list = []
        self._value_entries: dict[str, ctk.CTkEntry] = {}
        self._values: dict[str, str] = {}
        self._connected: bool | None = None
        self._check_error = ""
        self._last_error = ""
        self._account: dict = {}
        self._review = False
        self._open_step: int | None = None
        self._cid: str | None = None
        self._creds_exists = False
        self._has_token = False
        self._found: Path | None = find_downloaded_credentials()

        self._build()
        self.refresh()
        self.after(150, self._focus)
        self.after(2000, self._watch_downloads)

    # ── utilitaires ────────────────────────────────────────────────
    def _focus(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _close(self):
        self.app._google_dialog = None
        self.destroy()

    def _msg(self, text: str, error: bool = False):
        log.info("[UI] Popup Google : %s", text)
        if self.alive():
            self.lbl_msg.configure(text=text, text_color=ERR_COLOR if error else OK_COLOR)

    def _btn(self, parent, text, command, primary=False, **kw):
        return ctk.CTkButton(
            parent, text=text, command=command,
            fg_color=FG if primary else BTN, hover_color="#cfcfcf" if primary else BTN_HOVER,
            text_color=BG if primary else FG, **kw)

    def _card(self, parent, title: str, pack: bool = True) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=10)
        if pack:
            card.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=FG).pack(anchor="w", padx=16, pady=(12, 6))
        return card

    def _text(self, parent, text: str, color: str = MUTED, pady=(0, 10)):
        lbl = ctk.CTkLabel(parent, text=text, text_color=color, wraplength=500,
                           justify="left", anchor="w")
        lbl.pack(fill="x", padx=14, pady=pady)
        return lbl

    def _sub(self, parent, text: str, buttons=()):
        """Un paragraphe suivi éventuellement de boutons (label, commande)."""
        self._text(parent, text, FG, pady=(0, 6))
        if buttons:
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=(0, 12))
            for label, cmd in buttons:
                self._btn(r, label, cmd).pack(side="left", padx=(0, 8))

    def _nav_row(self, body, n: int, url_buttons=(), done_label="✔ C'est fait, étape suivante"):
        r = ctk.CTkFrame(body, fg_color="transparent")
        r.pack(fill="x", padx=14, pady=(0, 12))
        for label, url in url_buttons:
            self._btn(r, label, lambda u=url: self._open_url(u), primary=True).pack(
                side="left", padx=(0, 8))
        self._btn(r, done_label, lambda k=n: self._mark_done(k)).pack(side="left")

    def _open_url(self, url: str):
        log.info("[UI] Ouverture : %s", url)
        webbrowser.open(url)

    def _copy(self, text: str, what: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self._msg(f"{what} copié. Collez-le avec Ctrl+V.")

    # ── construction ───────────────────────────────────────────────
    def _build(self):
        ctk.CTkLabel(self, text="Compte Google Drive", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=FG).pack(anchor="w", padx=22, pady=(18, 2))
        ctk.CTkLabel(self, text="Permet à l'application d'envoyer vos fichiers sur VOTRE Google Drive.",
                     text_color=MUTED, wraplength=580, justify="left").pack(anchor="w", padx=22, pady=(0, 10))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=22, pady=(4, 14))
        self.lbl_msg = ctk.CTkLabel(bottom, text="", text_color=OK_COLOR, wraplength=580,
                                    justify="left", anchor="w")
        self.lbl_msg.pack(fill="x", pady=(0, 8))
        self._btn(bottom, "Fermer", self._close, width=120).pack(anchor="e")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=10)
        self.top_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.top_frame.pack(fill="x")

        # ── Mode « prêt » ──
        self.ready_card = self._card(self.top_frame, "Compte connecté", pack=False)
        self.lbl_state = ctk.CTkLabel(self.ready_card, text="", text_color=FG, anchor="w")
        self.lbl_state.pack(fill="x", padx=16)
        self.lbl_email = ctk.CTkLabel(self.ready_card, text="", text_color=MUTED, anchor="w")
        self.lbl_email.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(self.ready_card, text="Les fichiers envoyés sont accessibles à toute personne "
                     "qui possède le lien.", text_color=MUTED, wraplength=500, justify="left",
                     anchor="w").pack(fill="x", padx=16, pady=(0, 10))
        row = ctk.CTkFrame(self.ready_card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 8))
        btn_change = self._btn(row, "Changer de compte", self._connect, primary=True, width=170)
        btn_change.pack(side="left", padx=(0, 8))
        self._connect_btns.append(btn_change)
        self.btn_disconnect = self._btn(row, "Se déconnecter", self._disconnect, width=150)
        self.btn_disconnect.pack(side="left")
        self._btn(self.ready_card, "Revoir la configuration Google Cloud",
                  lambda: self._toggle_review(True), width=260).pack(anchor="w", padx=16, pady=(0, 14))

        # ── Mode « configuration » ──
        self.setup_frame = ctk.CTkFrame(self.top_frame, fg_color="transparent")
        self.lbl_progress = ctk.CTkLabel(self.setup_frame, text="", text_color=FG, wraplength=560,
                                         justify="left", anchor="w", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_progress.pack(fill="x", padx=4, pady=(0, 2))
        ctk.CTkLabel(self.setup_frame,
                     text="Suivez les étapes dans l'ordre. Cliquez sur le titre d'une étape pour la "
                          "déplier. Chaque étape ouvre la bonne page : faites ce qui est indiqué, "
                          "puis revenez ici.",
                     text_color=MUTED, wraplength=560, justify="left", anchor="w").pack(
            fill="x", padx=4, pady=(0, 10))
        self.btn_hide = self._btn(self.setup_frame, "Masquer les étapes",
                                  lambda: self._toggle_review(False), width=170)

        # Étapes simples : 1, 2, (3 et 4 spécifiques), 5, 6
        for n in (1, 2):
            self._build_simple(n)
        self._build_site_step()
        self._build_branding_step()
        for n in (5, 6):
            self._build_simple(n)
        self._build_import_step()
        self._build_connect_step()

        # ── Dossier Drive (toujours visible) ──
        c = self._card(self.scroll, "Dossier Drive")
        ctk.CTkLabel(c, text="Nom du dossier où seront rangés les fichiers. Il est créé "
                             "automatiquement s'il n'existe pas.",
                     text_color=MUTED, wraplength=500, justify="left", anchor="w").pack(
            fill="x", padx=16)
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(6, 14))
        self.entry_folder = ctk.CTkEntry(row, fg_color=BG, border_color=BORDER, text_color=FG)
        self.entry_folder.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entry_folder.insert(0, get_drive_folder_name())
        self._btn(row, "Enregistrer", self._save_folder, width=110).pack(side="right")

        self._refresh_values()

    def _make_step(self, n: int) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.setup_frame, fg_color=PANEL, border_color=BORDER,
                            border_width=1, corner_radius=10)
        card.pack(fill="x", pady=(0, 8))
        head = ctk.CTkButton(card, text="", anchor="w", fg_color="transparent", hover_color=BTN,
                             text_color=FG, font=ctk.CTkFont(size=14, weight="bold"),
                             command=lambda: self._expand(n))
        head.pack(fill="x", padx=6, pady=6)
        body = ctk.CTkFrame(card, fg_color="transparent")
        self._steps[n] = {"card": card, "head": head, "body": body}
        return body

    def _build_simple(self, n: int):
        body = self._make_step(n)
        text, buttons = SETUP_STEPS[n]
        self._text(body, text)
        self._nav_row(body, n, buttons)

    def _build_site_step(self):
        body = self._make_step(3)
        self._text(body,
                   "Pour publier l'application, Google exige une page d'accueil et une page « règles de "
                   "confidentialité » visibles sur Internet. On les crée gratuitement avec GitHub. Rien à "
                   "installer : tout se fait dans le navigateur (environ 5 minutes).")
        self._sub(body, "A.  Créez un compte GitHub gratuit (ou connectez-vous si vous en avez déjà un).",
                  [("Ouvrir GitHub", lambda: self._open_url(GITHUB_SIGNUP_URL))])
        self._sub(body, f"B.  Créez un dépôt : nom « {SITE_REPO} » (exactement), cochez « Public », "
                        "puis cliquez sur « Create repository ».",
                  [("Créer le dépôt", lambda: self._open_url(GITHUB_NEW_REPO_URL))])

        box = ctk.CTkFrame(body, fg_color=BG, border_color=BORDER, border_width=1, corner_radius=8)
        box.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(box, text="Renseignez ces deux informations : elles servent à préparer le contenu "
                               "des pages et les adresses de l'étape suivante.",
                     text_color=MUTED, wraplength=470, justify="left", anchor="w").pack(
            fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(box, text="Votre nom d'utilisateur GitHub (en haut à droite sur GitHub)",
                     text_color=FG, anchor="w").pack(fill="x", padx=12)
        st = load_settings()
        self.entry_gh = ctk.CTkEntry(box, fg_color=PANEL, border_color=BORDER, text_color=FG)
        self.entry_gh.pack(fill="x", padx=12, pady=(2, 8))
        self.entry_gh.insert(0, st.get("gh_user", ""))
        ctk.CTkLabel(box, text="Votre adresse e-mail (celle de votre compte Google)",
                     text_color=FG, anchor="w").pack(fill="x", padx=12)
        self.entry_mail = ctk.CTkEntry(box, fg_color=PANEL, border_color=BORDER, text_color=FG)
        self.entry_mail.pack(fill="x", padx=12, pady=(2, 12))
        self.entry_mail.insert(0, st.get("contact_email", ""))
        self.entry_gh.bind("<KeyRelease>", self._on_inputs)
        self.entry_mail.bind("<KeyRelease>", self._on_inputs)

        self._sub(body, "C.  Dans votre dépôt, cliquez sur « Add file » puis « Create new file ». "
                        "Nom du fichier : index.html. Cliquez ici sur « Copier », collez le contenu dans "
                        "GitHub (Ctrl+V), puis cliquez sur « Commit changes » et confirmez.",
                  [("Copier le contenu de index.html", lambda: self._copy_html("index"))])
        self._sub(body, "D.  Recommencez avec un second fichier nommé privacy.html.",
                  [("Copier le contenu de privacy.html", lambda: self._copy_html("privacy"))])
        self._sub(body, "E.  Dans GitHub, allez dans « Settings » puis « Pages ». Sous « Branch », choisissez "
                        "« main » et « / (root) », puis cliquez sur « Save ».",
                  [("Ouvrir les réglages Pages", lambda: self._open_site("settings"))])
        self._sub(body, "F.  Attendez 1 à 2 minutes, puis vérifiez que les deux pages s'affichent "
                        "(pas d'erreur 404). Rechargez si besoin.",
                  [("Voir l'accueil", lambda: self._open_site("home")),
                   ("Voir la confidentialité", lambda: self._open_site("privacy"))])
        self._text(body, "Ne déposez jamais credentials.json ni token.json dans ce dépôt public.",
                   MUTED)
        self._nav_row(body, 3, done_label="✔ Les deux pages s'affichent, étape suivante")

    def _build_branding_step(self):
        body = self._make_step(4)
        self._text(body, BRANDING_TEXT)
        for key, label in VALUE_ROWS:
            ctk.CTkLabel(body, text=label, text_color=FG, anchor="w").pack(fill="x", padx=14)
            r = ctk.CTkFrame(body, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=(2, 8))
            e = ctk.CTkEntry(r, fg_color=BG, border_color=BORDER, text_color=FG)
            e.pack(side="left", fill="x", expand=True, padx=(0, 8))
            self._value_entries[key] = e
            self._btn(r, "Copier", lambda k=key, l=label: self._copy_value(k, l), width=80).pack(side="right")
        self._nav_row(body, 4, [("Ouvrir la page « Branding »", CLOUD_BRANDING_URL)])

    def _build_import_step(self):
        body = self._make_step(STEP_IMPORT)
        self._text(body, "L'application cherche toute seule le fichier téléchargé dans votre dossier "
                         "Téléchargements. S'il n'apparaît pas ci-dessous, cliquez sur « Choisir le fichier… ».")
        self.lbl_creds = self._text(body, "", MUTED)
        self.found_frame = ctk.CTkFrame(body, fg_color=BG, border_color=BORDER, border_width=1,
                                        corner_radius=8)
        self.lbl_found = ctk.CTkLabel(self.found_frame, text="", text_color=FG, wraplength=470,
                                      justify="left", anchor="w")
        self.lbl_found.pack(fill="x", padx=12, pady=(10, 6))
        self._btn(self.found_frame, "Utiliser ce fichier", self._use_found, primary=True).pack(
            anchor="w", padx=12, pady=(0, 10))
        self.row_import = ctk.CTkFrame(body, fg_color="transparent")
        self.row_import.pack(fill="x", padx=14, pady=(0, 12))
        self._btn(self.row_import, "Choisir le fichier…", self._import, width=170).pack(side="left")

    def _build_connect_step(self):
        body = self._make_step(STEP_CONNECT)
        self._text(body,
                   "Cliquez sur « Se connecter » : votre navigateur s'ouvre. Choisissez votre compte Google.\n\n"
                   "Google peut afficher « Google n'a pas validé cette appli ». C'est normal, "
                   "l'application est la vôtre. Cliquez sur « Paramètres avancés », puis sur "
                   f"« Accéder à {APP_NAME} (non sécurisé) », puis « Continuer ».\n\n"
                   "Revenez ensuite ici : la connexion se termine toute seule.")
        self.lbl_connect_err = ctk.CTkLabel(body, text="", text_color=ERR_COLOR, wraplength=500,
                                            justify="left", anchor="w")
        self.lbl_connect_err.pack(fill="x", padx=14, pady=(0, 6))
        r = ctk.CTkFrame(body, fg_color="transparent")
        r.pack(fill="x", padx=14, pady=(0, 12))
        b = self._btn(r, "Se connecter", self._connect, primary=True, width=170)
        b.pack(side="left")
        self._connect_btns.append(b)

    # ── données saisies (étapes 3 et 4) ────────────────────────────
    def _on_inputs(self, _e=None):
        try:
            save_settings(gh_user=self.entry_gh.get().strip(),
                          contact_email=self.entry_mail.get().strip())
        except OSError as e:
            log.warning("Enregistrement des saisies impossible : %s", e)
        self._refresh_values()

    def _refresh_values(self):
        home, priv, domain = site_urls(self.entry_gh.get())
        email = self.entry_mail.get().strip()
        email = email if valid_email(email) else ""
        self._values = {"name": APP_NAME, "support": email, "home": home,
                        "privacy": priv, "domain": domain, "dev": email}
        for key, entry in self._value_entries.items():
            text = self._values[key] or "— à renseigner à l'étape 3 —"
            entry.configure(state="normal")
            entry.delete(0, "end")
            entry.insert(0, text)
            entry.configure(state="readonly")

    def _copy_value(self, key: str, label: str):
        value = self._values.get(key, "")
        if not value:
            self._msg("Renseignez d'abord votre nom d'utilisateur GitHub et votre e-mail (étape 3).",
                      error=True)
            return
        self._copy(value, label)

    def _copy_html(self, kind: str):
        if kind == "index":
            self._copy(INDEX_HTML, "Contenu de index.html")
            return
        email = self.entry_mail.get().strip()
        if not valid_email(email):
            self._msg("Saisissez d'abord une adresse e-mail valide (cadre au-dessus).", error=True)
            return
        self._copy(PRIVACY_HTML.replace("__EMAIL__", email), "Contenu de privacy.html")

    def _open_site(self, kind: str):
        user = self.entry_gh.get().strip()
        if not user:
            self._msg("Saisissez d'abord votre nom d'utilisateur GitHub (cadre au-dessus).", error=True)
            return
        home, priv, _ = site_urls(user)
        urls = {"home": home, "privacy": priv,
                "settings": f"https://github.com/{user}/{SITE_REPO}/settings/pages"}
        self._open_url(urls[kind])

    # ── état ───────────────────────────────────────────────────────
    def refresh(self):
        if not self.alive():
            return
        self._creds_exists = CREDENTIALS_FILE.exists()
        self._cid = read_client_id(CREDENTIALS_FILE) if self._creds_exists else None
        self._has_token = TOKEN_FILE.exists()
        self._check_error = ""
        if self._cid and self._has_token:
            self._connected = None
            self._check_account()
        else:
            self._connected = False
        self._open_step = self._current()
        self._render()

    def _done_steps(self) -> dict[int, bool]:
        saved = set(load_settings().get("setup_done", []))
        before_import = bool(self._cid or self._found)
        d = {n: (n in saved or before_import) for n in range(1, LAST_MANUAL + 1)}
        d[STEP_IMPORT] = bool(self._cid)
        d[STEP_CONNECT] = bool(self._cid and self._has_token and self._connected)
        return d

    def _current(self) -> int | None:
        d = self._done_steps()
        return next((n for n in range(1, TOTAL_STEPS + 1) if not d[n]), None)

    def _render(self):
        if not self.alive():
            return
        done = self._done_steps()
        current = next((n for n in range(1, TOTAL_STEPS + 1) if not done[n]), None)
        ready = bool(self._cid and self._has_token and self._connected is not False)

        if ready and not self._review:
            self.setup_frame.pack_forget()
            self.ready_card.pack(fill="x", pady=(0, 12))
            if self._connected:
                self.lbl_state.configure(text="✔ Connecté", text_color=OK_COLOR)
                name = self._account.get("displayName") or ""
                mail = self._account.get("emailAddress") or ""
                self.lbl_email.configure(text=f"{name} · {mail}" if name else mail)
            elif self._check_error:
                self.lbl_state.configure(text="Connexion à vérifier", text_color=ERR_COLOR)
                self.lbl_email.configure(text=self._check_error)
            else:
                self.lbl_state.configure(text="Vérification de la connexion…", text_color=MUTED)
                self.lbl_email.configure(text="")
            self.btn_disconnect.configure(state="normal" if self._has_token else "disabled")
        else:
            self.ready_card.pack_forget()
            self.setup_frame.pack(fill="x")

        if current is None:
            self.lbl_progress.configure(text="✔ Configuration terminée.")
        else:
            self.lbl_progress.configure(
                text=f"Configuration à faire une seule fois (environ 15 minutes) : "
                     f"étape {current} sur {TOTAL_STEPS}.")
        if ready:
            self.btn_hide.pack(anchor="w", pady=(0, 8), before=self._steps[1]["card"])
        else:
            self.btn_hide.pack_forget()

        for n in range(1, TOTAL_STEPS + 1):
            s = self._steps[n]
            mark = "✔" if done[n] else str(n)
            color = OK_COLOR if done[n] else (FG if n == current else MUTED)
            s["head"].configure(text=f"  {mark}    {STEP_TITLES[n]}", text_color=color)
            s["card"].configure(border_color=FG if n == current else BORDER)
            if n == self._open_step:
                s["body"].pack(fill="x", pady=(0, 6))
            else:
                s["body"].pack_forget()

        # Étape 7 : import
        if self._cid:
            self.lbl_creds.configure(text=f"✔ Fichier valide (client {self._cid[:14]}…)", text_color=OK_COLOR)
        elif self._creds_exists:
            self.lbl_creds.configure(text="✘ Le fichier credentials.json actuel est invalide : "
                                          "importez-en un autre.", text_color=ERR_COLOR)
        else:
            self.lbl_creds.configure(text="Aucun fichier importé pour l'instant.", text_color=MUTED)
        if self._found and not self._cid:
            self.lbl_found.configure(text=f"Fichier trouvé dans Téléchargements :\n{self._found.name}")
            self.found_frame.pack(fill="x", padx=14, pady=(0, 10), before=self.row_import)
        else:
            self.found_frame.pack_forget()

        # Étape 8 : connexion
        self.lbl_connect_err.configure(text=self._last_error)
        for b in self._connect_btns:
            b.configure(state="normal" if self._cid else "disabled")

    def _expand(self, n: int):
        self._open_step = None if self._open_step == n else n
        self._render()

    def _toggle_review(self, flag: bool):
        self._review = flag
        self._open_step = None
        self._render()

    def _mark_done(self, n: int):
        saved = set(load_settings().get("setup_done", []))
        saved.update(range(1, n + 1))
        try:
            save_settings(setup_done=sorted(saved))
        except OSError as e:
            log.warning("Enregistrement de la progression impossible : %s", e)
        self._open_step = n + 1
        self._render()

    def _watch_downloads(self):
        if not self.alive():
            return
        try:
            if not self._cid:
                found = find_downloaded_credentials()
                if found != self._found:
                    self._found = found
                    if found:
                        log.info("[UI] Fichier détecté dans Téléchargements : %s", found)
                        self._open_step = STEP_IMPORT
                    self._render()
        except Exception:
            log.exception("Surveillance du dossier Téléchargements")
        self.after(2000, self._watch_downloads)

    def _check_account(self):
        def work():
            try:
                info = get_account_info(get_drive_service(interactive=False))
                res = (info, None)
            except NotConnectedError:
                res = (None, "nc")
            except Exception as e:
                log.warning("Vérification du compte : %s", e)
                res = (None, friendly_google_error(e))
            self.app.ui(self._show_account, *res)
        threading.Thread(target=work, daemon=True).start()

    def _show_account(self, info, err):
        if not self.alive():
            return
        self._has_token = TOKEN_FILE.exists()
        if err == "nc":
            self._connected = False
            self._last_error = ("Votre connexion Google a expiré ou n'existe plus : cliquez sur "
                                "« Se connecter » pour la renouveler.")
        elif err:
            self._connected = None
            self._check_error = err
        else:
            self._connected = True
            self._account = info or {}
            self._last_error = ""
        self._open_step = self._current()
        self._render()

    # ── actions ────────────────────────────────────────────────────
    def _run_bg(self, work, done):
        self.app.set_busy(True)

        def runner():
            try:
                res, err = work(), None
            except Exception as e:
                log.exception("Erreur dans la popup Google")
                res, err = None, e

            def fin():
                self.app.set_busy(False)
                if self.alive():
                    done(res, err)
            self.app.ui(fin)

        threading.Thread(target=runner, daemon=True).start()

    def _connect(self):
        log.info("[UI] Action : Se connecter / Changer de compte")
        if self.app.busy:
            self._msg("Une opération est en cours, réessayez dans un instant.", error=True)
            return
        if not CREDENTIALS_FILE.exists():
            self._msg("Importez d'abord le fichier téléchargé (étape 7).", error=True)
            return
        self._msg("Une page s'ouvre dans votre navigateur : choisissez votre compte puis autorisez "
                  "l'accès (5 minutes maximum)…")
        for b in self._connect_btns:
            b.configure(state="disabled")

        def work():
            return get_account_info(get_drive_service(interactive=True, force=True))

        def done(info, err):
            if err:
                self._last_error = friendly_google_error(err)
                self._msg(self._last_error, error=True)
            else:
                self._last_error = ""
                self._msg("Connexion réussie.")
            self._review = False
            self.refresh()

        self._run_bg(work, done)

    def _disconnect(self):
        log.info("[UI] Action : Se déconnecter")
        ConfirmDialog(
            self, "Se déconnecter",
            "L'accès de l'application à votre Google Drive sera révoqué et le jeton local supprimé. "
            "Les fichiers déjà envoyés restent sur votre Drive. Continuer ?",
            yes="Se déconnecter", no="Annuler",
            on_result=lambda ok: ok and self._do_disconnect())

    def _do_disconnect(self):
        if self.app.busy:
            self._msg("Une opération est en cours, réessayez dans un instant.", error=True)
            return

        def done(_res, err):
            self._msg("Déconnecté." if not err else friendly_google_error(err), error=bool(err))
            self._account = {}
            self._last_error = ""
            self.refresh()
        self._run_bg(disconnect_google, done)

    def _use_found(self):
        if self._found:
            self._import_from(self._found)

    def _import(self):
        log.info("[UI] Action : Choisir le fichier credentials")
        p = filedialog.askopenfilename(parent=self, title="Choisir le fichier téléchargé depuis Google",
                                       filetypes=[("Fichier JSON", "*.json"), ("Tous les fichiers", "*.*")])
        if p:
            self._import_from(Path(p))

    def _import_from(self, src: Path):
        log.info("[UI] Import de %s", src)
        try:
            validate_credentials_file(src)
        except ValueError as e:
            self._msg(str(e), error=True)
            return
        old = read_client_id(CREDENTIALS_FILE) if CREDENTIALS_FILE.exists() else None
        try:
            if not (CREDENTIALS_FILE.exists() and src.resolve() == CREDENTIALS_FILE.resolve()):
                shutil.copyfile(src, CREDENTIALS_FILE)
        except OSError as e:
            self._msg(f"Copie impossible : {e}", error=True)
            return
        new = read_client_id(CREDENTIALS_FILE)
        if old and new and old != new and TOKEN_FILE.exists():
            TOKEN_FILE.unlink(missing_ok=True)
            self._msg("Fichier importé. L'identifiant a changé : reconnectez-vous (étape 8).")
        else:
            self._msg("Fichier importé. Dernière étape : connecter votre compte. "
                      "Vous pouvez supprimer l'original de votre dossier Téléchargements.")
        self.refresh()

    def _save_folder(self):
        name = self.entry_folder.get().strip()
        if not name:
            self._msg("Le nom du dossier ne peut pas être vide.", error=True)
            return
        try:
            save_settings(drive_folder=name)
        except OSError as e:
            self._msg(f"Enregistrement impossible : {e}", error=True)
            return
        self._msg(f"Enregistré. Les prochains envois iront dans « {name} ».")


# ════════════════════════════════════════════════════════════════════
#  Application principale
# ════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self._log_ids = itertools.count(1)   # identifiants des lignes du journal
        ctk.set_appearance_mode("dark")
        self.title("SAJJEEP Uploader")
        self.geometry("1380x800")
        self.minsize(1280, 720)
        self.configure(fg_color=BG)

        self.busy = False
        self.ytdlp_verified = False
        self._q: queue.Queue = queue.Queue()
        self._google_dialog: GoogleAccountDialog | None = None
        self._qr_pil: Image.Image | None = None
        self._qr_img = None
        self._link = ""

        # Panneaux 3 et 4 : document et image finale
        self._qr_base: Image.Image | None = None     # QR « 1 pixel par carré » du lien courant
        self._pdf = None                             # document PyMuPDF ouvert
        self._doc_name = ""
        self._page = 0
        self._bg_full: Image.Image | None = None     # page rendue en pleine définition
        self._bg_prev: Image.Image | None = None     # même page, réduite pour l'aperçu
        self._prev_img = None
        self._qr_frac = 0.20                         # largeur du QR / largeur du document
        self._qr_cx = 0.80                           # centre du QR (fraction de la largeur)
        self._qr_cy = 0.80                           # centre du QR (fraction de la hauteur)
        self._doc_loading = False

        st = load_settings()
        self.opt_delete = bool(st.get("delete_after", True))
        self.opt_auto = bool(st.get("auto_update", True))

        TOOLS_DIR.mkdir(exist_ok=True)
        DOWNLOAD_DIR.mkdir(exist_ok=True)

        self._build_ui()
        self.after(50, self._poll)
        log.info("Démarrage – dossier : %s", BASE_DIR)
        log.info("credentials.json : %s | token.json : %s | ffmpeg local : %s",
                 "présent" if CREDENTIALS_FILE.exists() else "ABSENT",
                 "présent" if TOKEN_FILE.exists() else "ABSENT",
                 "présent" if local_ffmpeg_exists() else "ABSENT")
        self._run_task(self._startup_job)

    # ── infrastructure thread-safe ─────────────────────────────────
    def ui(self, fn, *a, **k):
        self._q.put((fn, a, k))

    def _poll(self):
        try:
            while True:
                fn, a, k = self._q.get_nowait()
                try:
                    fn(*a, **k)
                except Exception:
                    log.exception("Erreur dans une action d'interface")
        except queue.Empty:
            pass
        self.after(50, self._poll)

    def report_callback_exception(self, exc, val, tb):
        log.critical("Exception Tk", exc_info=(exc, val, tb))

    def ask(self, title: str, message: str, yes: str = "Oui", no: str | None = "Non") -> bool:
        """Dialogue bloquant, à appeler depuis un thread de travail."""
        ev = threading.Event()
        res = {"v": False}

        def done(v):
            res["v"] = v
            ev.set()

        self.ui(lambda: ConfirmDialog(self, title, message, yes, no, on_result=done))
        ev.wait()
        return res["v"]

    def info(self, title: str, message: str):
        self.ask(title, message, yes="OK", no=None)

    def _run_task(self, fn, *args):
        if self.busy:
            self.log("Une opération est déjà en cours.")
            return
        self.set_busy(True)

        def runner():
            try:
                fn(*args)
            except Exception as e:
                log.exception("Tâche en échec")
                self.log(f"Erreur : {friendly_google_error(e)}")
            finally:
                self.ui(self.set_busy, False)
        threading.Thread(target=runner, daemon=True).start()

    # ── interface ──────────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=4)
        self.grid_columnconfigure(1, weight=3)
        self.grid_columnconfigure(2, weight=3)
        self.grid_rowconfigure(0, weight=1)

        def btn(parent, text, cmd, primary=False, **kw):
            return ctk.CTkButton(
                parent, text=text, command=cmd,
                fg_color=FG if primary else BTN, hover_color="#cfcfcf" if primary else BTN_HOVER,
                text_color=BG if primary else FG, **kw)
        self._mkbtn = btn

        def panel(parent):
            return ctk.CTkFrame(parent, fg_color=PANEL, border_color=BORDER, border_width=1,
                                corner_radius=12)

        # ── Panneau 1 : téléchargement et envoi ──
        left = panel(self)
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=(16, 8))
        left.grid_rowconfigure(10, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="SAJJEEP Uploader", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=FG).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(left, text="Son YouTube → Google Drive → QR code → Paroles", text_color=MUTED).grid(
            row=1, column=0, sticky="w", padx=20, pady=(0, 14))

        ctk.CTkLabel(left, text="Lien YouTube", text_color=MUTED).grid(row=2, column=0, sticky="w", padx=20)
        self.url_entry = ctk.CTkEntry(left, placeholder_text="https://www.youtube.com/watch?v=…",
                                      fg_color=BG, border_color=BORDER, text_color=FG, height=36)
        self.url_entry.grid(row=3, column=0, sticky="ew", padx=20, pady=(4, 10))
        self.url_entry.bind("<Return>", lambda e: self.start())

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 10))
        ctk.CTkLabel(row, text="Format", text_color=MUTED).pack(side="left", padx=(0, 10))
        self.fmt_var = ctk.StringVar(value=load_settings().get("format", "m4a"))
        self.seg_fmt = ctk.CTkSegmentedButton(
            row, values=["m4a", "mp3"], variable=self.fmt_var, fg_color=BG,
            selected_color=FG, selected_hover_color="#cfcfcf", unselected_color=BTN,
            unselected_hover_color=BTN_HOVER, text_color=FG,
            command=lambda v: save_settings(format=v))
        self.seg_fmt.pack(side="left")
        self.btn_local = btn(row, "Fichier audio local…", self.start_local, width=170)
        self.btn_local.pack(side="right")

        self.chk_delete = ctk.CTkCheckBox(
            left, text="Supprimer le fichier téléchargé après l'envoi", text_color=FG,
            fg_color=FG, hover_color="#cfcfcf", checkmark_color=BG, border_color=MUTED,
            command=self._on_options)
        self.chk_delete.grid(row=5, column=0, sticky="w", padx=20, pady=(0, 6))
        self.chk_auto = ctk.CTkCheckBox(
            left, text="Mettre à jour yt-dlp, ffmpeg et deno automatiquement", text_color=FG,
            fg_color=FG, hover_color="#cfcfcf", checkmark_color=BG, border_color=MUTED,
            command=self._on_options)
        self.chk_auto.grid(row=6, column=0, sticky="w", padx=20, pady=(0, 12))
        if self.opt_delete:
            self.chk_delete.select()
        if self.opt_auto:
            self.chk_auto.select()

        self.btn_start = btn(left, "Lancer", self.start, primary=True, height=40,
                             font=ctk.CTkFont(size=15, weight="bold"))
        self.btn_start.grid(row=7, column=0, sticky="ew", padx=20, pady=(0, 12))

        self.progress_bar = ctk.CTkProgressBar(left, fg_color=BG, progress_color=FG)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=8, column=0, sticky="ew", padx=20)
        self.lbl_status = ctk.CTkLabel(left, text="Prêt.", text_color=MUTED, anchor="w")
        self.lbl_status.grid(row=9, column=0, sticky="ew", padx=20, pady=(4, 8))

        self.txt_log = ctk.CTkTextbox(left, fg_color=BG, border_color=BORDER, border_width=1,
                                      text_color=MUTED, font=ctk.CTkFont(family="Consolas", size=12))
        self.txt_log.grid(row=10, column=0, sticky="nsew", padx=20, pady=(0, 18))
        self.txt_log.configure(state="disabled")

        # ── Colonne centrale : panneau 2 (QR) au-dessus du panneau 3 (document) ──
        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.grid(row=0, column=1, sticky="nsew", padx=8, pady=(16, 8))
        mid.grid_columnconfigure(0, weight=1)
        mid.grid_rowconfigure(1, weight=1)

        qrp = panel(mid)
        qrp.grid(row=0, column=0, sticky="ew")
        qrp.grid_columnconfigure(0, weight=1)

        self.lbl_qr = ctk.CTkLabel(qrp, text="Le QR code apparaîtra ici", text_color=MUTED,
                                   width=230, height=230, fg_color=BG, corner_radius=8)
        self.lbl_qr.grid(row=0, column=0, padx=20, pady=(20, 10))
        self.lbl_title = ctk.CTkLabel(qrp, text="", text_color=FG, wraplength=320,
                                      font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_title.grid(row=1, column=0, padx=20, pady=(0, 8))
        self.entry_link = ctk.CTkEntry(qrp, fg_color=BG, border_color=BORDER, text_color=FG)
        self.entry_link.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 8))

        row = ctk.CTkFrame(qrp, fg_color="transparent")
        row.grid(row=3, column=0, padx=20, pady=(0, 16))
        self.btn_copy = btn(row, "Copier", self.copy_link, width=80)
        self.btn_copy.pack(side="left", padx=3)
        self.btn_save_qr = btn(row, "Enregistrer le QR code", self.save_qr, width=130)
        self.btn_save_qr.pack(side="left", padx=3)
        self.btn_open = btn(row, "Ouvrir", self.open_link, width=80)
        self.btn_open.pack(side="left", padx=3)
        for b in (self.btn_copy, self.btn_save_qr, self.btn_open):
            b.configure(state="disabled")

        # ── Panneau 3 : document ──
        docp = panel(mid)
        docp.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        docp.grid_columnconfigure(0, weight=1)

        self.hdr_doc = ctk.CTkLabel(docp, text="Document", text_color=MUTED,
                                    font=ctk.CTkFont(size=15, weight="bold"))
        self.hdr_doc.grid(row=0, column=0, sticky="w", padx=20, pady=(14, 2))
        self.lbl_doc_hint = ctk.CTkLabel(docp, text="", text_color=MUTED, wraplength=340,
                                         justify="left", anchor="w")
        self.lbl_doc_hint.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        self.btn_doc = btn(docp, "Choisir un PDF ou Word…", self.choose_document, height=34)
        self.btn_doc.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 6))
        self.lbl_doc = ctk.CTkLabel(docp, text="Aucun document chargé.", text_color=MUTED,
                                    wraplength=340, justify="left", anchor="w")
        self.lbl_doc.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 6))
        prow = ctk.CTkFrame(docp, fg_color="transparent")
        prow.grid(row=4, column=0, pady=(0, 14))
        self.btn_prev = btn(prow, "◀", lambda: self._change_page(-1), width=44)
        self.btn_prev.pack(side="left", padx=4)
        self.lbl_page = ctk.CTkLabel(prow, text="", text_color=FG, width=110)
        self.lbl_page.pack(side="left")
        self.btn_next = btn(prow, "▶", lambda: self._change_page(+1), width=44)
        self.btn_next.pack(side="left", padx=4)

        # ── Panneau 4 : image finale ──
        imgp = panel(self)
        imgp.grid(row=0, column=2, sticky="nsew", padx=(8, 16), pady=(16, 8))
        imgp.grid_columnconfigure(0, weight=1)

        self.hdr_img = ctk.CTkLabel(imgp, text="Image finale", text_color=MUTED,
                                    font=ctk.CTkFont(size=15, weight="bold"))
        self.hdr_img.grid(row=0, column=0, sticky="w", padx=20, pady=(14, 8))
        self.lbl_preview = ctk.CTkLabel(imgp, text="L'image apparaîtra ici", text_color=MUTED,
                                        width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1],
                                        fg_color=BG, corner_radius=8)
        self.lbl_preview.grid(row=1, column=0, padx=20, pady=(0, 6))
        self.lbl_size = ctk.CTkLabel(imgp, text="", text_color=MUTED)
        self.lbl_size.grid(row=2, column=0, pady=(0, 6))

        ctrl = ctk.CTkFrame(imgp, fg_color="transparent")
        ctrl.grid(row=3, column=0, pady=(0, 10))

        m = 0.01     # pas de déplacement (1 % de l'image)
        pos = ctk.CTkFrame(ctrl, fg_color="transparent")
        pos.grid(row=0, column=0, padx=(0, 28))
        ctk.CTkLabel(pos, text="Position", text_color=MUTED).grid(row=0, column=0, columnspan=3)
        self.btn_up = self._hold_btn(pos, "↑", lambda: self._qr_move(0, -m))
        self.btn_left = self._hold_btn(pos, "←", lambda: self._qr_move(-m, 0))
        self.btn_right = self._hold_btn(pos, "→", lambda: self._qr_move(m, 0))
        self.btn_down = self._hold_btn(pos, "↓", lambda: self._qr_move(0, m))
        self.btn_up.grid(row=1, column=1, padx=2, pady=2)
        self.btn_left.grid(row=2, column=0, padx=2, pady=2)
        self.btn_right.grid(row=2, column=2, padx=2, pady=2)
        self.btn_down.grid(row=3, column=1, padx=2, pady=2)

        size = ctk.CTkFrame(ctrl, fg_color="transparent")
        size.grid(row=0, column=1)
        ctk.CTkLabel(size, text="Taille", text_color=MUTED).grid(row=0, column=0)
        self.btn_plus = self._hold_btn(size, "+", lambda: self._qr_resize(+0.005))
        self.btn_minus = self._hold_btn(size, "−", lambda: self._qr_resize(-0.005))
        self.btn_plus.grid(row=1, column=0, padx=2, pady=2)
        self.btn_minus.grid(row=2, column=0, padx=2, pady=2)

        self.btn_save_img = btn(imgp, "Exporter le résultat…", self.save_composite, primary=True, height=36)
        self.btn_save_img.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 16))

        self._img_widgets = [self.btn_up, self.btn_down, self.btn_left, self.btn_right,
                             self.btn_plus, self.btn_minus, self.btn_save_img]

        # ── Barre du bas : compte Google et outils ──
        bar = ctk.CTkFrame(self, fg_color=PANEL, border_color=BORDER, border_width=1, corner_radius=12)
        bar.grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 16))
        bar.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="tools")

        self.btn_google = btn(bar, "Compte Google Drive…", self.open_google_dialog)
        self.btn_ytdlp = btn(bar, "yt-dlp …", lambda: self._manual("yt-dlp"))
        self.btn_ffmpeg = btn(bar, ffmpeg_label(), lambda: self._manual("ffmpeg"))
        self.btn_deno = btn(bar, "deno …", lambda: self._manual("deno"))
        for i, b in enumerate((self.btn_google, self.btn_ytdlp, self.btn_ffmpeg, self.btn_deno)):
            b.grid(row=0, column=i, sticky="ew",
                   padx=(12 if i == 0 else 4, 12 if i == 3 else 4), pady=12)

        self._update_panels()
        self._refresh_preview()

    def _hold_btn(self, parent, text: str, command, width: int = 36):
        """Bouton qui répète son action tant qu'on le maintient enfoncé."""
        b = self._mkbtn(parent, text, None, width=width, height=32)
        job = {"id": None}

        def stop(_e=None):
            if job["id"] is not None:
                try:
                    b.after_cancel(job["id"])
                except Exception:
                    pass
                job["id"] = None

        def tick():
            command()
            job["id"] = b.after(60, tick)

        def press(_e=None):
            if str(b.cget("state")) == "disabled":
                return
            stop()
            command()
            job["id"] = b.after(350, tick)

        b.bind("<ButtonPress-1>", press, add="+")
        b.bind("<ButtonRelease-1>", stop, add="+")
        return b

    def set_busy(self, flag: bool):
        self.busy = flag
        state = "disabled" if flag else "normal"
        for b in (self.btn_start, self.btn_local, self.btn_ytdlp, self.btn_ffmpeg, self.btn_deno):
            b.configure(state=state)
        self.btn_start.configure(text="En cours…" if flag else "Lancer")

    # ── journal (lignes remplaçables) ──────────────────────────────
    def log(self, msg: str, replace: int | None = None) -> int:
        """Ajoute une ligne au journal et renvoie son identifiant.
        Avec replace=<identifiant>, remplace cette ligne au lieu d'en ajouter une."""
        line_id = replace if replace is not None else next(self._log_ids)
        log.info("[UI] %s", msg)
        self.ui(self._log_ui, line_id, msg, replace is not None)
        return line_id

    def _log_ui(self, line_id: int, msg: str, replace: bool):
        tb = self.txt_log._textbox
        mark = f"line{line_id}"
        text = f"{datetime.now():%H:%M:%S}  {msg}"
        self.txt_log.configure(state="normal")
        if replace and mark in tb.mark_names():
            tb.delete(mark, f"{mark} lineend")
            tb.insert(mark, text)
        else:
            start = tb.index("end-1c")
            tb.insert("end", text + "\n")
            tb.mark_set(mark, start)
            tb.mark_gravity(mark, "left")
        tb.see("end")
        self.txt_log.configure(state="disabled")
        self.lbl_status.configure(text=msg[:90])

    def _tool_task(self, name: str, kind: str, work):
        """Affiche « nom : … », exécute work(), puis remplace la ligne par la conclusion.
        work() renvoie True (réussi), "same" (rien à faire, mises à jour) ou lève une exception.
        Renvoie True/"same", ou False en cas d'échec (détail dans le terminal et app.log)."""
        start, ok, fail, same = TASK_TEXT[kind]
        line = self.log(start.format(n=name))
        try:
            result = work()
        except Exception:
            log.exception("%s : %s en échec", name, "téléchargement" if kind == "download" else "mise à jour")
            self.log(fail.format(n=name), replace=line)
            return False
        if result == "same" and same:
            self.log(same.format(n=name), replace=line)
        else:
            self.log(ok.format(n=name), replace=line)
        return result

    def progress(self, frac: float, text: str | None = None):
        def apply():
            self.progress_bar.set(max(0.0, min(1.0, frac)))
            if text:
                self.lbl_status.configure(text=text)
        self.ui(apply)

    def _on_options(self):
        self.opt_delete = bool(self.chk_delete.get())
        self.opt_auto = bool(self.chk_auto.get())
        log.info("[UI] Options : suppression=%s, maj auto=%s", self.opt_delete, self.opt_auto)
        try:
            save_settings(delete_after=self.opt_delete, auto_update=self.opt_auto)
        except OSError as e:
            log.warning("Enregistrement des options impossible : %s", e)

    def _set_btn_text(self, btn, text: str):
        self.ui(lambda: btn.configure(text=text))

    # ── composants : installation / mises à jour ───────────────────
    def _install_ytdlp(self):
        def work():
            try:
                with step("Installation de yt-dlp"):
                    download_file(YTDLP_DL.format(asset=ytdlp_asset()), YTDLP_PATH,
                                  lambda f: self.progress(f, f"yt-dlp : {int(f * 100)} %"))
                    if not IS_WIN:
                        YTDLP_PATH.chmod(0o755)
                return True
            finally:
                self.progress(0)
        return self._tool_task("yt-dlp", "download", work)

    def _update_ytdlp(self) -> bool:
        def work():
            with step("Mise à jour de yt-dlp"):
                before = ytdlp_version()
                rc, out = run_capture([str(YTDLP_PATH), "-U"], timeout=180)
                for line in out.strip().splitlines():
                    log.debug("[yt-dlp -U] %s", line)
                after = ytdlp_version()
                self._set_btn_text(self.btn_ytdlp, f"yt-dlp {after or '?'}")
                if rc != 0:
                    raise RuntimeError(f"yt-dlp -U a échoué (code {rc})")
                self.ytdlp_verified = True
                return True if after != before else "same"
        return bool(self._tool_task("yt-dlp", "update", work))

    def _install_deno(self, kind: str = "download"):
        def work():
            try:
                with step("Installation de deno"):
                    install_deno(lambda f: self.progress(f, f"deno : {int(f * 100)} %"))
                self._set_btn_text(self.btn_deno, deno_label())
                return True
            finally:
                self.progress(0)
        return self._tool_task("deno", kind, work)

    def _install_ffmpeg(self, remote: datetime | None, kind: str = "download"):
        def work():
            try:
                with step("Installation de ffmpeg"):
                    install_ffmpeg(remote, lambda f: self.progress(f, f"ffmpeg : {int(f * 100)} %"))
                self._set_btn_text(self.btn_ffmpeg, ffmpeg_label())
                return True
            finally:
                self.progress(0)
        return self._tool_task("ffmpeg", kind, work)

    def _startup_job(self):
        # yt-dlp
        try:
            if not YTDLP_PATH.exists():
                self._install_ytdlp()
            local = ytdlp_version()
            self._set_btn_text(self.btn_ytdlp, f"yt-dlp {local or '?'}")
            latest = latest_ytdlp_version()
            log.info("yt-dlp local=%s, dernière=%s", local, latest)
            if local and latest and not is_newer(latest, local):
                self.ytdlp_verified = True
            elif local and latest:
                if self.opt_auto or self.ask(
                        "Mise à jour de yt-dlp",
                        f"Une nouvelle version est disponible ({latest}, installée : {local}). Mettre à jour ?",
                        yes="Mettre à jour", no="Plus tard"):
                    self._update_ytdlp()
                else:
                    self.log("yt-dlp : non mis à jour")
        except Exception:
            log.exception("Vérification yt-dlp")

        # deno
        try:
            if deno_local_exists():
                local = deno_version()
                latest = latest_deno_version()
                log.info("deno local=%s, dernière=%s", local, latest)
                if is_newer(latest, local):
                    if self.opt_auto or self.ask(
                            "Mise à jour de deno",
                            f"Une nouvelle version est disponible ({latest.lstrip('v')}, installée : {local}). Mettre à jour ?",
                            yes="Mettre à jour", no="Plus tard"):
                        self._install_deno("update")
                    else:
                        self.log("deno : non mis à jour")
            elif find_js_runtime():
                log.info("Runtime JS système : %s (%s)", *find_js_runtime())
            else:
                self._install_deno("download")
            self._set_btn_text(self.btn_deno, deno_label())
        except Exception:
            log.exception("deno")

        # ffmpeg
        try:
            if IS_WIN:
                remote = remote_ffmpeg_date()
                if not local_ffmpeg_exists():
                    self._install_ffmpeg(remote, "download")
                else:
                    local_d = local_ffmpeg_date()
                    old = remote and (local_d is None or (remote - local_d).days >= FFMPEG_MAX_AGE_DAYS)
                    log.info("ffmpeg local=%s, distant=%s, périmé=%s", local_d, remote, bool(old))
                    if old:
                        if self.opt_auto or self.ask(
                                "Mise à jour de ffmpeg",
                                "Votre ffmpeg date de plus de 30 jours. Télécharger un build récent (~150 Mo) ?",
                                yes="Mettre à jour", no="Plus tard"):
                            self._install_ffmpeg(remote, "update")
                        else:
                            self.log("ffmpeg : non mis à jour")
            elif not ffmpeg_available():
                self.log("ffmpeg : introuvable, à installer avec votre gestionnaire de paquets")
            self._set_btn_text(self.btn_ffmpeg, ffmpeg_label())
        except Exception:
            log.exception("ffmpeg")

        self.progress(0, "Prêt.")

    def _manual(self, which: str):
        log.info("[UI] Action : vérification manuelle %s", which)
        jobs = {"yt-dlp": self._manual_ytdlp, "ffmpeg": self._manual_ffmpeg, "deno": self._manual_deno}
        self._run_task(jobs[which])

    def _manual_deno(self):
        if not deno_local_exists():
            js = find_js_runtime()
            if js:
                self.info("deno", f"Le runtime JavaScript utilisé est {js[0]}, installé sur votre système. "
                                  "Il n'est pas géré par l'application.")
            elif self.ask("deno", "deno est absent. Le télécharger ?", yes="Télécharger", no="Annuler"):
                self._install_deno("download")
            return
        local = deno_version()
        latest = latest_deno_version()
        self._set_btn_text(self.btn_deno, deno_label())
        if not latest:
            self.info("deno", "Impossible de joindre GitHub pour vérifier la version.")
        elif is_newer(latest, local):
            if self.ask("Mise à jour de deno",
                        f"Version {latest.lstrip('v')} disponible (installée : {local}). Mettre à jour ?",
                        yes="Mettre à jour", no="Plus tard"):
                self._install_deno("update")
            else:
                self.log("deno : non mis à jour")
        else:
            self.log("deno : déjà à jour")
            self.info("deno", f"deno est à jour (version {local or '?'}).")

    def _manual_ytdlp(self):
        if not YTDLP_PATH.exists():
            self._install_ytdlp()
        local = ytdlp_version()
        latest = latest_ytdlp_version()
        self._set_btn_text(self.btn_ytdlp, f"yt-dlp {local or '?'}")
        if not latest:
            self.info("yt-dlp", "Impossible de joindre GitHub pour vérifier la version.")
        elif is_newer(latest, local):
            if self.ask("Mise à jour de yt-dlp",
                        f"Version {latest} disponible (installée : {local}). Mettre à jour ?",
                        yes="Mettre à jour", no="Plus tard"):
                self._update_ytdlp()
            else:
                self.log("yt-dlp : non mis à jour")
        else:
            self.ytdlp_verified = True
            self.log("yt-dlp : déjà à jour")
            self.info("yt-dlp", f"yt-dlp est à jour (version {local}).")

    def _manual_ffmpeg(self):
        if not IS_WIN:
            self.info("ffmpeg", "Sur ce système, ffmpeg est celui du système et n'est pas géré par l'application.")
            return
        remote = remote_ffmpeg_date()
        if not local_ffmpeg_exists():
            if self.ask("ffmpeg", "ffmpeg est absent. Le télécharger (~150 Mo) ?", yes="Télécharger", no="Annuler"):
                self._install_ffmpeg(remote, "download")
            return
        local_d = local_ffmpeg_date()
        if remote is None:
            self.info("ffmpeg", "Impossible de joindre GitHub pour vérifier la version.")
        elif local_d is None or remote > local_d:
            if self.ask("ffmpeg", "Un build plus récent est disponible. Le télécharger (~150 Mo) ?",
                        yes="Télécharger", no="Plus tard"):
                self._install_ffmpeg(remote, "update")
            else:
                self.log("ffmpeg : non mis à jour")
        else:
            self.log("ffmpeg : déjà à jour")
            self.info("ffmpeg", f"ffmpeg est à jour (build du {local_d:%Y-%m-%d}).")

    # ── vérifications avant lancement ──────────────────────────────
    def _components_ready(self) -> bool:
        if not YTDLP_PATH.exists():
            self.log("yt-dlp est absent : utilisez le bouton yt-dlp pour l'installer.")
            return False
        if not ffmpeg_available():
            self.log("ffmpeg est absent : utilisez le bouton ffmpeg pour l'installer.")
            return False
        return True

    def _drive_ready(self) -> bool:
        if not CREDENTIALS_FILE.exists():
            self.log("credentials.json manquant : importez-le dans « Compte Google Drive… ».")
            self.open_google_dialog()
            return False
        return True

    def open_google_dialog(self):
        log.info("[UI] Action : Compte Google Drive…")
        if self._google_dialog is not None and self._google_dialog.alive():
            self._google_dialog.lift()
            self._google_dialog.focus_force()
            return
        self._google_dialog = GoogleAccountDialog(self)

    # ── lancement ──────────────────────────────────────────────────
    def start(self):
        if self.busy:
            return
        url = self.url_entry.get().strip()
        log.info("[UI] Action : Lancer (%s)", url)
        if not re.match(r"https?://", url):
            self.log("Collez un lien YouTube valide (http…).")
            return
        if self._components_ready() and self._drive_ready():
            self._launch(url=url)

    def start_local(self):
        if self.busy:
            return
        log.info("[UI] Action : Fichier audio local…")
        if not self._drive_ready():
            return
        p = filedialog.askopenfilename(
            title="Choisir un fichier audio",
            filetypes=[("Audio", "*.m4a *.mp3 *.wav *.ogg *.opus *.flac *.aac"), ("Tous les fichiers", "*.*")])
        if p:
            log.info("[UI] Fichier choisi : %s", p)
            self._launch(path=Path(p))

    def _launch(self, url: str | None = None, path: Path | None = None):
        self.set_busy(True)
        self.progress(0, "Démarrage…")
        threading.Thread(target=self._pipeline, args=(url, path), daemon=True).start()

    def _pipeline(self, url, path):
        downloaded = False
        try:
            with step("Traitement complet"):
                self.ui(self.clear_result)
                base = 0.0
                if url:
                    path, title = self._download(url)
                    downloaded = True
                    base = 0.5
                else:
                    title = path.stem
                service = get_drive_service(interactive=True)
                link = self._upload(service, path, base)
                self.progress(1.0, "Terminé.")
                self.ui(self.show_result, title, link)
                if downloaded and self.opt_delete:
                    try:
                        path.unlink()
                        self.log("Fichier téléchargé supprimé.")
                    except OSError as e:
                        self.log(f"Suppression impossible : {e}")
        except Exception as e:
            log.exception("Échec du traitement")
            self.log(f"Échec : {friendly_google_error(e)}")
            self.progress(0)
        finally:
            self.ui(self.set_busy, False)

    # ── téléchargement ─────────────────────────────────────────────
    def _download(self, url: str) -> tuple[Path, str]:
        line = self.log("Audio : téléchargement…")
        try:
            result = self._try_download(url)
        except Exception:
            self.log("Audio : téléchargement échoué", replace=line)
            raise
        self.log("Audio : téléchargement réussi", replace=line)
        log.info("Fichier téléchargé : %s", result[0].name)
        return result

    def _try_download(self, url: str) -> tuple[Path, str]:
        fmt = self.fmt_var.get()
        last_err = ""
        for name, extra in STRATEGIES:
            log.info("Téléchargement : stratégie %s", name)
            ok, path, title, err = self._run_ytdlp(url, fmt, extra)
            if ok:
                return path, title
            last_err = err
            log.warning("Stratégie %s en échec : %s", name, err)
            if any(m in err.lower() for m in FATAL_MARKERS):
                raise RuntimeError(err)
        if not self.ytdlp_verified:
            log.info("Échec du téléchargement : mise à jour de yt-dlp puis dernier essai")
            if self._update_ytdlp():
                ok, path, title, err = self._run_ytdlp(url, fmt, [])
                if ok:
                    return path, title
                last_err = err
        raise RuntimeError(
            (last_err or "Téléchargement impossible.")
            + "\nLa vidéo est peut-être restreinte : essayez « Fichier audio local… ».")

    def _run_ytdlp(self, url: str, fmt: str, extra: list[str]):
        t_start = time.time()
        cmd = [
            str(YTDLP_PATH), "--no-playlist", "--newline", "--progress",
            "-f", "bestaudio[ext=m4a]/bestaudio/best" if fmt == "m4a" else "bestaudio/best",
            "-x", "--audio-format", fmt,
            "--windows-filenames", "--trim-filenames", "120",
            "-P", str(DOWNLOAD_DIR), "-o", "%(title)s [%(id)s].%(ext)s",
            "--no-simulate",
            "--print", "before_dl:__TITLE__%(title)s",
            "--print", "after_move:__FILE__%(filepath)s",
        ]
        if fmt == "mp3":
            cmd += ["--audio-quality", "0"]
        if ARGS.verbose:
            cmd.append("-v")
        js = find_js_runtime()
        if js:
            cmd += ["--js-runtimes", f"{js[0]}:{js[1]}"]
        if local_ffmpeg_exists():
            cmd += ["--ffmpeg-location", str(TOOLS_DIR)]
        cmd += extra + [url]
        log.debug("Commande : %s", " ".join(cmd))

        title, filepath = "", ""
        lines: list[str] = []
        prog_re = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=tool_env(), creationflags=NO_WINDOW, bufsize=1)
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("__TITLE__"):
                title = line[9:]
            elif line.startswith("__FILE__"):
                filepath = line[8:]
            else:
                m = prog_re.search(line)
                if m:
                    pct = float(m.group(1))
                    self.progress(pct / 100 * 0.45, f"Téléchargement : {pct:.0f} %")
                    log.debug("[yt-dlp] %s", line)
                else:
                    lines.append(line)
                    log.info("[yt-dlp] %s", line)
        rc = proc.wait()

        if rc != 0:
            errs = [l for l in lines if "ERROR" in l]
            return False, Path(), "", "\n".join(errs or lines[-5:]) or f"yt-dlp a échoué (code {rc})."

        path = Path(filepath) if filepath else Path()
        if not (filepath and path.exists()):
            cands = [p for p in DOWNLOAD_DIR.iterdir()
                     if p.is_file() and p.suffix.lower() == f".{fmt}" and p.stat().st_mtime >= t_start - 2]
            if not cands:
                return False, Path(), "", "Fichier téléchargé introuvable."
            path = max(cands, key=lambda p: p.stat().st_mtime)
            log.warning("Chemin imprimé inutilisable, repli sur : %s", path)
        return True, path, title or path.stem, ""

    # ── envoi Drive ────────────────────────────────────────────────
    def _upload(self, service, path: Path, base: float) -> str:
        line = self.log("Google Drive : envoi…")
        try:
            link = self._do_upload(service, path, base)
        except Exception:
            self.log("Google Drive : envoi échoué", replace=line)
            raise
        self.log("Google Drive : envoi réussi", replace=line)
        return link

    def _do_upload(self, service, path: Path, base: float) -> str:
        with step("Envoi sur Google Drive"):
            folder_name = get_drive_folder_name()
            log.info("Dossier Drive : %s", folder_name)
            folder_id = get_or_create_folder(service, folder_name)
            media = MediaFileUpload(str(path), mimetype=guess_mime(path), resumable=True,
                                    chunksize=5 * 1024 * 1024)
            req = service.files().create(body={"name": path.name, "parents": [folder_id]},
                                         media_body=media, fields="id")
            resp = None
            while resp is None:
                status, resp = req.next_chunk()
                if status:
                    f = status.progress()
                    self.progress(base + f * (1 - base), f"Envoi : {int(f * 100)} %")
            file_id = resp["id"]
            service.permissions().create(
                fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
            link = f"https://drive.google.com/file/d/{file_id}/view"
            log.info("Lien : %s", link)
            return link

    # ── résultat (panneau 2) ───────────────────────────────────────
    def clear_result(self):
        self._link = ""
        self._qr_pil = None
        self._qr_img = None
        self._qr_base = None
        self.lbl_qr.configure(text="Le QR code apparaîtra ici")
        clear_label_image(self.lbl_qr)
        self.lbl_title.configure(text="")
        self.entry_link.delete(0, "end")
        for b in (self.btn_copy, self.btn_save_qr, self.btn_open):
            b.configure(state="disabled")
        self._update_panels()
        self._refresh_preview()

    def show_result(self, title: str, link: str):
        self._link = link
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
        qr.add_data(link)
        qr.make(fit=True)
        self._qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        self._qr_img = ctk.CTkImage(light_image=self._qr_pil, dark_image=self._qr_pil, size=(220, 220))
        self.lbl_qr.configure(image=self._qr_img, text="")
        self.lbl_title.configure(text=title)
        self.entry_link.delete(0, "end")
        self.entry_link.insert(0, link)
        for b in (self.btn_copy, self.btn_save_qr, self.btn_open):
            b.configure(state="normal")
        self._qr_base = make_qr_base(link)
        self._update_panels()
        self._refresh_preview()

    def copy_link(self):
        log.info("[UI] Action : Copier le lien")
        self.clipboard_clear()
        self.clipboard_append(self._link)
        self.log("Lien copié.")

    def save_qr(self):
        log.info("[UI] Action : Enregistrer le QR")
        if not self._qr_pil:
            return
        p = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")],
                                         initialfile="qrcode.png", title="Enregistrer le QR code")
        if p:
            self._qr_pil.resize((800, 800), Image.NEAREST).save(p)
            self.log(f"QR enregistré : {p}")

    def open_link(self):
        log.info("[UI] Action : Ouvrir le lien")
        if self._link:
            webbrowser.open(self._link)

    # ── panneaux 3 et 4 : document et image finale ─────────────────
    def _update_panels(self):
        qr_ready = self._qr_base is not None
        doc_ready = qr_ready and self._bg_full is not None
        pages = self._pdf.page_count if self._pdf is not None else 0

        def st(flag: bool) -> str:
            return "normal" if flag else "disabled"

        self.hdr_doc.configure(text_color=FG if qr_ready else MUTED)
        self.hdr_img.configure(text_color=FG if doc_ready else MUTED)
        if not qr_ready:
            self.lbl_doc_hint.configure(
                text="S'active quand le QR code est prêt (envoyez d'abord un son sur Google Drive).")
        else:
            self.lbl_doc_hint.configure(
                text="Chargez un PDF ou un document Word : il servira de fond à l'image finale, "
                     "avec le QR code par-dessus.")
        self.btn_doc.configure(state=st(qr_ready and not self._doc_loading))
        self.btn_prev.configure(state=st(doc_ready and pages > 1 and self._page > 0))
        self.btn_next.configure(state=st(doc_ready and pages > 1 and self._page < pages - 1))
        self.lbl_page.configure(text=f"Page {self._page + 1} / {pages}" if pages > 1 else "")
        for w in self._img_widgets:
            w.configure(state=st(doc_ready))

    def _refresh_preview(self):
        if self._bg_prev is None or self._bg_full is None:
            self._prev_img = None
            self.lbl_preview.configure(text="L'image apparaîtra ici")
            clear_label_image(self.lbl_preview)
            self.lbl_size.configure(text="")
            return
        if self._qr_base is not None:
            img = compose_image(self._bg_prev, self._qr_base, self._qr_frac,
                                self._qr_cx, self._qr_cy, full_width=self._bg_full.width)
            self.lbl_size.configure(text=f"Taille du QR code : {self._qr_frac * 100:.1f} % de la largeur")
        else:
            img = self._bg_prev
            self.lbl_size.configure(text="")
        self._prev_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
        self.lbl_preview.configure(image=self._prev_img, text="")

    def _clamp_qr(self):
        W, H = self._bg_full.size if self._bg_full is not None else (1, 1)
        self._qr_frac = max(0.05, min(self._qr_frac, min(0.9, H / W)))
        half_x = self._qr_frac / 2
        half_y = self._qr_frac * W / H / 2
        self._qr_cx = max(half_x, min(1 - half_x, self._qr_cx))
        self._qr_cy = max(half_y, min(1 - half_y, self._qr_cy))

    def _qr_move(self, dx: float, dy: float):
        if self._bg_full is None:
            return
        self._qr_cx += dx
        self._qr_cy += dy
        self._clamp_qr()
        self._refresh_preview()

    def _qr_resize(self, delta: float):
        if self._bg_full is None:
            return
        self._qr_frac += delta
        self._clamp_qr()
        self._refresh_preview()

    def _set_background(self, img: Image.Image, reset_pos: bool = True):
        self._bg_full = img
        prev = img.copy()
        prev.thumbnail(PREVIEW_SIZE, Image.LANCZOS)
        self._bg_prev = prev
        if reset_pos:
            self._qr_frac, self._qr_cx, self._qr_cy = 0.20, 0.80, 0.80   # en bas à droite
        self._clamp_qr()

    def choose_document(self):
        if self._qr_base is None or self._doc_loading:
            return
        log.info("[UI] Action : Choisir un document")
        p = filedialog.askopenfilename(title="Choisir un PDF ou un document Word", filetypes=DOC_TYPES)
        if not p:
            return
        self._doc_loading = True
        self._update_panels()
        line = self.log("Document : chargement…")
        threading.Thread(target=self._load_document_job, args=(Path(p), line), daemon=True).start()

    def _load_document_job(self, path: Path, line: int):
        try:
            with step(f"Chargement du document {path.name}"):
                doc = open_document(path)
                img = render_pdf_page(doc, 0)
            self.ui(self._document_loaded, path, doc, img, None, line)
        except Exception as e:
            log.exception("Chargement du document en échec")
            self.ui(self._document_loaded, path, None, None, e, line)

    def _document_loaded(self, path: Path, doc, img, err, line: int):
        self._doc_loading = False
        if err is not None:
            self.log("Document : chargement échoué", replace=line)
            if isinstance(err, (ValueError, RuntimeError)):
                msg = str(err)
            else:
                msg = "Document illisible ou format non pris en charge."
            self.lbl_doc.configure(text=msg, text_color=ERR_COLOR)
        else:
            self.log("Document : chargement réussi", replace=line)
            if self._pdf is not None:
                try:
                    self._pdf.close()
                except Exception:
                    pass
            self._pdf = doc
            self._doc_name = path.name
            self._page = 0
            self._set_background(img, reset_pos=self._bg_full is None)
            self.lbl_doc.configure(text=path.name, text_color=FG)
        self._update_panels()
        self._refresh_preview()

    def _change_page(self, delta: int):
        if self._pdf is None:
            return
        new = max(0, min(self._pdf.page_count - 1, self._page + delta))
        if new == self._page:
            return
        try:
            img = render_pdf_page(self._pdf, new)
        except Exception:
            log.exception("Rendu de la page %s impossible", new + 1)
            self.log("Document : page illisible")
            return
        self._page = new
        self._set_background(img, reset_pos=False)
        self._update_panels()
        self._refresh_preview()

    def save_composite(self):
        """Exporte le résultat en PNG, PDF ou Word (.docx) selon l'extension choisie."""
        log.info("[UI] Action : Exporter le résultat")
        if self._bg_full is None or self._qr_base is None:
            return
        stem = Path(self._doc_name).stem or "image"
        if self._pdf is not None and self._pdf.page_count > 1:
            stem += f"-p{self._page + 1}"
        p = filedialog.asksaveasfilename(
            title="Exporter le résultat",
            initialfile=f"{stem}-qr",
            defaultextension=".png",
            filetypes=[("Image PNG", "*.png"),
                       ("Document PDF", "*.pdf"),
                       ("Document Word (.docx)", "*.docx")])
        if not p:
            return

        path = Path(p)
        ext = path.suffix.lower()
        if ext not in EXPORT_TYPES:               # extension inconnue : on ajoute .png
            path = path.with_name(path.name + ".png")
            ext = ".png"
        label = EXPORT_TYPES[ext]

        # Valeurs figées avant de partir dans le thread
        bg, base = self._bg_full, self._qr_base
        frac, cx, cy = self._qr_frac, self._qr_cx, self._qr_cy
        try:
            rect = self._pdf.load_page(self._page).rect
            page_pts = (float(rect.width), float(rect.height))
        except Exception:
            page_pts = (bg.width / RENDER_DPI * 72, bg.height / RENDER_DPI * 72)

        line = self.log(f"Export {label} : enregistrement…")

        def job():
            try:
                img = compose_image(bg, base, frac, cx, cy)
                if ext == ".pdf":
                    export_pdf(img, path, page_pts)
                elif ext == ".docx":
                    export_docx(img, path, page_pts)
                else:
                    export_png(img, path)
            except Exception as e:
                log.exception("Export %s en échec", label)
                self.log(f"Export {label} : enregistrement échoué", replace=line)
                self.info("Export impossible", str(e) or e.__class__.__name__)
                return
            self.log(f"Export {label} : enregistrement réussi", replace=line)
            log.info("Export enregistré : %s", path)

        threading.Thread(target=job, daemon=True).start()


def main():
    log.info("=== SAJJEEP Uploader – Python %s – %s ===", platform.python_version(), platform.platform())
    App().mainloop()


if __name__ == "__main__":
    main()