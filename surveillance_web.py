"""Web application routes for local RTSP surveillance."""
from __future__ import annotations

from typing import Any

from app_version import APP_VERSION
from flask import Flask, Response, jsonify, render_template, request, stream_with_context


def create_app(engine: Any) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024

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

    @app.get("/api/config")
    def get_config() -> Any:
        return jsonify(engine.config_snapshot())

    @app.get("/api/state")
    def state() -> Any:
        # The stream URL is intentionally never included in API responses.
        return jsonify(engine.status_snapshot())

    @app.post("/api/config")
    def configure() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(ok=False, error="Configuration JSON invalide."), 400
        try:
            engine.update_settings(payload)
        except (TypeError, ValueError) as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True, settings=engine.settings_snapshot(), config=engine.config_snapshot())

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
