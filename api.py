"""
api.py — Backend FastAPI pour AlphaConvert
"""
import os, re, logging, unicodedata
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AlphaConvert API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

DOWNLOAD_PATH = "/tmp/alphaconvert"
os.makedirs(DOWNLOAD_PATH, exist_ok=True)


def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    elif "instagram.com" in u:
        return "Instagram"
    elif "tiktok.com" in u:
        return "TikTok"
    return "Inconnu"


def safe_filename(name: str) -> str:
    name = unicodedata.normalize('NFKD', name)
    name = name.encode('ascii', 'ignore').decode('ascii')
    name = re.sub(r'[^\w\s\-.]', '_', name)
    return name.strip() or "video"


@app.get("/info")
async def get_info(url: str):
    try:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "title":     info.get("title", "Vidéo"),
            "duration":  info.get("duration", 0),
            "thumbnail": info.get("thumbnail"),
            "uploader":  info.get("uploader", ""),
            "platform":  detect_platform(url),
        }
    except Exception as e:
        logger.error(f"get_info error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/download")
async def download(url: str, format: str = "mp4", quality: str = "720"):
    tpl = os.path.join(DOWNLOAD_PATH, "%(id)s.%(ext)s")
    base_opts = {
        "outtmpl": tpl,
        "quiet": False,
        "no_warnings": False,
        "restrictfilenames": True,
    }

    if format == "mp3":
        opts = {**base_opts, "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio"}
    else:
        qmap = {
            "1080": "best[height<=1080][ext=mp4]/best[height<=1080]/best[ext=mp4]/best",
            "720":  "best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best",
            "480":  "best[height<=480][ext=mp4]/best[height<=480]/best[ext=mp4]/best",
            "360":  "best[height<=360][ext=mp4]/best[height<=360]/best[ext=mp4]/best",
        }
        opts = {**base_opts, "format": qmap.get(quality, qmap["720"])}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get("id", "video")
            for ext in [".mp4", ".mp3", ".mkv", ".webm", ".m4a"]:
                candidate = os.path.join(DOWNLOAD_PATH, f"{video_id}{ext}")
                if os.path.exists(candidate):
                    title = safe_filename(info.get("title", "video"))
                    dl_name = f"{title}{ext}"
                    return FileResponse(
                        candidate,
                        media_type="application/octet-stream",
                        filename=dl_name,
                        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'}
                    )
        raise HTTPException(status_code=500, detail="Fichier introuvable")
    except Exception as e:
        logger.error(f"download error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}
