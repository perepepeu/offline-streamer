import json
import os
import re
import shutil
import subprocess
import sys
import csv
import io
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, flash, Response, jsonify, stream_with_context
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
FAVS_FILE = BASE_DIR / "favorites.json"
HISTORY_FILE = BASE_DIR / "history.json"


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {"downloads_dir": "D:/VIDEOS", "default_quality": "best", "autoplay": True, "theme": "dark"}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_downloads_dir():
    cfg = load_config()
    p = Path(cfg.get("downloads_dir", "D:/VIDEOS"))
    p.mkdir(parents=True, exist_ok=True)
    return p


app = Flask(__name__)
app.secret_key = "offline-streamer-local-key"

# ─── SSE download progress ──────────────────────────────────────────────────
download_events = {}  # task_id -> list of messages


# ─── Helpers ────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9-_ ]+", "", text or "video")
    return re.sub(r"\s+", "-", text).strip("-").lower()[:80] or "video"


def run_cmd(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def detect_video_id(url: str):
    try:
        parsed = urlparse(url)
        if "youtube.com" in parsed.netloc:
            return parse_qs(parsed.query).get("v", [None])[0]
        if "youtu.be" in parsed.netloc:
            return parsed.path.strip("/") or None
    except Exception:
        return None
    return None


def quality_selector(quality: str) -> str:
    if quality == "audio":
        return "bestaudio/best"
    if quality == "720":
        return "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    if quality == "480":
        return "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    if quality == "360":
        return "bestvideo[height<=360]+bestaudio/best[height<=360]/best"
    return "bestvideo+bestaudio/best"


def ensure_ytdlp():
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


# ─── Favorites & History ────────────────────────────────────────────────────

def load_favs():
    if FAVS_FILE.exists():
        return json.loads(FAVS_FILE.read_text(encoding="utf-8"))
    return []


def save_favs(favs):
    FAVS_FILE.write_text(json.dumps(favs, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history():
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def add_to_history(video_id: str):
    history = load_history()
    history = [h for h in history if h.get("id") != video_id]
    history.insert(0, {"id": video_id, "watched_at": datetime.now().isoformat()})
    history = history[:100]  # keep last 100
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── Library scanner ────────────────────────────────────────────────────────

def scan_library():
    """
    Returns (videos, playlists) where:
      - videos: list of single-video dicts
      - playlists: list of {meta, videos: [...]}
    """
    DOWNLOADS_DIR = get_downloads_dir()
    all_items = []
    for folder in sorted(DOWNLOADS_DIR.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        meta_file = folder / "meta.json"
        if not meta_file.exists():
            continue
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            video_file = next(
                (p.name for p in folder.iterdir()
                 if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mp3", ".m4a"}), None
            )
            thumb_file = next(
                (p.name for p in folder.iterdir()
                 if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None
            )
            data["file_name"] = video_file
            data["thumbnail"] = thumb_file
            if video_file:
                all_items.append(data)
        except Exception:
            continue

    favs = set(load_favs())
    history_ids = [h["id"] for h in load_history()]

    # separate playlists
    playlist_map = {}
    loose_videos = []
    for item in all_items:
        item["is_favorite"] = item.get("id") in favs
        pl_id = item.get("playlist_id")
        pl_title = item.get("playlist_title")
        if pl_id and pl_title:
            if pl_id not in playlist_map:
                playlist_map[pl_id] = {
                    "id": pl_id,
                    "title": pl_title,
                    "channel": item.get("channel"),
                    "videos": []
                }
            playlist_map[pl_id]["videos"].append(item)
        else:
            loose_videos.append(item)

    # sort playlist videos by playlist_index
    playlists = []
    for pl in playlist_map.values():
        pl["videos"].sort(key=lambda x: x.get("playlist_index") or 0)
        pl["thumbnail"] = pl["videos"][0]["thumbnail"] if pl["videos"] else None
        pl["video_count"] = len(pl["videos"])
        playlists.append(pl)

    return loose_videos, playlists


def find_item(video_id: str):
    loose, playlists = scan_library()
    all_vids = loose + [v for pl in playlists for v in pl["videos"]]
    for item in all_vids:
        if item.get("id") == video_id:
            return item
    return None


def find_playlist(pl_id: str):
    _, playlists = scan_library()
    for pl in playlists:
        if pl["id"] == pl_id:
            return pl
    return None


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    q = request.args.get("q", "").strip().lower()
    tag_filter = request.args.get("tag", "").strip().lower()
    loose_videos, playlists = scan_library()
    history_ids = [h["id"] for h in load_history()]
    cfg = load_config()

    if q:
        loose_videos = [v for v in loose_videos
                        if q in (v.get("title") or "").lower()
                        or q in (v.get("channel") or "").lower()]
        playlists = [pl for pl in playlists
                     if q in pl["title"].lower()
                     or q in (pl.get("channel") or "").lower()]

    if tag_filter:
        loose_videos = [v for v in loose_videos
                        if tag_filter in [t.lower() for t in (v.get("tags") or [])]]

    # collect all tags
    all_tags = sorted({t for v in loose_videos for t in (v.get("tags") or [])})

    return render_template(
        "index.html",
        videos=loose_videos,
        playlists=playlists,
        history_ids=history_ids,
        q=q,
        tag_filter=tag_filter,
        all_tags=all_tags,
        cfg=cfg
    )


@app.route("/watch/<video_id>")
def watch(video_id):
    item = find_item(video_id)
    if not item:
        flash("Vídeo não encontrado.")
        return redirect(url_for("index"))
    add_to_history(video_id)
    cfg = load_config()

    # build queue (playlist or all loose)
    pl_id = item.get("playlist_id")
    if pl_id:
        pl = find_playlist(pl_id)
        queue = pl["videos"] if pl else [item]
        pl_title = item.get("playlist_title")
    else:
        loose, _ = scan_library()
        queue = loose
        pl_title = None

    idx = next((i for i, v in enumerate(queue) if v["id"] == video_id), 0)
    next_video = queue[idx + 1] if idx + 1 < len(queue) else None
    prev_video = queue[idx - 1] if idx > 0 else None

    return render_template(
        "watch.html",
        current=item,
        queue=queue,
        next_video=next_video,
        prev_video=prev_video,
        pl_title=pl_title,
        cfg=cfg
    )


@app.route("/playlist/<pl_id>")
def playlist_view(pl_id):
    pl = find_playlist(pl_id)
    if not pl:
        flash("Playlist não encontrada.")
        return redirect(url_for("index"))
    cfg = load_config()
    return render_template("playlist.html", pl=pl, cfg=cfg)


@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url", "").strip()
    quality = request.form.get("quality", load_config().get("default_quality", "best"))
    if not url:
        flash("URL inválida.")
        return redirect(url_for("index"))

    DOWNLOADS_DIR = get_downloads_dir()

    probe_cmd = ensure_ytdlp() + ["-J", "--flat-playlist", "--no-warnings", url]
    probe = run_cmd(probe_cmd)
    if probe.returncode != 0:
        flash("Falha ao ler a URL.")
        return redirect(url_for("index"))

    try:
        info = json.loads(probe.stdout)
    except json.JSONDecodeError:
        flash("Não foi possível interpretar os metadados.")
        return redirect(url_for("index"))

    is_playlist = info.get("_type") == "playlist"
    pl_id = info.get("id") if is_playlist else None
    pl_title = info.get("title") if is_playlist else None

    out_base = DOWNLOADS_DIR / slugify(info.get("title") or detect_video_id(url) or "video")
    out_base.mkdir(parents=True, exist_ok=True)

    output_template = str(out_base / "%(playlist_index|)s%(title)s [%(id)s].%(ext)s")
    cmd = ensure_ytdlp() + [
        url,
        "-f", quality_selector(quality),
        "--yes-playlist",
        "--write-info-json",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "--merge-output-format", "mp4",
        "-o", output_template,
    ]

    result = run_cmd(cmd)
    if result.returncode != 0:
        flash("Download falhou. Verifique ffmpeg e yt-dlp.")
        return redirect(url_for("index"))

    for info_json in out_base.glob("*.info.json"):
        try:
            data = json.loads(info_json.read_text(encoding="utf-8"))
            meta = {
                "id": data.get("id"),
                "title": data.get("title"),
                "channel": data.get("channel") or data.get("uploader"),
                "description": data.get("description"),
                "duration": data.get("duration"),
                "duration_string": data.get("duration_string"),
                "webpage_url": data.get("webpage_url"),
                "upload_date": data.get("upload_date"),
                "view_count": data.get("view_count"),
                "like_count": data.get("like_count"),
                "tags": [],
            }
            if is_playlist:
                meta["playlist_id"] = pl_id
                meta["playlist_title"] = pl_title
                meta["playlist_index"] = data.get("playlist_index")

            if meta["id"]:
                item_dir = DOWNLOADS_DIR / meta["id"]
                item_dir.mkdir(exist_ok=True)
                for file in out_base.iterdir():
                    if f"[{meta['id']}]" in file.name:
                        shutil.copy2(file, item_dir / file.name)
                (item_dir / "meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        except Exception:
            continue

    if out_base.exists():
        try:
            shutil.rmtree(out_base)
        except Exception:
            pass

    flash(f"{'Playlist' if is_playlist else 'Vídeo'} baixado com sucesso!")
    return redirect(url_for("index"))


@app.route("/stream/<video_id>")
def stream_video(video_id):
    DOWNLOADS_DIR = get_downloads_dir()
    item = find_item(video_id)
    if not item or not item.get("file_name"):
        return "Arquivo não encontrado", 404
    return send_from_directory(DOWNLOADS_DIR / video_id, item["file_name"], as_attachment=False)


@app.route("/file/<video_id>")
def file_link(video_id):
    DOWNLOADS_DIR = get_downloads_dir()
    item = find_item(video_id)
    if not item or not item.get("file_name"):
        return "Arquivo não encontrado", 404
    return send_from_directory(DOWNLOADS_DIR / video_id, item["file_name"], as_attachment=True)


@app.route("/thumb/<video_id>")
def thumb(video_id):
    DOWNLOADS_DIR = get_downloads_dir()
    item = find_item(video_id)
    if not item or not item.get("thumbnail"):
        return "Thumbnail não encontrada", 404
    return send_from_directory(DOWNLOADS_DIR / video_id, item["thumbnail"], as_attachment=False)


# ─── Feature: Favorites ──────────────────────────────────────────────────────

@app.route("/favorite/<video_id>", methods=["POST"])
def toggle_favorite(video_id):
    favs = load_favs()
    if video_id in favs:
        favs.remove(video_id)
        status = "removed"
    else:
        favs.append(video_id)
        status = "added"
    save_favs(favs)
    return jsonify({"status": status, "count": len(favs)})


# ─── Feature: Tags ───────────────────────────────────────────────────────────

@app.route("/tag/<video_id>", methods=["POST"])
def set_tags(video_id):
    DOWNLOADS_DIR = get_downloads_dir()
    tags_raw = request.form.get("tags", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    item = find_item(video_id)
    if not item:
        return jsonify({"error": "not found"}), 404
    meta_path = DOWNLOADS_DIR / video_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["tags"] = tags
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"tags": tags})


# ─── Feature: Delete video ───────────────────────────────────────────────────

@app.route("/delete/<video_id>", methods=["POST"])
def delete_video(video_id):
    DOWNLOADS_DIR = get_downloads_dir()
    item_dir = DOWNLOADS_DIR / video_id
    if item_dir.exists():
        shutil.rmtree(item_dir)
        # remove from history and favs
        history = [h for h in load_history() if h.get("id") != video_id]
        HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        favs = [f for f in load_favs() if f != video_id]
        save_favs(favs)
        flash("Vídeo removido.")
    else:
        flash("Vídeo não encontrado.")
    return redirect(url_for("index"))


# ─── Feature: Export library ─────────────────────────────────────────────────

@app.route("/export")
def export_library():
    fmt = request.args.get("fmt", "json")
    loose, playlists = scan_library()
    all_vids = loose + [v for pl in playlists for v in pl["videos"]]
    fields = ["id", "title", "channel", "duration_string", "playlist_title", "upload_date", "view_count"]
    if fmt == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for v in all_vids:
            writer.writerow({f: v.get(f, "") for f in fields})
        return Response(
            output.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=library.csv"}
        )
    else:
        export = [{f: v.get(f) for f in fields} for v in all_vids]
        return Response(
            json.dumps(export, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=library.json"}
        )


# ─── Feature: Settings ───────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if request.method == "POST":
        cfg["downloads_dir"] = request.form.get("downloads_dir", cfg["downloads_dir"])
        cfg["default_quality"] = request.form.get("default_quality", cfg["default_quality"])
        cfg["autoplay"] = request.form.get("autoplay") == "on"
        save_config(cfg)
        flash("Configurações salvas.")
        return redirect(url_for("settings"))
    return render_template("settings.html", cfg=cfg)


# ─── Feature: History page ────────────────────────────────────────────────────

@app.route("/history")
def history_page():
    history = load_history()
    items = []
    for h in history[:50]:
        item = find_item(h["id"])
        if item:
            item["watched_at"] = h.get("watched_at", "")
            items.append(item)
    cfg = load_config()
    return render_template("history.html", items=items, cfg=cfg)


if __name__ == "__main__":
    print("Offline Streamer em http://127.0.0.1:5000")
    app.run(debug=True, threaded=True)
