(() => {
  const $ = (id) => document.getElementById(id);
  const search = $("recording-search");
  const list = $("recording-list");
  const player = $("recording-player");
  const speedButtons = [...document.querySelectorAll("[data-speed]")];
  const detailFields = {
    name: "detail-name",
    size: "detail-size",
    date: "detail-date",
    duration: "detail-duration",
    resolution: "detail-resolution",
    codec: "detail-codec",
    frameRate: "detail-frame-rate",
    bitrate: "detail-bitrate",
  };
  let query = "";
  let page = 0;
  let pageSize = 40;
  let total = 0;
  let files = [];
  let selectedName = null;
  let selectedGlobalIndex = -1;
  let selectionVersion = 0;
  let searchTimer = null;
  let listRequestVersion = 0;
  let playbackSpeed = 1;

  const formatBytes = (value) => {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "—";
    if (bytes < 1000) return `${bytes} o`;
    const units = ["ko", "Mo", "Go", "To"];
    let size = bytes / 1000;
    let unit = 0;
    while (size >= 1000 && unit < units.length - 1) {
      size /= 1000;
      unit += 1;
    }
    return `${new Intl.NumberFormat("fr-FR", { maximumFractionDigits: size >= 100 ? 0 : 1 }).format(size)} ${units[unit]}`;
  };
  const formatDate = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Date inconnue" : new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" }).format(date);
  };
  const formatDuration = (value) => {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) return "Non disponible";
    const whole = Math.floor(seconds);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const remaining = whole % 60;
    return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}` : `${minutes}:${String(remaining).padStart(2, "0")}`;
  };
  const formatFrameRate = (value) => {
    if (typeof value !== "string") return "Non disponible";
    const [numerator, denominator] = value.split("/").map(Number);
    if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator === 0) return "Non disponible";
    return `${new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 }).format(numerator / denominator)} i/s`;
  };
  const formatBitrate = (value) => {
    const bits = Number(value);
    if (!Number.isFinite(bits) || bits <= 0) return "Non disponible";
    return `${new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 }).format(bits / 1000)} kbit/s`;
  };
  const setViewerFeedback = (message, state = "") => {
    const feedback = $("viewer-feedback");
    feedback.textContent = message;
    feedback.className = `viewer-feedback${state ? ` ${state}` : ""}`;
  };
  const resetDetails = () => {
    Object.values(detailFields).forEach((id) => { $(id).textContent = "—"; });
    $("details-state").textContent = "En attente d’une sélection";
  };
  const resetEvidence = () => {
    const message = $("evidence-message");
    const labels = $("evidence-labels");
    const image = $("evidence-image");
    if (!message || !labels || !image) return;
    message.textContent = "Sélectionnez un clip pour afficher les détections associées.";
    labels.replaceChildren();
    image.removeAttribute("src");
    image.hidden = true;
  };
  const updateNavigation = () => {
    const hasSelection = selectedGlobalIndex >= 0 && selectedGlobalIndex < total;
    $("previous-file").disabled = !hasSelection || selectedGlobalIndex >= total - 1;
    $("next-file").disabled = !hasSelection || selectedGlobalIndex === 0;
    $("delete-trigger").disabled = !selectedName;
  };
  const renderFiles = () => {
    list.replaceChildren();
    list.setAttribute("aria-busy", "false");
    for (const [index, file] of files.entries()) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "recording-item";
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(file.name === selectedName));
      item.dataset.index = String(index);
      const name = document.createElement("span");
      name.className = "recording-item-name";
      name.textContent = file.name;
      const size = document.createElement("span");
      size.className = "recording-item-size";
      size.textContent = formatBytes(file.size_bytes);
      const date = document.createElement("span");
      date.className = "recording-item-date";
      date.textContent = formatDate(file.modified_at);
      item.append(name, size, date);
      item.addEventListener("click", () => selectFile(file, page * pageSize + index));
      list.append(item);
    }
    if (!files.length) {
      const empty = document.createElement("div");
      empty.className = "recording-list-empty";
      const title = document.createElement("strong");
      const description = document.createElement("span");
      if (query) {
        title.textContent = "Aucun résultat";
        description.textContent = "Essayez un autre nom ou une autre date.";
      } else {
        title.textContent = "Aucun clip MP4";
        description.textContent = "Les nouveaux enregistrements apparaîtront ici.";
      }
      empty.append(title, description);
      list.append(empty);
    }
    const pages = Math.max(1, Math.ceil(total / pageSize));
    $("library-count").textContent = `${new Intl.NumberFormat("fr-FR").format(total)} ${total === 1 ? "fichier" : "fichiers"}`;
    $("page-indicator").textContent = `${page + 1} / ${pages}`;
    $("previous-page").disabled = page <= 0 || total === 0;
    $("next-page").disabled = page >= pages - 1 || total === 0;
    updateNavigation();
  };
  const clearSelection = () => {
    selectionVersion += 1;
    selectedName = null;
    selectedGlobalIndex = -1;
    $("selected-name").textContent = "Sélectionnez un enregistrement";
    $("selected-subtitle").textContent = "Choisissez un fichier dans la bibliothèque.";
    player.pause();
    player.removeAttribute("src");
    player.load();
    player.hidden = true;
    $("player-empty").hidden = false;
    $("delete-confirmation").hidden = true;
    resetDetails();
    resetEvidence();
    renderFiles();
  };
  const loadPage = async ({ clear = true } = {}) => {
    const requestVersion = ++listRequestVersion;
    if (clear) clearSelection();
    list.setAttribute("aria-busy", "true");
    $("list-message").classList.remove("error");
    $("list-message").textContent = "Recherche des enregistrements…";
    try {
      const params = new URLSearchParams({ q: query, page: String(page) });
      const response = await fetch(`/api/recordings?${params.toString()}`, { cache: "no-store" });
      const result = await response.json();
      if (requestVersion !== listRequestVersion) return;
      if (!response.ok) throw new Error(result.error || "Impossible de lire la bibliothèque.");
      total = Number(result.total) || 0;
      pageSize = Number(result.page_size) || 40;
      page = Number(result.page) || 0;
      files = Array.isArray(result.files) ? result.files : [];
      if (result.directory_label) $("recording-folder").textContent = `Dossier · ${result.directory_label}`;
      $("list-message").textContent = query ? `Résultats pour « ${query} »` : "Fichiers MP4 du dossier configuré";
      renderFiles();
    } catch (error) {
      if (requestVersion !== listRequestVersion) return;
      files = [];
      total = 0;
      list.setAttribute("aria-busy", "false");
      $("list-message").textContent = error.message || "Impossible de charger les enregistrements.";
      $("list-message").classList.add("error");
      renderFiles();
    }
  };
  const selectFile = async (file, globalIndex, { autoplay = false } = {}) => {
    const version = ++selectionVersion;
    selectedName = file.name;
    selectedGlobalIndex = globalIndex;
    $("selected-name").textContent = file.name;
    $("selected-subtitle").textContent = `${formatBytes(file.size_bytes)} · ${formatDate(file.modified_at)}`;
    $("details-state").textContent = "Chargement des métadonnées…";
    $("detail-name").textContent = file.name;
    $("detail-size").textContent = formatBytes(file.size_bytes);
    $("detail-date").textContent = formatDate(file.modified_at);
    ["detail-duration", "detail-resolution", "detail-codec", "detail-frame-rate", "detail-bitrate"].forEach((id) => { $(id).textContent = "Lecture des métadonnées…"; });
    $("delete-confirmation").hidden = true;
    setViewerFeedback("");
    resetEvidence();
    player.pause();
    player.src = `/api/recordings/${encodeURIComponent(file.name)}/video`;
    player.playbackRate = playbackSpeed;
    player.hidden = false;
    $("player-empty").hidden = true;
    player.load();
    if (autoplay) {
      player.play().catch((error) => {
        if (version !== selectionVersion || error.name === "AbortError") return;
        const message = error.name === "NotAllowedError"
          ? "Le navigateur a bloqué la lecture automatique. Lancez-la depuis le lecteur."
          : "La lecture automatique n’a pas pu démarrer.";
        setViewerFeedback(message, "error");
      });
    }
    renderFiles();
    try {
      const response = await fetch(`/api/recordings/${encodeURIComponent(file.name)}`, { cache: "no-store" });
      const details = await response.json();
      if (!response.ok) throw new Error(details.error || "Impossible de lire les détails du fichier.");
      if (version !== selectionVersion) return;
      $("detail-duration").textContent = formatDuration(details.duration_seconds);
      $("detail-resolution").textContent = details.width && details.height ? `${details.width} × ${details.height}` : "Non disponible";
      $("detail-codec").textContent = details.codec || "Non disponible";
      $("detail-frame-rate").textContent = formatFrameRate(details.frame_rate);
      $("detail-bitrate").textContent = formatBitrate(details.bit_rate);
      const labels = Array.isArray(details.event_labels) ? details.event_labels : [];
      const labelContainer = $("evidence-labels");
      const evidenceMessage = $("evidence-message");
      const evidenceImage = $("evidence-image");
      if (labelContainer && evidenceMessage && evidenceImage) {
        labelContainer.replaceChildren();
        for (const label of labels) {
          const tag = document.createElement("span");
          tag.className = "evidence-label";
          tag.setAttribute("role", "listitem");
          tag.textContent = label;
          labelContainer.append(tag);
        }
        if (details.evidence_available) {
          evidenceImage.src = `/api/recordings/${encodeURIComponent(file.name)}/evidence`;
          evidenceImage.hidden = false;
          evidenceMessage.textContent = labels.length
            ? "Image du premier instant détecté. Les labels listés ont été observés pendant l’enregistrement."
            : "Image annotée disponible; aucun label lisible dans le fichier associé.";
        } else {
          evidenceMessage.textContent = labels.length
            ? "Labels détectés pendant l’enregistrement; image annotée indisponible."
            : "Aucune image ou liste de labels associée à ce clip.";
        }
      }
      $("details-state").textContent = "Métadonnées du fichier";
    } catch (error) {
      if (version !== selectionVersion) return;
      $("details-state").textContent = "Détails partiels";
      ["detail-duration", "detail-resolution", "detail-codec", "detail-frame-rate", "detail-bitrate"].forEach((id) => { $(id).textContent = "Non disponible"; });
      setViewerFeedback(error.message || "Les métadonnées détaillées ne sont pas disponibles.", "error");
    }
    updateNavigation();
  };
  const moveSelection = async (step) => {
    const targetIndex = selectedGlobalIndex + step;
    if (targetIndex < 0 || targetIndex >= total) return;
    const targetPage = Math.floor(targetIndex / pageSize);
    if (targetPage !== page) {
      page = targetPage;
      await loadPage({ clear: false });
    }
    const item = files[targetIndex - page * pageSize];
    if (item) await selectFile(item, targetIndex, { autoplay: true });
  };
  const deleteSelected = async () => {
    if (!selectedName) return;
    const name = selectedName;
    const button = $("confirm-delete");
    button.disabled = true;
    button.textContent = "Suppression…";
    try {
      const response = await fetch(`/api/recordings/${encodeURIComponent(name)}`, {
        method: "DELETE",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Impossible de supprimer ce fichier.");
      total = Math.max(0, total - 1);
      page = Math.min(page, Math.max(0, Math.ceil(total / pageSize) - 1));
      clearSelection();
      await loadPage({ clear: false });
      setViewerFeedback(`Fichier supprimé : ${result.name || name}`, "success");
      renderFiles();
    } catch (error) {
      setViewerFeedback(error.message || "La suppression a échoué.", "error");
      $("delete-confirmation").hidden = true;
    } finally {
      button.disabled = false;
      button.textContent = "Supprimer le fichier";
    }
  };

  search.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      query = search.value.trim();
      page = 0;
      loadPage();
    }, 180);
  });
  $("previous-page").addEventListener("click", () => {
    if (page > 0) { page -= 1; loadPage(); }
  });
  $("next-page").addEventListener("click", () => {
    if ((page + 1) * pageSize < total) { page += 1; loadPage(); }
  });
  $("previous-file").addEventListener("click", () => moveSelection(1));
  $("next-file").addEventListener("click", () => moveSelection(-1));
  $("delete-trigger").addEventListener("click", () => {
    if (!selectedName) return;
    $("delete-title").textContent = `Supprimer « ${selectedName} » définitivement ?`;
    $("delete-confirmation").hidden = false;
    $("confirm-delete").focus();
  });
  $("cancel-delete").addEventListener("click", () => {
    $("delete-confirmation").hidden = true;
    $("delete-trigger").focus();
  });
  $("confirm-delete").addEventListener("click", deleteSelected);
  speedButtons.forEach((button) => button.addEventListener("click", () => {
    playbackSpeed = Number(button.dataset.speed);
    player.playbackRate = playbackSpeed;
    speedButtons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  }));
  list.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) || !files.length) return;
    const current = event.target.closest(".recording-item");
    const currentIndex = current ? Number(current.dataset.index) : -1;
    let target = currentIndex;
    if (event.key === "ArrowDown") target = Math.min(files.length - 1, currentIndex + 1);
    if (event.key === "ArrowUp") target = Math.max(0, currentIndex - 1);
    if (event.key === "Home") target = 0;
    if (event.key === "End") target = files.length - 1;
    if (target >= 0 && target !== currentIndex) {
      event.preventDefault();
      const item = files[target];
      selectFile(item, page * pageSize + target);
      list.querySelector(`[data-index="${target}"]`)?.focus();
    }
  });
  player.addEventListener("error", () => {
    if (selectedName) setViewerFeedback("Impossible de lire ce MP4. Le fichier peut être endommagé ou son codec non pris en charge par le navigateur.", "error");
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey
        && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      search.focus();
    }
  });
  loadPage();
})();
