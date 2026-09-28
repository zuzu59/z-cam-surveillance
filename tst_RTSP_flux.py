from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import cv2
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# Keep scans bounded: a single submitted scan cannot create unlimited worker threads.
MAX_JOBS = 16
SCAN_TIMEOUT_MS = 2500
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()


@dataclass(frozen=True)
class RtspAddress:
    host: str
    port: int
    username: str | None
    password: str | None


def parse_rtsp_url(raw_url: str) -> tuple[Any, RtspAddress]:
    """Validate an RTSP URL and return its parsed parts and connection details."""
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme.lower() not in {"rtsp", "rtsps"}:
        raise ValueError("L'URL doit commencer par rtsp:// ou rtsps://.")
    if not parsed.hostname:
        raise ValueError("L'adresse doit contenir un nom d'hôte ou une adresse IP.")
    try:
        port = parsed.port or (322 if parsed.scheme.lower() == "rtsps" else 554)
    except ValueError as exc:
        raise ValueError("Le port RTSP est invalide.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("Le port doit être compris entre 1 et 65535.")
    return parsed, RtspAddress(parsed.hostname, port, parsed.username, parsed.password)


def _replace_stream_tokens(path: str, stream: int) -> str:
    """Update common stream/channel parameters embedded in camera-specific paths."""
    path = re.sub(r"(?i)(stream=)\d+", rf"\g<1>{stream}", path)
    path = re.sub(r"(?i)(channel=)\d+", rf"\g<1>{stream + 1}", path)
    return path


def generate_rtsp_variants(raw_url: str) -> list[str]:
    """Generate de-duplicated mainstream/substream URL patterns from a base URL."""
    parsed, _address = parse_rtsp_url(raw_url)
    original_path = parsed.path or "/"
    clean_path = re.sub(r"(?i)(?:user|username|password|channel|stream)=[^/&]*", "", original_path)
    clean_path = re.sub(r"/{2,}", "/", clean_path)
    clean_path = re.sub(r"[-_]{2,}", "_", clean_path).rstrip("_-") or "/"

    paths: list[str] = [original_path]
    # Preserve a camera's path syntax, changing stream/channel values when present.
    paths.extend(_replace_stream_tokens(original_path, stream) for stream in (0, 1, 2))
    paths.extend([clean_path])
    common = [
        "stream1", "stream2", "h264", "h265", "live/ch0", "live/ch1",
        "onvif1", "onvif2", "mpeg4", "Streaming/Channels/101",
        "Streaming/Channels/102", "cam/realmonitor?channel=1&subtype=0",
        "cam/realmonitor?channel=1&subtype=1", "live/main", "live/sub",
    ]
    paths.extend("/" + item for item in common)

    variants: list[str] = []
    seen: set[str] = set()
    for path in paths:
        # Some paths contain a query string as a shorthand in the list above.
        if "?" in path:
            path, query = path.split("?", 1)
        else:
            query = parsed.query if path == original_path else ""
        url = urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))
        if url not in seen:
            seen.add(url)
            variants.append(url)
    return variants


def scan_one(url: str) -> dict[str, Any]:
    """Try opening and reading one frame; always release the native capture handle.

    FFmpeg's OpenCV backend accepts open/read timeout parameters. They bound network
    waits on supported builds (opencv-python-headless includes FFmpeg on common
    platforms); `read()` is still checked explicitly because opening alone is not proof
    that a video stream is decodable.
    """
    started = time.monotonic()
    capture = None
    try:
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, SCAN_TIMEOUT_MS,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, SCAN_TIMEOUT_MS,
        ]
        capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
        if not capture.isOpened():
            return {"ok": False, "width": None, "height": None, "elapsed": round(time.monotonic() - started, 2)}
        ok, frame = capture.read()
        if not ok or frame is None:
            return {"ok": False, "width": None, "height": None, "elapsed": round(time.monotonic() - started, 2)}
        height, width = frame.shape[:2]
        return {"ok": True, "width": int(width), "height": int(height), "elapsed": round(time.monotonic() - started, 2)}
    except Exception:
        return {"ok": False, "width": None, "height": None, "elapsed": round(time.monotonic() - started, 2)}
    finally:
        if capture is not None:
            capture.release()


