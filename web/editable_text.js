import { app } from "../../scripts/app.js";

const MAX_SHOTS = 8;

const PROP_VALUE = "h3_persistent_value";
const PROP_SOURCE = "h3_source_snapshot";

function persistentString(node, key, fallback = "") {
  const value = node.properties?.[key];
  return typeof value === "string" ? value : String(fallback ?? "");
}

function setPersistentString(node, key, value) {
  node.properties ??= {};
  node.properties[key] = String(value ?? "");
}

function widgetByName(node, name) {
  return node.widgets?.find((widget) => widget.name === name);
}

function hideBackingWidget(widget) {
  if (!widget || widget.__h3Hidden) return;
  widget.__h3Hidden = true;
  widget.__h3OrigType = widget.type;
  widget.__h3OrigComputeSize = widget.computeSize;
  widget.__h3OrigDraw = widget.draw;
  widget.__h3OrigSerializeValue = widget.serializeValue;

  // Keep the backend STRING widget as the persistence/execution source of truth,
  // but make it presentation-less.  Comfy's converted-widget handling can omit
  // values during workflow serialization, so explicitly serialize the current
  // value even though we use a converted type to avoid the thin multiline bar.
  widget.type = "converted-widget:h3-hidden";
  widget.computeSize = () => [0, 0];
  widget.draw = () => {};
  widget.serializeValue = () => widget.value;
  widget.options ??= {};
  widget.options.serialize = true;
  widget.hidden = true;
}

function setBacking(widget, value) {
  if (!widget) return;
  widget.value = value;
  widget.callback?.(value);
}

function installPersistenceHooks(node, backing, snapshot, restore) {
  const originalOnSerialize = node.onSerialize?.bind(node);
  node.onSerialize = function (info) {
    originalOnSerialize?.(info);
    info.properties ??= {};
    info.properties[PROP_VALUE] = String(backing?.value ?? persistentString(node, PROP_VALUE, ""));
    info.properties[PROP_SOURCE] = String(snapshot?.value ?? persistentString(node, PROP_SOURCE, ""));
  };

  const originalOnConfigure = node.onConfigure?.bind(node);
  node.onConfigure = function (info) {
    const result = originalOnConfigure?.(info);
    const savedValue = typeof info?.properties?.[PROP_VALUE] === "string"
      ? info.properties[PROP_VALUE]
      : String(backing?.value ?? "");
    const savedSource = typeof info?.properties?.[PROP_SOURCE] === "string"
      ? info.properties[PROP_SOURCE]
      : String(snapshot?.value ?? "");
    if (savedValue || savedSource) restore(savedValue, savedSource);
    return result;
  };
}

function baseStyle(el) {
  el.style.boxSizing = "border-box";
  el.style.width = "100%";
  el.style.fontFamily = "Inter, system-ui, sans-serif";
  el.style.color = "var(--fg-color, #ddd)";
}

function makeLabel(text) {
  const el = document.createElement("div");
  baseStyle(el);
  el.textContent = text;
  el.style.fontSize = "12px";
  el.style.fontWeight = "600";
  el.style.margin = "8px 0 4px";
  return el;
}

function makeHint(text) {
  const el = document.createElement("div");
  baseStyle(el);
  el.textContent = text;
  el.style.fontSize = "10px";
  el.style.opacity = "0.7";
  el.style.marginBottom = "4px";
  return el;
}

function makeTextarea(value, onInput, rows = 3) {
  const el = document.createElement("textarea");
  baseStyle(el);
  el.value = value ?? "";
  el.rows = rows;
  el.spellcheck = false;
  el.style.resize = "vertical";
  el.style.minHeight = `${rows * 22}px`;
  el.style.padding = "7px 8px";
  el.style.border = "1px solid rgba(160,160,160,.35)";
  el.style.borderRadius = "6px";
  el.style.background = "rgba(20,20,20,.55)";
  el.style.fontSize = "12px";
  el.addEventListener("input", () => onInput(el.value));
  return el;
}

