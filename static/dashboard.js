(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("settings-form");
  const lowUrlInput = $("low-stream-url");
  const highUrlInput = $("high-stream-url");
  const threshold = $("threshold");
  const interval = $("interval");
  const cooldown = $("cooldown");
  const recordingEnabled = $("recording-enabled");
  const overlayToggle = $("overlay-toggle");
  const labels = { person: "Personne", car: "Voiture", bicycle: "Vélo", motorcycle: "Moto", dog: "Chien", cat: "Chat" };
  let newestSequence = -1;

  const pct = (value) => `${Math.round(Number(value) * 100)} %`;
  const setRangeProgress = () => {
    const min = Number(threshold.min);
    const max = Number(threshold.max);
    const value = ((Number(threshold.value) - min) / (max - min)) * 100;
    threshold.style.setProperty("--range-progress", `${value}%`);
    $("threshold-value").textContent = pct(threshold.value);
  };
  const setConnection = (connected, configured) => {
    const pill = $("connection-pill");
    pill.classList.toggle("connected", connected);
    pill.classList.toggle("error", !connected && !configured);
    $("connection-text").textContent = connected ? "Flux connecté" : configured ? "Connexion au flux…" : "Source non configurée";
  };
  const setSourceState = (prefix, configured) => {
    $(`${prefix}-source-state-text`).textContent = configured ? "URL mémorisée localement" : "URL non configurée";
    $(`${prefix}-source-dot`).classList.toggle("inactive", !configured);
  };
  const renderDetections = (detections) => {
    const list = $("detection-list");
    const count = detections.length;
    $("detection-count").textContent = String(count);
    list.replaceChildren();
    if (!count) {
      const empty = document.createElement("div");
      empty.className = "quiet-empty";
      const dot = document.createElement("span");
      dot.className = "quiet-empty-dot";
      empty.append(dot, document.createTextNode("Aucune détection sur la dernière image analysée."));
      list.append(empty);
      $("detection-overlay").replaceChildren();
      return;
    }
    const items = document.createElement("div");
    items.className = "detection-items";
    const svg = $("detection-overlay");
    const svgNodes = [];
    detections.forEach((detection) => {
      const tag = document.createElement("div");
      tag.className = "detection-tag";
      const name = document.createElement("strong");
      name.textContent = labels[detection.label] || detection.label;
      const confidence = document.createElement("span");
      confidence.textContent = pct(detection.confidence);
      tag.append(name, confidence);
      items.append(tag);

      const [yMin, xMin, yMax, xMax] = detection.box.map((coordinate) => Math.max(0, Math.min(1, Number(coordinate))));
      const rectangle = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rectangle.setAttribute("x", xMin);
      rectangle.setAttribute("y", yMin);
      rectangle.setAttribute("width", Math.max(0, xMax - xMin));
      rectangle.setAttribute("height", Math.max(0, yMax - yMin));
      svgNodes.push(rectangle);
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      const textX = Math.min(xMax, 0.97);
      const textY = yMin < 0.05 ? Math.min(yMin + 0.035, 0.08) : yMin - 0.008;
      label.setAttribute("x", textX);
      label.setAttribute("y", textY);
      label.setAttribute("text-anchor", "end");
      label.textContent = `${labels[detection.label] || detection.label} ${pct(detection.confidence)}`;
      svgNodes.push(label);
    });
    list.append(items);
    svg.replaceChildren(...svgNodes);
    svg.hidden = !overlayToggle.checked;
  };
  const hydrateSettings = (settings) => {
    threshold.value = settings.threshold;
    interval.value = settings.interval;
    cooldown.value = settings.no_detection_seconds;
    recordingEnabled.checked = settings.recording_enabled;
    setRangeProgress();
  };

  threshold.addEventListener("input", setRangeProgress);
  overlayToggle.addEventListener("change", () => {
    $("detection-overlay").hidden = !overlayToggle.checked;
  });
  document.querySelectorAll(".stepper").forEach((button) => {
    button.addEventListener("click", () => {
      const input = $(button.dataset.target);
      const step = Number(button.dataset.step);
      const value = Math.max(Number(input.min), Math.min(Number(input.max), Number(input.value) + step));
      input.value = Number(value.toFixed(1));
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const feedback = $("form-feedback");
    const lowUrl = lowUrlInput.value.trim();
    const highUrl = highUrlInput.value.trim();
    const payload = {
      threshold: Number(threshold.value),
      interval: Number(interval.value),
      no_detection_seconds: Number(cooldown.value),
      recording_enabled: recordingEnabled.checked,
    };
    if (lowUrl) payload.low_resolution_url = lowUrl;
    if (highUrl) payload.high_resolution_url = highUrl;
    const sourceUpdated = Boolean(lowUrl || highUrl);
    feedback.textContent = "Application et sauvegarde des réglages…";
    feedback.className = "form-feedback";
    button.disabled = true;
    try {
      const response = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Réglages refusés.");
      hydrateSettings(result.settings);
      lowUrlInput.value = "";
      highUrlInput.value = "";
      feedback.textContent = sourceUpdated
        ? "Flux mis à jour et sauvegardés. Reconnexion en cours…"
        : "Réglages sauvegardés dans le fichier local.";
      feedback.classList.add("success");
      await refreshState();
    } catch (error) {
      feedback.textContent = error.message || "Impossible d’appliquer les réglages.";
      feedback.classList.add("error");
    } finally {
      button.disabled = false;
    }
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
      if (settings && newestSequence < 0) hydrateSettings(settings);
      if (state.preview_sequence !== newestSequence) {
        newestSequence = state.preview_sequence;
        const image = $("preview-image");
        if (newestSequence > 0) {
          image.hidden = false;
          $("empty-state").hidden = true;
          const resolution = state.width && state.height ? `${state.width} × ${state.height}` : "Image analysée";
          $("preview-meta").textContent = `${resolution} · aperçu actualisé à chaque analyse`;
          $("frame-label").textContent = resolution;
        }
      }
      $("sample-rate").textContent = `Analyse toutes les ${Number(settings.interval).toLocaleString("fr-FR")} s`;
      $("stat-resolution").textContent = state.width ? `${state.width} × ${state.height}` : "—";
      $("stat-fps").textContent = state.connected ? `${state.capture_fps} i/s` : "—";
      const recording = $("stat-recording");
      recording.textContent = state.recording ? "ACTIF" : "ARRÊTÉ";
      recording.classList.toggle("active", state.recording);
      renderDetections(state.detections || []);
    } catch {
      setConnection(false, false);
      $("connection-text").textContent = "Tableau indisponible";
    }
  }

  setRangeProgress();
  refreshState();
  window.setInterval(refreshState, 1000);
})();
