"""Web application routes for local RTSP surveillance."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from app_version import APP_VERSION
from flask import Flask, Response, abort, jsonify, render_template, request, send_file, stream_with_context
from surveillance_recordings import (
    list_recordings,
    recording_details,
    recordings_directory,
    resolve_recording,
    resolve_recording_sidecar,
)


def _launch_start_script() -> None:
    start_script = Path(__file__).resolve().with_name("start.sh")
    runtime_dir = start_script.parent / ".runtime"
    try:
        runtime_dir.mkdir(mode=0o700, exist_ok=True)
        runtime_dir.chmod(0o700)
        log_path = runtime_dir / "surveillance.log"
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(log_path, 0o600)
        with os.fdopen(log_fd, "ab", buffering=0) as log_file:
            subprocess.Popen(
                [str(start_script)],
                cwd=start_script.parent,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError:
        logging.exception("Impossible de relancer l’application avec start.sh")


def _schedule_application_restart() -> None:
    timer = threading.Timer(1.25, _launch_start_script)
    timer.daemon = True
    timer.start()


def create_app(engine: Any) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024
    app.config["SERVER_INSTANCE_ID"] = uuid.uuid4().hex

    @app.context_processor
    def inject_app_info() -> dict[str, str]:
        return {"app_version": APP_VERSION}

    @app.get("/")
    def dashboard() -> str:
        return render_template("surveillance_dashboard.html", active_page="home")

    @app.get("/configuration")
    def configuration() -> str:
        return render_template("configuration.html", active_page="configuration")

    @app.get("/help")
    def help_page() -> str:
        return render_template("help.html", active_page="help")

    @app.get("/about")
    def about_page() -> str:
        return render_template("about.html", active_page="about")

    @app.get("/recordings")
    def recordings_page() -> str:
        return render_template("recordings.html", active_page="recordings")

    def configured_recordings_directory() -> Path:
        lock = getattr(engine, "lock", None)
        if lock is None:
            output_dir = engine.output_dir
        else:
            with lock:
                output_dir = engine.output_dir
        return recordings_directory(output_dir)

    @app.get("/api/recordings")
    def recordings_index() -> Any:
        query = request.args.get("q", "")
        if len(query) > 160:
            return jsonify(ok=False, error="Le filtre est trop long."), 400
        try:
            page = int(request.args.get("page", "0"))
            if page < 0:
                raise ValueError
            directory = configured_recordings_directory()
            result = list_recordings(directory, query, page)
            result["directory_label"] = directory.name or "Enregistrements"
        except (OSError, ValueError):
            return jsonify(ok=False, error="Impossible de lire le dossier des enregistrements."), 400
        return jsonify(result)

    @app.get("/api/recordings/<path:filename>")
    def recording_info(filename: str) -> Any:
        details = recording_details(configured_recordings_directory(), filename)
        if details is None:
            abort(404)
        return jsonify(details)

    @app.get("/api/recordings/<path:filename>/video")
    def recording_video(filename: str) -> Response:
        path = resolve_recording(configured_recordings_directory(), filename)
        if path is None:
            abort(404)
        return send_file(path, mimetype="video/mp4", as_attachment=False,
                         download_name=path.name, conditional=True, max_age=0)

    @app.get("/api/recordings/<path:filename>/evidence")
    def recording_evidence(filename: str) -> Response:
        path = resolve_recording_sidecar(configured_recordings_directory(), filename, ".jpg")
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/jpeg", as_attachment=False,
                         download_name=path.name, conditional=True, max_age=0)

    @app.delete("/api/recordings/<path:filename>")
    def delete_recording(filename: str) -> Any:
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return jsonify(ok=False, error="Demande de suppression invalide."), 403
        path = resolve_recording(configured_recordings_directory(), filename)
        if path is None:
            abort(404)
        archive_path = path.parent / ".originals" / path.name
        try:
            has_archived_original = archive_path.is_file() and os.path.samefile(path, archive_path)
            for extension in (".jpg", ".txt"):
                sidecar = resolve_recording_sidecar(path.parent, filename, extension)
                if sidecar is not None:
                    sidecar.unlink()
            path.unlink()
            if has_archived_original:
                archive_path.unlink(missing_ok=True)
        except OSError:
            return jsonify(ok=False, error="Impossible de supprimer toutes les versions du fichier."), 500
        return jsonify(ok=True, name=path.name)

    @app.get("/api/config")
    def get_config() -> Any:
        return jsonify(engine.config_snapshot())

    @app.get("/api/state")
    def state() -> Any:
        # The stream URL is intentionally never included in status responses.
        return jsonify(
            **engine.status_snapshot(),
            server_pid=os.getpid(),
            server_instance_id=app.config["SERVER_INSTANCE_ID"],
        )

    @app.post("/api/config/reveal")
    def reveal_configured_url() -> Any:
        # The custom header prevents a cross-origin HTML form from reading secrets.
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return jsonify(ok=False, error="Demande de révélation invalide."), 403
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get("source") not in {"low", "high"}:
            return jsonify(ok=False, error="Source vidéo invalide."), 400
        url = engine.reveal_stream_url(payload["source"])
        if not url:
            return jsonify(ok=False, error="Aucune URL enregistrée pour cette source."), 404
        return jsonify(url=url)

    @app.post("/api/config")
    def configure() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(ok=False, error="Configuration JSON invalide."), 400
        restart_application = payload.get("restart_application", False)
        if not isinstance(restart_application, bool):
            return jsonify(ok=False, error="La demande de redémarrage doit être booléenne."), 400
        settings = {key: value for key, value in payload.items() if key != "restart_application"}
        try:
            engine.update_settings(settings)
        except (TypeError, ValueError) as exc:
            return jsonify(ok=False, error=str(exc)), 400
        if restart_application:
            _schedule_application_restart()
        return jsonify(
            ok=True,
            settings=engine.settings_snapshot(),
            config=engine.config_snapshot(),
            restart_scheduled=restart_application,
            server_pid=os.getpid(),
            server_instance_id=app.config["SERVER_INSTANCE_ID"],
        )

    @app.get("/video_feed")
    def video_feed() -> Response:
        @stream_with_context
        def frames():
            sequence = -1
            while True:
                next_sequence, jpeg = engine.preview_snapshot()
                if jpeg is None or next_sequence == sequence:
                    import time
                    time.sleep(0.05)
                    continue
                sequence = next_sequence
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode("ascii") + b"\r\n\r\n" + jpeg + b"\r\n")

        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

    @app.after_request
    def disable_cache(response: Any) -> Any:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