def run_scan(job_id: str, urls: list[str]) -> None:
    with jobs_lock:
        jobs[job_id]["status"] = "running"
    results = []
    for index, url in enumerate(urls, start=1):
        result = scan_one(url)
        results.append({"url": url, **result})
        with jobs_lock:
            jobs[job_id].update({"completed": index, "results": list(results)})
    with jobs_lock:
        jobs[job_id].update({"status": "done", "results": results})


PAGE = r'''<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RTSP Flux Finder</title>
  <style>
    :root{color-scheme:dark;--bg:#0b1118;--panel:#111b25;--line:#243342;--muted:#98a9b8;--text:#edf4fa;--blue:#68b6ff;--green:#52dda2;--red:#ff8a8a}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 70% -20%,#18314a 0,transparent 55%),var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
    main{max-width:1060px;margin:0 auto;padding:58px 24px 90px}.eyebrow{color:var(--blue);font-size:12px;font-weight:750;letter-spacing:.15em;text-transform:uppercase}
    h1{font-size:clamp(32px,5vw,52px);line-height:1.08;letter-spacing:-.045em;margin:12px 0}.intro{color:var(--muted);max-width:680px;margin:0 0 30px}
    .panel{background:linear-gradient(145deg,#131f2b,#101821);border:1px solid var(--line);border-radius:18px;padding:24px;box-shadow:0 24px 65px #0003}
    label{display:block;font-size:13px;font-weight:650;margin-bottom:8px}.inputrow{display:flex;gap:10px}input{min-width:0;flex:1;background:#0a121a;border:1px solid #344658;border-radius:10px;padding:13px 14px;color:var(--text);font:inherit;outline:none}input:focus{border-color:var(--blue);box-shadow:0 0 0 3px #68b6ff20}
    button{border:0;border-radius:10px;padding:12px 17px;background:var(--blue);color:#07111b;font:700 14px system-ui;cursor:pointer;white-space:nowrap}button:disabled{opacity:.55;cursor:wait}.hint,.status{font-size:13px;color:var(--muted);margin-top:10px}.status{min-height:22px}
    #results{margin-top:27px}.summary{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:12px}.summary h2{font-size:19px;margin:0}.count{color:var(--muted);font-size:13px}
    .table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:13px}table{border-collapse:collapse;width:100%;min-width:660px;background:#101922}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid #22313e;font-size:13px}th{color:#9fb1c0;background:#14202b;font-size:11px;text-transform:uppercase;letter-spacing:.08em}tr:last-child td{border:0}.url{font-family:ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere;color:#c7d7e4}.badge{display:inline-flex;padding:4px 8px;border-radius:20px;font-size:11px;font-weight:800;letter-spacing:.02em}.ok{color:var(--green);background:#52dda216}.bad{color:var(--red);background:#ff8a8a13}.copy{background:#233849;color:#d9edff;padding:7px 10px;font-size:12px}.success-row{background:#52dda205}.notice{color:#8ea2b2;font-size:12px;margin:12px 1px 0}
    @media(max-width:640px){main{padding:34px 16px 60px}.panel{padding:18px}.inputrow{flex-direction:column}.inputrow button{width:100%}.summary{align-items:flex-start;flex-direction:column}}
  </style>
</head>
<body><main>
  <div class="eyebrow">Outil local · caméra RTSP</div><h1>Trouvez le bon flux.</h1>
  <p class="intro">Testez les chemins vidéo courants de votre caméra pour repérer un flux principal ou secondaire compatible avec votre projet.</p>
  <section class="panel">
    <form id="scan-form"><label for="rtsp">URL RTSP de base</label><div class="inputrow"><input id="rtsp" name="rtsp_url" type="password" autocomplete="off" spellcheck="false" placeholder="rtsp://utilisateur:mot-de-passe@192.168.1.50:554/…" required><button id="submit" type="submit">Analyser les flux <span aria-hidden="true">→</span></button></div></form>
    <div class="hint">Les identifiants sont masqués à l’écran. Le scan s’exécute sur ce serveur et aucune URL n’est enregistrée sur disque.</div><div class="status" id="status" role="status" aria-live="polite"></div>
  </section>
  <section id="results" hidden><div class="summary"><h2>Résultats du scan</h2><div class="count" id="count"></div></div><div class="table-wrap"><table><thead><tr><th>État</th><th>URL testée</th><th>Résolution</th><th></th></tr></thead><tbody id="result-rows"></tbody></table></div><p class="notice">Les URLs sont masquées dans cette page. Utilisez « Copier » pour récupérer l’URL complète; le presse-papiers peut contenir les identifiants caméra.</p></section>
</main>
<script>
const form=document.querySelector('#scan-form'), input=document.querySelector('#rtsp'), button=document.querySelector('#submit'), statusEl=document.querySelector('#status');
const redact=(url)=>url.replace(/(rtsp(?:s)?:\/\/)([^/@]+)@/i,'$1••••••@').replace(/(password=)[^_&/?]*/ig,'$1••••••');
form.addEventListener('submit',async(e)=>{e.preventDefault();const rtsp_url=input.value.trim();if(!rtsp_url)return;button.disabled=true;input.value='';statusEl.textContent='Préparation du scan…';document.querySelector('#results').hidden=false;document.querySelector('#result-rows').replaceChildren();
 try{const response=await fetch('/api/scans',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rtsp_url})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Impossible de démarrer le scan.');await poll(data.id)}catch(error){statusEl.textContent=error.message;button.disabled=false}
});
async function poll(id){const res=await fetch('/api/scans/'+id),job=await res.json();if(!res.ok)throw new Error(job.error||'Erreur de scan.');statusEl.textContent=job.status==='done'?'Scan terminé.':`Test des variantes… ${job.completed} / ${job.total}`;render(job.results||[]);document.querySelector('#count').textContent=`${(job.results||[]).filter(x=>x.ok).length} flux fonctionnel(s) · ${job.completed} / ${job.total} testés`;
 if(job.status==='done'){button.disabled=false;return}setTimeout(()=>poll(id).catch(e=>{statusEl.textContent=e.message;button.disabled=false}),700)}
function render(results){const body=document.querySelector('#result-rows');body.replaceChildren(...results.map(item=>{const tr=document.createElement('tr');if(item.ok)tr.className='success-row';const state=document.createElement('td'),badge=document.createElement('span');badge.className='badge '+(item.ok?'ok':'bad');badge.textContent=item.ok?'FONCTIONNELLE':'INACCESSIBLE';state.append(badge);
 const url=document.createElement('td');url.className='url';url.textContent=redact(item.url);const resolution=document.createElement('td');resolution.textContent=item.ok?`${item.width} × ${item.height}`:'—';const action=document.createElement('td');if(item.ok){const copy=document.createElement('button');copy.className='copy';copy.textContent='Copier';copy.addEventListener('click',async()=>{await navigator.clipboard.writeText(item.url);copy.textContent='Copié';setTimeout(()=>copy.textContent='Copier',1200)});action.append(copy)}tr.append(state,url,resolution,action);return tr}))}
</script></body></html>'''


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/api/scans")
def start_scan():
    payload = request.get_json(silent=True) or {}
    raw_url = payload.get("rtsp_url", "")
    if not isinstance(raw_url, str) or not raw_url.strip():
        return jsonify(error="Saisissez une URL RTSP."), 400
    try:
        urls = generate_rtsp_variants(raw_url)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    with jobs_lock:
        # Expire finished state opportunistically and cap memory use.
        for key in list(jobs):
            if jobs[key]["status"] == "done" and time.time() - jobs[key]["created"] > 1800:
                del jobs[key]
        if len(jobs) >= MAX_JOBS:
            return jsonify(error="Trop de scans actifs. Réessayez dans quelques instants."), 429
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"status": "queued", "created": time.time(), "completed": 0, "total": len(urls), "results": []}
    threading.Thread(target=run_scan, args=(job_id, urls), daemon=True).start()
    return jsonify(id=job_id), 202


@app.get("/api/scans/<job_id>")
def scan_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify(error="Scan introuvable ou expiré."), 404
        # Return only data needed by the UI; the submitted base URL itself is never kept.
        return jsonify({key: job[key] for key in ("status", "completed", "total", "results")})


if __name__ == "__main__":
    # Debug/reloader is deliberately disabled so camera credentials aren't echoed in tracebacks.
    app.run(host="0.0.0.0", port=8090, debug=False, threaded=True)