function makeButton(label, onClick, destructive = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.style.padding = "5px 9px";
  button.style.borderRadius = "6px";
  button.style.border = "1px solid rgba(160,160,160,.4)";
  button.style.background = destructive ? "rgba(130,40,40,.45)" : "rgba(70,70,70,.55)";
  button.style.color = "inherit";
  button.style.cursor = "pointer";
  button.style.whiteSpace = "nowrap";
  button.style.flexShrink = "0";
  button.style.lineHeight = "1.2";
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    onClick();
  });
  return button;
}

function makeNumber(value, onInput) {
  const el = document.createElement("input");
  el.type = "number";
  el.min = "0";
  el.step = "0.1";
  el.value = value ?? "";
  el.style.width = "110px";
  el.style.padding = "5px 7px";
  el.style.borderRadius = "6px";
  el.style.border = "1px solid rgba(160,160,160,.35)";
  el.style.background = "rgba(20,20,20,.55)";
  el.style.color = "inherit";
  el.addEventListener("input", () => {
    onInput(el.value === "" ? null : Number(el.value));
  });
  return el;
}

async function queueSelectedOutput(node) {
  if (typeof app.canvas?.selectItems === "function") {
    app.canvas.selectItems([node]);
  } else if (typeof app.canvas?.selectNode === "function") {
    app.canvas.selectNode(node);
  }
  const command = app.extensionManager?.command;
  if (!command || typeof command.execute !== "function") {
    throw new Error("ComfyUI command service is unavailable");
  }
  await command.execute("Comfy.QueueSelectedOutputNodes");
}

function installRunButton(node, label) {
  const button = node.addWidget("button", label, "", () => {
    queueSelectedOutput(node).catch((error) => {
      console.error(`[H3 Scribe] Failed to run ${label} with native Partial Execution`, error);
    });
  });
  button.serialize = false;

  // Keep the phase action visually first and easy to hit while still using
  // LiteGraph's native button widget rather than custom DOM/CSS.
  button.computeSize = (width) => [width, 46];
  const widgets = node.widgets ?? [];
  const index = widgets.indexOf(button);
  if (index > 0) {
    widgets.splice(index, 1);
    widgets.unshift(button);
  }
}

