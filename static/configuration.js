(() => {
  const $ = (id) => document.getElementById(id);
  const form = $("config-form");
  const urlFields = [
    ["low-resolution-url", "clear-low-url"],
    ["high-resolution-url", "clear-high-url"],
  ];
  const textFields = ["output-dir", "model", "labels", "host"];
  const numberFields = ["threshold", "interval", "no-detection-seconds", "port"];
  const restartLabels = { model: "modèle", labels: "labels", host: "adresse d’écoute", port: "port" };

  const showFeedback = (message, type = "") => {
    const feedback = $("config-feedback");
    feedback.textContent = message;
    feedback.className = `form-feedback${type ? ` ${type}` : ""}`;
  };
  const hydrate = (config) => {
    numberFields.forEach((id) => {
      const key = id.replaceAll("-", "_");
      $(id).value = config[key];
    });
    textFields.forEach((id) => $(id).value = config[id.replaceAll("-", "_")]);
    $("recording-enabled").checked = config.recording_enabled;
    $("low-url-status").textContent = config.low_stream_configured ? "URL mémorisée localement (valeur masquée)" : "Aucune URL mémorisée";
    $("high-url-status").textContent = config.high_stream_configured ? "URL mémorisée localement (valeur masquée)" : "Aucune URL mémorisée";
  };
  const loadConfig = async () => {
    try {
      const response = await fetch("/api/config", { cache: "no-store" });
      if (!response.ok) throw new Error("Configuration indisponible.");
      const config = await response.json();
      hydrate(config);
      const pending = config.restart_required_fields || [];
      if (pending.length) {
        const names = pending.map((field) => restartLabels[field] || field);
        showFeedback(`Redémarrage requis pour appliquer : ${names.join(", ")}.`);
      }
    } catch (error) {
      showFeedback(error.message || "Impossible de charger la configuration.", "error");
    }
  };

  urlFields.forEach(([inputId, clearId]) => {
    $(inputId).addEventListener("input", () => {
      if ($(inputId).value.trim()) $(clearId).checked = false;
    });
    $(clearId).addEventListener("change", () => {
      $(inputId).disabled = $(clearId).checked;
      if ($(clearId).checked) $(inputId).value = "";
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const payload = {
      threshold: Number($("threshold").value),
      interval: Number($("interval").value),
      no_detection_seconds: Number($("no-detection-seconds").value),
      recording_enabled: $("recording-enabled").checked,
      output_dir: $("output-dir").value.trim(),
      model: $("model").value.trim(),
      labels: $("labels").value.trim(),
      host: $("host").value.trim(),
      port: Number($("port").value),
      clear_low_resolution_url: $("clear-low-url").checked,
      clear_high_resolution_url: $("clear-high-url").checked,
    };
    const lowUrl = $("low-resolution-url").value.trim();
    const highUrl = $("high-resolution-url").value.trim();
    if (lowUrl) payload.low_resolution_url = lowUrl;
    if (highUrl) payload.high_resolution_url = highUrl;
    showFeedback("Validation et sauvegarde de tous les paramètres…");
    button.disabled = true;
    try {
      const response = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Configuration refusée.");
      hydrate(result.config);
      urlFields.forEach(([inputId, clearId]) => {
        $(inputId).value = "";
        $(inputId).disabled = false;
        $(clearId).checked = false;
      });
      const pending = result.config.restart_required_fields || [];
      const names = pending.map((field) => restartLabels[field] || field);
      const suffix = names.length ? ` Redémarrage requis pour appliquer : ${names.join(", ")}.` : "";
      showFeedback(`Configuration enregistrée dans le fichier JSON.${suffix}`, "success");
    } catch (error) {
      showFeedback(error.message || "Impossible d’enregistrer la configuration.", "error");
    } finally {
      button.disabled = false;
    }
  });

  loadConfig();
})();
