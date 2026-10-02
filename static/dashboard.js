(() => {
  const $ = (id) => document.getElementById(id);
  const labels = { person: "Personne", car: "Voiture", bicycle: "Vélo", motorcycle: "Moto", dog: "Chien", cat: "Chat" };
  let newestSequence = -1;

  const percent = (value) => `${Math.round(Number(value) * 100)} %`;
  const alignDetectionOverlay = () => {
    const image = $("preview-image");
    const frame = $("camera-frame");
    if (image.hidden) return;
    const imageRect = image.getBoundingClientRect();
    if (!imageRect.width || !imageRect.height) return;
    const frameRect = frame.getBoundingClientRect();
    Object.assign($("detection-overlay").style, {
      left: `${imageRect.left - frameRect.left}px`,
      top: `${imageRect.top - frameRect.top}px`,
      right: "auto",
      bottom: "auto",
      width: `${imageRect.width}px`,
      height: `${imageRect.height}px`,
    });
  };
  if ("ResizeObserver" in window) {
    const frameObserver = new ResizeObserver(alignDetectionOverlay);
    frameObserver.observe($("camera-frame"));
    frameObserver.observe($("preview-image"));
  } else {
    window.addEventListener("resize", alignDetectionOverlay);
  }
  const setConnection = (connected, configured) => {
    const pill = $("connection-pill");
    pill.classList.toggle("connected", connected);
    pill.classList.toggle("error", !connected && !configured);
    $("connection-text").textContent = connected ? "Flux connecté" : configured ? "Connexion au flux…" : "Source non configurée";
  };
  const setSourceState = (prefix, configured) => {
    $(`${prefix}-source-state-text`).textContent = configured ? "Configuré" : "Non configuré";
    $(`${prefix}-source-dot`).classList.toggle("inactive", !configured);
  };
  const renderDetections = (detections) => {
    const list = $("detection-list");
    $("detection-count").textContent = String(detections.length);
    list.replaceChildren();
    const svg = $("detection-overlay");
    svg.replaceChildren();
    if (!detections.length) {
      const empty = document.createElement("div");
      empty.className = "quiet-empty";
      const dot = document.createElement("span");
      dot.className = "quiet-empty-dot";
      empty.append(dot, document.createTextNode("Aucune détection sur la dernière image analysée."));
      list.append(empty);
      return;
    }
    const items = document.createElement("div");
    items.className = "detection-items";
    const svgNodes = [];
    detections.forEach((detection) => {
      const name = labels[detection.label] || detection.label;
      const tag = document.createElement("div");
      tag.className = "detection-tag";
      const title = document.createElement("strong");
      title.textContent = name;
      const confidence = document.createElement("span");
      confidence.textContent = percent(detection.confidence);
      tag.append(title, confidence);
      items.append(tag);

      const [yMin, xMin, yMax, xMax] = detection.box.map((coordinate) => Math.max(0, Math.min(1, Number(coordinate))));
      const rectangle = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rectangle.setAttribute("x", xMin);
      rectangle.setAttribute("y", yMin);
      rectangle.setAttribute("width", Math.max(0, xMax - xMin));
      rectangle.setAttribute("height", Math.max(0, yMax - yMin));
      svgNodes.push(rectangle);
      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", Math.min(xMax, 0.97));
      text.setAttribute("y", yMin < 0.05 ? Math.min(yMin + 0.035, 0.08) : yMin - 0.008);
      text.setAttribute("text-anchor", "end");
      text.textContent = `${name} ${percent(detection.confidence)}`;
      svgNodes.push(text);
    });
    list.append(items);
    svg.replaceChildren(...svgNodes);
    svg.hidden = !$("overlay-toggle").checked;
  };

  $("overlay-toggle").addEventListener("change", (event) => {
    $("detection-overlay").hidden = !event.target.checked;
  });

  async function refreshState() {
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (!response.ok) throw new Error("État indisponible");
      const state = await response.json();
      const settings = state.settings;
      setConnection(state.connected, settings.low_stream_configured);
      setSourceState("low", settings.low_stream_configured);
      setSourceState("high", settings.high_stream_configured);
      if (state.preview_sequence !== newestSequence) {
        newestSequence = state.preview_sequence;
        if (newestSequence > 0) {
          $("preview-image").hidden = false;
          $("empty-state").hidden = true;
          requestAnimationFrame(alignDetectionOverlay);
          const resolution = state.width && state.height ? `${state.width} × ${state.height}` : "Image analysée";
          $("preview-meta").textContent = `${resolution} · aperçu actualisé à chaque analyse`;
          $("frame-label").textContent = resolution;
        }
      }
      $("sample-rate").textContent = `Analyse toutes les ${Number(settings.interval).toLocaleString("fr-FR")} s`;
      $("stat-interval").textContent = `${Number(settings.interval).toLocaleString("fr-FR")} s`;
      $("stat-resolution").textContent = state.width ? `${state.width} × ${state.height}` : "—";
      $("stat-fps").textContent = state.connected ? `${state.capture_fps} i/s` : "—";
      const recording = $("stat-recording");
      recording.textContent = state.recording ? "ACTIF" : settings.recording_enabled ? "EN ATTENTE" : "DÉSACTIVÉ";
      recording.classList.toggle("active", state.recording);
      renderDetections(state.detections || []);
    } catch {
      setConnection(false, false);
      $("connection-text").textContent = "Tableau indisponible";
    }
  }

  refreshState();
  window.setInterval(refreshState, 1000);
})();