function installAuthoringEditor(node) {
  const backing = widgetByName(node, "authoring_json");
  const snapshot = widgetByName(node, "source_snapshot");
  hideBackingWidget(backing);
  hideBackingWidget(snapshot);
  // Standard LiteGraph button; ComfyUI still owns selection, graph trimming, and queueing.
  // Install it before the DOM form to avoid mixed-widget layout issues in Nodes 2.0.
  installRunButton(node, "▶ ① ANALYZE");

  const root = document.createElement("div");
  baseStyle(root);
  root.style.padding = "2px 7px 10px";
  root.style.overflow = "auto";
  root.style.height = "100%";

  let state = null;

  const syncBacking = () => {
    if (!state) return;
    const text = JSON.stringify(state, null, 2);
    setBacking(backing, text);
    setPersistentString(node, PROP_VALUE, text);
    node.setDirtyCanvas(true, true);
  };

  const render = () => {
    root.replaceChildren();
    if (!state) {
      root.append(makeHint("Run Analyze once to populate this editor."));
      return;
    }

    const meta = document.createElement("div");
    baseStyle(meta);
    meta.style.fontSize = "10px";
    meta.style.opacity = "0.65";
    meta.style.marginBottom = "6px";
    const initialPic = state.initial_picture_number == null ? "none" : state.initial_picture_number;
    meta.textContent = `${String(state.mode).toUpperCase()} · ${state.reference_image_count} picture(s) · Initial: ${initialPic}`;
    root.append(meta);

    root.append(makeLabel("Subjects / Appearance"));
    if (!state.subjects?.length) {
      root.append(makeHint("No canonical subjects were extracted."));
    } else {
      for (const subject of state.subjects) {
        const card = document.createElement("div");
        card.style.border = "1px solid rgba(160,160,160,.22)";
        card.style.borderRadius = "7px";
        card.style.padding = "6px 7px 8px";
        card.style.marginBottom = "6px";
        const title = document.createElement("div");
        title.textContent = `${subject.label} · Picture ${subject.picture_number} (${subject.source_role})`;
        title.style.fontSize = "11px";
        title.style.fontWeight = "600";
        title.style.marginBottom = "4px";
        card.append(title);
        card.append(makeTextarea(subject.appearance_ja, (value) => {
          subject.appearance_ja = value;
          syncBacking();
        }, 3));
        root.append(card);
      }
    }

    root.append(makeLabel("Initial"));
    root.append(makeHint("Opening state / scene in natural Japanese prose. This is one semantic field, not a fact list."));
    root.append(makeTextarea(state.initial_ja, (value) => {
      state.initial_ja = value;
      syncBacking();
    }, 5));

    root.append(makeLabel("Style"));
    root.append(makeTextarea(state.style_ja, (value) => {
      state.style_ja = value;
      syncBacking();
    }, 2));

    root.append(makeLabel("Throughout"));
    root.append(makeHint("Instructions that should hold throughout all shots. Optional."));
    root.append(makeTextarea(state.throughout, (value) => {
      state.throughout = value;
      syncBacking();
    }, 3));

    const shotHeader = document.createElement("div");
    shotHeader.style.display = "flex";
    shotHeader.style.alignItems = "center";
    shotHeader.style.justifyContent = "space-between";
    shotHeader.append(makeLabel("Shots"));
    const addShot = makeButton("+ Add Shot", () => {
      if (state.shots.length >= MAX_SHOTS) return;
      const previous = state.shots[state.shots.length - 1];
      const previousStart = previous?.start_time_seconds ?? 0;
      state.shots.push({
        start_time_seconds: state.shots.length === 0 ? null : Math.max(previousStart + 1, 1),
        motion: "",
        camera: "Fixed camera",
      });
      syncBacking();
      render();
    });
    addShot.style.minWidth = "104px";
    addShot.style.padding = "6px 12px";
    addShot.disabled = state.shots.length >= MAX_SHOTS;
    shotHeader.append(addShot);
    root.append(shotHeader);

    state.shots.forEach((shot, index) => {
      const card = document.createElement("div");
      card.style.border = "1px solid rgba(160,160,160,.28)";
      card.style.borderRadius = "8px";
      card.style.padding = "7px";
      card.style.margin = "4px 0 8px";

      const header = document.createElement("div");
      header.style.display = "flex";
      header.style.alignItems = "center";
      header.style.justifyContent = "space-between";
      const title = document.createElement("strong");
      title.textContent = `Shot ${index + 1}`;
      title.style.fontSize = "12px";
      header.append(title);
      if (state.shots.length > 1) {
        header.append(makeButton("Remove", () => {
          state.shots.splice(index, 1);
          if (state.shots.length) state.shots[0].start_time_seconds = null;
          syncBacking();
          render();
        }, true));
      }
      card.append(header);

      if (index === 0) {
        card.append(makeHint("Starts at the opening frame."));
        shot.start_time_seconds = null;
      } else {
        const row = document.createElement("div");
        row.style.display = "flex";
        row.style.gap = "8px";
        row.style.alignItems = "center";
        row.style.margin = "6px 0";
        const label = document.createElement("span");
        label.textContent = "Start time (s)";
        label.style.fontSize = "11px";
        row.append(label);
        row.append(makeNumber(shot.start_time_seconds, (value) => {
          shot.start_time_seconds = value;
          syncBacking();
        }));
        card.append(row);
      }

      card.append(makeLabel("Motion"));
      card.append(makeTextarea(shot.motion, (value) => {
        shot.motion = value;
        syncBacking();
      }, 4));

      card.append(makeLabel("Camera"));
      card.append(makeTextarea(shot.camera, (value) => {
        shot.camera = value;
        syncBacking();
      }, 2));
      root.append(card);
    });

    const next = document.createElement("div");
    baseStyle(next);
    next.textContent = "Edit Motion / Camera / Throughout as needed, then run ② COMPOSE ▶. Top-right Run Workflow is also supported for a full-chain run.";
    next.style.marginTop = "10px";
    next.style.padding = "8px 10px";
    next.style.border = "1px solid rgba(130,110,190,.55)";
    next.style.borderRadius = "7px";
    next.style.background = "rgba(90,70,130,.22)";
    next.style.fontSize = "11px";
    root.append(next);
  };

  const setStateFromText = (text, sourceText = null) => {
    try {
      state = JSON.parse(text);
      const normalized = JSON.stringify(state, null, 2);
      setBacking(backing, normalized);
      setPersistentString(node, PROP_VALUE, normalized);
      if (typeof sourceText === "string") {
        setBacking(snapshot, sourceText);
        setPersistentString(node, PROP_SOURCE, sourceText);
      }
      render();
    } catch (error) {
      root.replaceChildren();
      const message = document.createElement("div");
      message.textContent = `H3 Authoring Editor could not parse its state: ${error}`;
      message.style.color = "#ff8a8a";
      root.append(message);
    }
  };

  installPersistenceHooks(node, backing, snapshot, (value, source) => {
    if (value) setStateFromText(value, source);
    else {
      setBacking(backing, "");
      setBacking(snapshot, source);
      setPersistentString(node, PROP_VALUE, "");
      setPersistentString(node, PROP_SOURCE, source);
      state = null;
      render();
    }
  });

  const domWidget = node.addDOMWidget("h3_authoring_form", "H3_AUTHORING_FORM", root, {
    hideOnZoom: false,
    getMinHeight: () => 520,
    getHeight: () => "100%",
  });
  domWidget.serialize = false;

  const persistedValue = persistentString(node, PROP_VALUE, backing?.value ?? "");
  const persistedSource = persistentString(node, PROP_SOURCE, snapshot?.value ?? "");
  if (persistedValue) setStateFromText(persistedValue, persistedSource);
  else render();

  // During a normal full workflow the backend editor itself resolves the merge.
  // Mirror that authoritative result back into the persistent UI widgets so the
  // next queue/save sees the same source snapshot and edited value.
  const originalOnExecuted = node.onExecuted?.bind(node);
  node.onExecuted = function (message) {
    originalOnExecuted?.(message);
    const value = message?.h3_editor_value?.[0];
    const source = message?.h3_editor_source?.[0];
    if (typeof value === "string") {
      setStateFromText(value, typeof source === "string" ? source : null);
      node.setDirtyCanvas(true, true);
    }
  };

  node.size[0] = Math.max(node.size[0], 560);
  node.size[1] = Math.max(node.size[1], 720);
}

