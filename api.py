"""
api.py — Backend FastAPI AlphaConvert
— yt-dlp prioritaire + RapidAPI fallback YouTube, TikTok
"""
import os, re, logging, unicodedata, base64, random, httpx, urllib.parse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AlphaConvert API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

DOWNLOAD_PATH = "/tmp/alphaconvert"
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# ── RapidAPI ──────────────────────────────────────────────────────────────────
_raw_keys = os.environ.get("RAPIDAPI_KEYS", os.environ.get("RAPIDAPI_KEY", ""))
RAPIDAPI_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
_rapi_idx = 0

def _get_rapidapi_key():
    global _rapi_idx
    if not RAPIDAPI_KEYS: return None
    key = RAPIDAPI_KEYS[_rapi_idx % len(RAPIDAPI_KEYS)]
    _rapi_idx += 1
    return key

_raw_proxies = os.environ.get("PROXY_URLS", os.environ.get("PROXY_URL", ""))
PROXY_LIST = [p.strip() for p in _raw_proxies.split(",") if p.strip()]
logger.info(f"Proxies: {len(PROXY_LIST)} | RapidAPI keys: {len(RAPIDAPI_KEYS)}")

# ── Helpers ───────────────────────────────────────────────────────────────────
def clean_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url.strip())
        params = urllib.parse.parse_qs(parsed.query)
        if "youtube.com" in parsed.netloc or "youtu.be" in parsed.netloc:
            clean_params = {k: v for k, v in params.items() if k == "v"}
            new_query = urllib.parse.urlencode(clean_params, doseq=True)
            return parsed._replace(query=new_query).geturl()
        if "tiktok.com" in parsed.netloc or "vm.tiktok" in parsed.netloc:
            return parsed._replace(query="", fragment="").geturl()
    except Exception:
        pass
    return url.strip()

def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "tiktok.com" in u or "vm.tiktok" in u: return "tiktok"
    return "unknown"

def safe_filename(name: str) -> str:
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^\w\s\-.]', '_', name).strip() or "video"

def _extract_yt_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else url

def _save_stream(dl_url: str, title: str, ext: str) -> str:
    safe = re.sub(r'[^\w\-]', '_', title)[:60]
    path = os.path.join(DOWNLOAD_PATH, f"{safe}{ext}")
    with httpx.stream("GET", dl_url, timeout=120, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes(8192):
                f.write(chunk)
    return path

# ── RapidAPI download ─────────────────────────────────────────────────────────
def _rapi_download(url: str, platform: str, format_type: str):
    """Retourne (path_or_direct_url, title, is_redirect)"""
    key = _get_rapidapi_key()
    if not key: return None, "media", False
    try:
        if platform == "youtube" and format_type == "mp3":
            r = httpx.get("https://youtube-mp36.p.rapidapi.com/dl",
                params={"id": _extract_yt_id(url)},
                headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "youtube-mp36.p.rapidapi.com"}, timeout=30)
            logger.info(f"youtube-mp36 MP3: {r.status_code}")
            if r.status_code == 200:
                d = r.json()
                if d.get("link"):
                    path = _save_stream(d["link"], d.get("title", "audio"), ".mp3")
                    return path, d.get("title", "audio"), False

        elif platform == "youtube":
            # Essai 1 : yt-api (retourne URL directe → redirect)
            r = httpx.get("https://yt-api.p.rapidapi.com/dl",
                params={"id": _extract_yt_id(url), "cgeo": "US"},
                headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "yt-api.p.rapidapi.com"}, timeout=30)
            logger.info(f"yt-api: {r.status_code}")
            if r.status_code == 200:
                d = r.json()
                title = d.get("title", "video")
                # Chercher formats combinés (vidéo+audio) en priorité
                formats = d.get("formats", []) + d.get("adaptiveFormats", [])
                mp4s = [f for f in formats if f.get("mimeType", "").startswith("video/mp4") and f.get("url")]
                if mp4s:
                    best = sorted(mp4s, key=lambda x: x.get("height", 0), reverse=True)[0]
                    # Redirect direct — le navigateur télécharge lui-même
                    return best["url"], title, True

            # Essai 2 : youtube-mp36 (MP4 via format=mp4)
            r2 = httpx.get("https://youtube-mp36.p.rapidapi.com/dl",
                params={"id": _extract_yt_id(url), "format": "mp4"},
                headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "youtube-mp36.p.rapidapi.com"}, timeout=30)
            logger.info(f"youtube-mp36 MP4: {r2.status_code}")
            if r2.status_code == 200:
                d2 = r2.json()
                dl_url = d2.get("link") or d2.get("url")
                if dl_url:
                    return dl_url, d2.get("title", "video"), True

        elif platform == "tiktok":
            r = httpx.get("https://tiktok-scraper7.p.rapidapi.com/video/info",
                params={"url": url, "hd": "1"},
                headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "tiktok-scraper7.p.rapidapi.com"}, timeout=30)
            logger.info(f"tiktok-scraper7: {r.status_code}")
            if r.status_code == 200:
                d = r.json().get("data", {})
                title = d.get("title", "tiktok")
                dl_url = d.get("hdplay") or d.get("play") or d.get("wmplay")
                if dl_url:
                    ext = ".mp3" if format_type == "mp3" else ".mp4"
                    path = _save_stream(dl_url, title, ext)
                    return path, title, False

    except Exception as e:
        logger.error(f"RapidAPI download [{platform}]: {e}")
    return None, "media", False

# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/info")
async def get_info(url: str):
    url = clean_url(url)
    platform = detect_platform(url)
    if platform == "unknown":
        raise HTTPException(status_code=400, detail="Plateforme non supportée")

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {"title": info.get("title", "Vidéo"), "duration": info.get("duration", 0),
                "thumbnail": info.get("thumbnail"), "uploader": info.get("uploader", ""), "platform": platform}
    except Exception:
        logger.warning(f"yt-dlp info [{platform}] failed → RapidAPI")

    key = _get_rapidapi_key()
    if key:
        try:
            if platform == "tiktok":
                r = httpx.get("https://tiktok-scraper7.p.rapidapi.com/video/info",
                    params={"url": url, "hd": "1"},
                    headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "tiktok-scraper7.p.rapidapi.com"}, timeout=15)
                if r.status_code == 200:
                    d = r.json().get("data", {})
                    return {"title": d.get("title", "TikTok"), "duration": d.get("duration", 0),
                            "thumbnail": d.get("cover"), "uploader": d.get("author", {}).get("nickname", ""),
                            "platform": platform}
            elif platform == "youtube":
                r = httpx.get("https://youtube-mp36.p.rapidapi.com/dl",
                    params={"id": _extract_yt_id(url)},
                    headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "youtube-mp36.p.rapidapi.com"}, timeout=15)
                if r.status_code == 200:
                    d = r.json()
                    return {"title": d.get("title", "YouTube"), "duration": int(d.get("duration", 0) or 0),
                            "thumbnail": None, "uploader": "YouTube", "platform": platform}
        except Exception as e2:
            logger.error(f"RapidAPI info [{platform}]: {e2}")

    raise HTTPException(status_code=400, detail="Impossible d'analyser ce lien")


@app.get("/download")
async def download(url: str, format: str = "mp4", quality: str = "720"):
    url = clean_url(url)
    platform = detect_platform(url)
    if platform == "unknown":
        raise HTTPException(status_code=400, detail="Plateforme non supportée")

    tpl = os.path.join(DOWNLOAD_PATH, "%(id)s.%(ext)s")
    base_opts = {"outtmpl": tpl, "quiet": False, "no_warnings": False, "restrictfilenames": True}

    if format == "mp3":
        opts = {**base_opts, "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]}
    else:
        qmap = {"1080": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
                "720":  "best[height<=720][ext=mp4]/best[height<=720]/best",
                "480":  "best[height<=480][ext=mp4]/best[height<=480]/best",
                "360":  "best[height<=360][ext=mp4]/best[height<=360]/best"}
        opts = {**base_opts, "format": qmap.get(quality, qmap["720"])}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            vid = info.get("id", "video")
            for ext in [".mp4", ".mp3", ".mkv", ".webm", ".m4a"]:
                candidate = os.path.join(DOWNLOAD_PATH, f"{vid}{ext}")
                if os.path.exists(candidate):
                    title = safe_filename(info.get("title", "video"))
                    dl_name = f"{title}{ext}"
                    return FileResponse(candidate, media_type="application/octet-stream", filename=dl_name,
                                        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'})
    except Exception as e:
        logger.warning(f"yt-dlp download [{platform}] failed → RapidAPI")

    file_path, title, is_redirect = _rapi_download(url, platform, format)
    if file_path:
        if is_redirect:
            return RedirectResponse(url=file_path)
        if os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1]
            dl_name = f"{safe_filename(title)}{ext}"
            return FileResponse(file_path, media_type="application/octet-stream", filename=dl_name,
                                headers={"Content-Disposition": f'attachment; filename="{dl_name}"'})

    raise HTTPException(status_code=400, detail="Téléchargement impossible")


@app.get("/health")
async def health():
    return {"status": "ok", "rapidapi_keys": len(RAPIDAPI_KEYS)}