function installTextEditor(node) {
  const text = widgetByName(node, "text");
  const snapshot = widgetByName(node, "source_snapshot");

  // Final Prompt is plain text: keep ComfyUI's native multiline STRING widget visible.
  // Only the source snapshot is H3-internal state.
  hideBackingWidget(snapshot);
  installRunButton(node, "▶ ② COMPOSE");

  const savedSource = persistentString(node, PROP_SOURCE, snapshot?.value ?? "");
  setBacking(snapshot, savedSource);
  if (savedSource) setPersistentString(node, PROP_SOURCE, savedSource);

  const originalOnSerialize = node.onSerialize?.bind(node);
  node.onSerialize = function (info) {
    originalOnSerialize?.(info);
    info.properties ??= {};
    info.properties[PROP_SOURCE] = String(snapshot?.value ?? persistentString(node, PROP_SOURCE, ""));
  };

  const originalOnConfigure = node.onConfigure?.bind(node);
  node.onConfigure = function (info) {
    const result = originalOnConfigure?.(info);
    const source = typeof info?.properties?.[PROP_SOURCE] === "string"
      ? info.properties[PROP_SOURCE]
      : "";
    setBacking(snapshot, source);
    setPersistentString(node, PROP_SOURCE, source);
    return result;
  };

  const originalOnExecuted = node.onExecuted?.bind(node);
  node.onExecuted = function (message) {
    originalOnExecuted?.(message);
    const value = message?.h3_editor_value?.[0];
    const source = message?.h3_editor_source?.[0];
    if (typeof value === "string") setBacking(text, value);
    if (typeof source === "string") {
      setBacking(snapshot, source);
      setPersistentString(node, PROP_SOURCE, source);
    }
    node.setDirtyCanvas(true, true);
  };

  node.size[0] = Math.max(node.size[0], 560);
  node.size[1] = Math.max(node.size[1], 460);
}

app.registerExtension({
  name: "h3scribe.editors",

  async nodeCreated(node) {
    if (node.comfyClass === "H3Scribe_AuthoringEditor") {
      installAuthoringEditor(node);
      return;
    }
    if (node.comfyClass === "H3Scribe_TextEditor") {
      installTextEditor(node);
    }
  },
});
