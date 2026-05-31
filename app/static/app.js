const state = {
  ws: null,
  assistantNode: null,
  companionAssistantNode: null,
  audioQueue: [],
  playing: false,
  playbackStarted: false,
  playbackContext: null,
  nextAudioTime: 0,
  playbackLeadTime: 0.12,
  scheduledSources: [],
  providersReady: false,
  currentTurnId: null,
  theme: "light",
  profileAudio: { sample_rate: 16000, channels: 1, frame_ms: 30 },
  ttsRuntime: null,
  ttsBusy: false,
  pendingTtsPresetId: null,
  pendingTtsVoiceId: null,
  settings: null,
  settingsBusy: false,
  view: "chat",
  samplerDefaults: {
    temperature: 0.7,
    max_tokens: 256,
  },
  mic: {
    active: false,
    stream: null,
    context: null,
    source: null,
    processor: null,
    sequence: 0,
    pending: [],
  },
};

const profileEl = document.querySelector("#profile");
const providersEl = document.querySelector("#providers");
const conversationEl = document.querySelector("#conversation");
const latencyEl = document.querySelector("#latency");
const eventsEl = document.querySelector("#events");
const connectButton = document.querySelector("#connect");
const themeToggleButton = document.querySelector("#theme-toggle");
const micButton = document.querySelector("#mic");
const bargeButton = document.querySelector("#barge");
const stopAudioButton = document.querySelector("#stop-audio");
const clearButton = document.querySelector("#clear");
const sendButton = document.querySelector("#send");
const continueButton = document.querySelector("#continue-btn");
const deviceSelect = document.querySelector("#device");
const levelEl = document.querySelector("#level");
const vadStateEl = document.querySelector("#vad-state");
const asrStateEl = document.querySelector("#asr-state");
const ttsStateEl = document.querySelector("#tts-state");
const audioStatusEl = document.querySelector("#audio-status");
const composer = document.querySelector("#composer");
const textInput = document.querySelector("#text");
const systemPromptInput = document.querySelector("#system-prompt");
const ttsPresetEl = document.querySelector("#tts-preset");
const ttsVoiceEl = document.querySelector("#tts-voice");
const ttsLoadButton = document.querySelector("#tts-load");
const ttsUnloadButton = document.querySelector("#tts-unload");
const ttsRuntimeStateEl = document.querySelector("#tts-runtime-state");
const ttsRuntimeSummaryEl = document.querySelector("#tts-runtime-summary");
const tabChatButton = document.querySelector("#tab-chat");
const tabCompanionButton = document.querySelector("#tab-companion");
const tabSettingsButton = document.querySelector("#tab-settings");
const shellEl = document.querySelector(".shell");
const userNameInput = document.querySelector("#user-name");
const aiNameInput = document.querySelector("#ai-name");
const samplerTemperature = document.querySelector("#sampler-temperature");
const samplerTopK = document.querySelector("#sampler-top-k");
const samplerTopP = document.querySelector("#sampler-top-p");
const samplerMinP = document.querySelector("#sampler-min-p");
const samplerRepeatPenalty = document.querySelector("#sampler-repeat-penalty");
const samplerFrequencyPenalty = document.querySelector("#sampler-frequency-penalty");
const samplerPresencePenalty = document.querySelector("#sampler-presence-penalty");
const samplerMaxTokens = document.querySelector("#sampler-max-tokens");
const samplerResetButton = document.querySelector("#sampler-reset");
const characterDrop = document.querySelector("#character-drop");
const characterFile = document.querySelector("#character-file");
const characterInfo = document.querySelector("#character-info");
const characterNameEl = document.querySelector("#character-name");
const characterDescEl = document.querySelector("#character-description");
const characterFullEl = document.querySelector("#character-full");
const characterClearButton = document.querySelector("#character-clear");
const additionalSystemPromptInput = document.querySelector("#additional-system-prompt");
const companionConversationEl = document.querySelector("#companion-conversation");
const companionComposer = document.querySelector("#companion-composer");
const companionTextInput = document.querySelector("#companion-text");
const companionSendButton = document.querySelector("#companion-send");
const companionContinueButton = document.querySelector("#companion-continue-btn");
const companionUserNameInput = document.querySelector("#companion-user-name");
const companionAiNameInput = document.querySelector("#companion-ai-name");
const companionMemoryEnabledInput = document.querySelector("#companion-memory-enabled");
const companionSystemPromptInput = document.querySelector("#companion-system-prompt");
const companionMemoryInput = document.querySelector("#companion-memory");
const memorySaveButton = document.querySelector("#memory-save");
const memorySummarizeButton = document.querySelector("#memory-summarize");
const memoryClearButton = document.querySelector("#memory-clear");
const memoryStatusEl = document.querySelector("#memory-status");
const companionSamplerTemperature = document.querySelector("#companion-sampler-temperature");
const companionSamplerTopP = document.querySelector("#companion-sampler-top-p");
const companionSamplerMinP = document.querySelector("#companion-sampler-min-p");
const companionSamplerMaxTokens = document.querySelector("#companion-sampler-max-tokens");
const companionSamplerResetButton = document.querySelector("#companion-sampler-reset");
let systemPromptTimer = null;
let settingsSaveTimer = null;

async function loadStatus() {
  const response = await fetch("/api/status");
  const status = await response.json();
  applyStatus(status);
  await loadInputDevices();
}

function applyStatus(status) {
  state.profileAudio = status.profile.audio || state.profileAudio;
  state.ttsRuntime = status.tts_runtime || null;
  if (state.pendingTtsPresetId && status.tts_runtime?.selected_preset_id === state.pendingTtsPresetId) {
    state.pendingTtsPresetId = null;
  }
  if (state.pendingTtsVoiceId && status.tts_runtime?.selected_voice === state.pendingTtsVoiceId) {
    state.pendingTtsVoiceId = null;
  }
  const override = status.tts_runtime?.selected_preset?.label;
  profileEl.textContent = override
    ? `${status.profile.name} - ${status.profile.description} - TTS override: ${override}`
    : `${status.profile.name} - ${status.profile.description}`;
  renderProviders(status.providers, status.profile.summary || []);
  renderTtsRuntime(status.tts_runtime);
  if (status.settings) {
    applySettings(status.settings);
  }
}

function renderProviders(providers, summary = []) {
  providersEl.innerHTML = "";
  state.providersReady = providers.every((provider) => provider.ready);
  updateSendState();
  for (const provider of providers) {
    const details = summary.find((item) => item.kind === provider.kind) || {};
    const detailText = formatProviderDetails(details, provider);
    const node = document.createElement("article");
    node.className = "provider";
    node.dataset.ready = provider.ready ? "true" : "false";
    const title = document.createElement("strong");
    title.textContent = `${provider.kind}: ${provider.name}`;
    const ready = document.createElement("span");
    ready.textContent = provider.ready ? "ready" : "not ready";
    node.append(title, ready);
    if (detailText) {
      const detailsNode = document.createElement("p");
      detailsNode.textContent = detailText;
      node.appendChild(detailsNode);
    }
    const message = document.createElement("p");
    message.textContent = provider.message;
    node.appendChild(message);
    providersEl.appendChild(node);
  }
}

function formatProviderDetails(details, provider = {}) {
  const parts = [];
  for (const key of ["provider", "model", "device", "compute_type", "endpoint", "voice"]) {
    if (details[key]) {
      parts.push(`${key}: ${details[key]}`);
    }
  }
  if (provider.kind === "tts") {
    if (provider.selected) {
      parts.push("selected");
    }
    if (provider.managed_externally) {
      parts.push("external");
    } else if (provider.loaded) {
      parts.push("loaded");
    } else {
      parts.push("unloaded");
    }
  }
  return parts.join(" | ");
}

function renderTtsRuntime(runtime) {
  state.ttsRuntime = runtime || null;
  const selectedPresetId = state.pendingTtsPresetId || runtime?.selected_preset_id;
  const selectedVoiceId = state.pendingTtsVoiceId || runtime?.selected_voice;
  ttsPresetEl.innerHTML = "";
  const presets = runtime?.presets || [];
  for (const preset of presets) {
    const option = document.createElement("option");
    option.value = preset.id;
    option.textContent = preset.label;
    if (preset.id === selectedPresetId) {
      option.selected = true;
    }
    ttsPresetEl.appendChild(option);
  }
  if (presets.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No presets available";
    ttsPresetEl.appendChild(option);
  }

  ttsVoiceEl.innerHTML = "";
  const voices = runtime?.available_voices || [];
  for (const voice of voices) {
    const option = document.createElement("option");
    option.value = voice.id;
    option.textContent = voice.label;
    if (voice.id === selectedVoiceId) {
      option.selected = true;
    }
    ttsVoiceEl.appendChild(option);
  }
  if (voices.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No voices available";
    ttsVoiceEl.appendChild(option);
  }

  const status = runtime?.status;
  let mode = "unavailable";
  let text = "TTS unavailable";
  if (status) {
    if (!status.ready) {
      mode = "unavailable";
      text = "Unavailable";
    } else if (status.managed_externally) {
      mode = "external";
      text = "External";
    } else if (status.loaded) {
      mode = "loaded";
      text = "Loaded";
    } else {
      mode = "unloaded";
      text = "Unloaded";
    }
  }
  ttsRuntimeStateEl.dataset.mode = mode;
  ttsRuntimeStateEl.textContent = text;
  const selectedPreset = presets.find((preset) => preset.id === selectedPresetId) || runtime?.selected_preset;
  const selectedVoice = voices.find((voice) => voice.id === selectedVoiceId);
  ttsRuntimeSummaryEl.textContent = selectedPreset
    ? `${selectedPreset.label} - ${selectedPreset.description}${selectedVoice ? ` - Voice: ${selectedVoice.label}` : ""}`
    : "";

  const supportsManagement = Boolean(status?.supports_model_management);
  const supportsVoiceSelection = Boolean(status?.supports_voice_selection);
  ttsPresetEl.disabled = state.ttsBusy || presets.length === 0;
  ttsVoiceEl.disabled = state.ttsBusy || !supportsVoiceSelection || voices.length === 0;
  ttsLoadButton.disabled = state.ttsBusy || !supportsManagement;
  ttsUnloadButton.disabled = state.ttsBusy || !supportsManagement;
}

function connect() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.close();
    return;
  }
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  state.ws = new WebSocket(`${protocol}://${location.host}/ws/events`);
  state.ws.addEventListener("open", () => {
    connectButton.textContent = "Disconnect";
    updateSendState();
    updateMicState();
    sendSystemPromptUpdate();
    sendCompanionPromptUpdate();
    addSystemMessage("Connected");
  });
  state.ws.addEventListener("close", () => {
    connectButton.textContent = "Connect";
    sendButton.disabled = true;
    companionSendButton.disabled = true;
    companionContinueButton.disabled = true;
    bargeButton.disabled = true;
    stopAudioButton.disabled = true;
    clearButton.disabled = true;
    updateMicState();
    addSystemMessage("Disconnected");
  });
  state.ws.addEventListener("message", (message) => {
    handleEvent(JSON.parse(message.data));
  });
}

function handleEvent(event) {
  addEvent(event.type);
  const payload = event.payload || {};
  const mode = payload.mode || "chat";
  if (event.type === "providers.status") {
    renderProviders(payload.providers, payload.summary || []);
    renderTtsRuntime(payload.tts_runtime || state.ttsRuntime);
  } else if (event.type === "turn.started") {
    latencyEl.innerHTML = "";
    state.currentTurnId = payload.turn_id || null;
    state.playbackStarted = false;
    resetPlaybackClock();
  } else if (event.type === "asr.transcript" && payload.final) {
    updateAsrState(false, "ASR done");
    setLatency("ASR final", payload.latency_ms);
    addMessage("user", payload.text, mode);
  } else if (event.type === "asr.started") {
    updateAsrState(true, "ASR transcribing");
  } else if (event.type === "asr.progress") {
    updateAsrState(true, payload.message || `ASR ${payload.stage}`);
  } else if (event.type === "asr.error") {
    updateAsrState(false, `ASR error: ${payload.message}`);
  } else if (event.type === "asr.buffer_warning") {
    updateAsrState(false, payload.message);
  } else if (event.type === "llm.first_token") {
    setLatency("LLM first token", payload.latency_ms);
    if (mode === "companion") {
      state.companionAssistantNode = addMessage("assistant", "", "companion");
    } else {
      state.assistantNode = addMessage("assistant", "", "chat");
    }
  } else if (event.type === "llm.token") {
    const nodeKey = mode === "companion" ? "companionAssistantNode" : "assistantNode";
    if (!state[nodeKey]) {
      state[nodeKey] = addMessage("assistant", "", mode);
    }
    state[nodeKey].textContent = payload.accumulated;
    const target = conversationForMode(mode);
    target.scrollTop = target.scrollHeight;
  } else if (event.type === "tts.first_chunk") {
    updateTtsState(true, "TTS playing");
    setLatency("TTS first chunk", payload.latency_ms);
  } else if (event.type === "tts.audio") {
    setMetric("TTS chunks", payload.chunk_index);
    setMetric("TTS latest bytes", payload.byte_length);
    enqueueAudio(payload);
  } else if (event.type === "tts.progress") {
    updateTtsState(true, payload.message || `TTS ${payload.stage}`);
  } else if (event.type === "tts.error") {
    updateTtsState(false, `TTS error: ${payload.message}`);
  } else if (event.type === "audio.input_level") {
    updateInputLevel(payload.rms, payload.peak);
    audioStatusEl.textContent = `Mic ${Math.round((payload.rms || 0) * 100)}% - ${payload.received_frames} frames`;
  } else if (event.type === "audio.frame_error") {
    audioStatusEl.textContent = `Audio error: ${payload.message}`;
  } else if (event.type === "vad.probability") {
    updateVadState(null, payload.probability);
  } else if (event.type === "vad.speech_start") {
    updateVadState(true, payload.probability);
  } else if (event.type === "vad.speech_end") {
    updateVadState(false, payload.probability);
  } else if (event.type === "vad.error") {
    vadStateEl.textContent = `VAD error: ${payload.message}`;
    vadStateEl.dataset.speaking = "false";
  } else if (event.type === "turn.finished") {
    setLatency("Turn complete", payload.latency_ms);
    if (mode === "companion") state.companionAssistantNode = null;
    else state.assistantNode = null;
    updateTtsState(false, "TTS idle");
  } else if (event.type === "turn.error") {
    addMessage("system", `Error: ${payload.message}`, mode);
  } else if (event.type === "tts.cancelled") {
    stopAudio();
    updateTtsState(false, "TTS cancelled");
  } else if (event.type === "conversation.cleared") {
    conversationForMode(mode).innerHTML = "";
    latencyEl.innerHTML = "";
    addMessage("system", "Conversation cleared", mode);
  } else if (event.type === "conversation.seeded") {
    addMessage(payload.role || "assistant", payload.text || "", mode);
  } else if (event.type === "settings.updated") {
    applySettings(payload);
  }
}

function conversationForMode(mode) {
  return mode === "companion" ? companionConversationEl : conversationEl;
}

function addMessage(kind, text, mode = "chat") {
  const node = document.createElement("div");
  node.className = `message ${kind}`;
  if (text) {
    node.textContent = text;
  }
  const target = conversationForMode(mode);
  target.appendChild(node);
  target.scrollTop = target.scrollHeight;
  return node;
}

function displayName(kind) {
  if (kind === "user") {
    return state.settings?.user_name || "You";
  }
  if (kind === "assistant") {
    if (state.settings?.character?.name) return state.settings.character.name;
    return state.settings?.ai_name || "Assistant";
  }
  return kind;
}

function addSystemMessage(text) {
  addMessage("system", text);
}

function updateSendState() {
  const connected = state.ws && state.ws.readyState === WebSocket.OPEN;
  sendButton.disabled = !connected || !state.providersReady;
  continueButton.disabled = !connected || !state.providersReady;
  companionSendButton.disabled = !connected || !state.providersReady;
  companionContinueButton.disabled = !connected || !state.providersReady;
  clearButton.disabled = !connected;
  stopAudioButton.disabled = !connected;
  updateMicState();
  if (!state.providersReady) {
    textInput.placeholder = "A required provider is not ready. Check the status cards above.";
  } else {
    textInput.placeholder = "Type a test utterance while the audio path is being wired...";
  }
}

function updateMicState() {
  const connected = state.ws && state.ws.readyState === WebSocket.OPEN;
  micButton.disabled = !connected || !state.providersReady;
  micButton.textContent = state.mic.active ? "Stop Mic" : "Start Mic";
  bargeButton.disabled = !connected;
  deviceSelect.disabled = state.mic.active;
}

function addEvent(type) {
  const node = document.createElement("li");
  node.textContent = type;
  eventsEl.prepend(node);
  while (eventsEl.children.length > 35) {
    eventsEl.lastElementChild.remove();
  }
}

function setLatency(label, value) {
  if (value === undefined || value === null) {
    return;
  }
  setMetric(label, `${value} ms`);
}

function setMetric(label, value) {
  if (value === undefined || value === null) {
    return;
  }
  let row = latencyEl.querySelector(`[data-label="${label}"]`);
  if (!row) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.dataset.label = label;
    latencyEl.append(dt, dd);
    row = dd;
  }
  row.textContent = String(value);
}

async function loadInputDevices() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
    audioStatusEl.textContent = "Browser audio devices unavailable";
    return;
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  const inputs = devices.filter((device) => device.kind === "audioinput");
  deviceSelect.innerHTML = '<option value="">Default input</option>';
  for (const input of inputs) {
    const option = document.createElement("option");
    option.value = input.deviceId;
    option.textContent = input.label || `Input ${deviceSelect.children.length}`;
    deviceSelect.appendChild(option);
  }
}

async function toggleMic() {
  if (state.mic.active) {
    stopMic();
    return;
  }
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    audioStatusEl.textContent = "Connect before starting mic";
    return;
  }
  sendSystemPromptUpdate();
  const constraints = {
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  };
  if (deviceSelect.value) {
    constraints.audio.deviceId = { exact: deviceSelect.value };
  }
  const stream = await navigator.mediaDevices.getUserMedia(constraints);
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(2048, 1, 1);
  state.mic = {
    active: true,
    stream,
    context,
    source,
    processor,
    sequence: 0,
    pending: [],
  };
  processor.onaudioprocess = (event) => handleAudioProcess(event.inputBuffer.getChannelData(0), context.sampleRate);
  source.connect(processor);
  processor.connect(context.destination);
  await loadInputDevices();
  audioStatusEl.textContent = `Mic active - ${context.sampleRate} Hz browser input`;
  updateMicState();
}

function stopMic() {
  if (state.mic.processor) {
    state.mic.processor.disconnect();
  }
  if (state.mic.source) {
    state.mic.source.disconnect();
  }
  if (state.mic.context) {
    state.mic.context.close();
  }
  if (state.mic.stream) {
    for (const track of state.mic.stream.getTracks()) {
      track.stop();
    }
  }
  state.mic = { active: false, stream: null, context: null, source: null, processor: null, sequence: 0, pending: [] };
  updateInputLevel(0, 0);
  updateVadState(false, 0);
  updateAsrState(false, "ASR idle");
  updateTtsState(false, "TTS idle");
  audioStatusEl.textContent = "Audio idle";
  updateMicState();
}

function handleAudioProcess(input, inputSampleRate) {
  if (!state.mic.active || !state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  const targetRate = state.profileAudio.sample_rate || 16000;
  const frameMs = state.profileAudio.frame_ms || 30;
  const targetSamples = Math.floor((targetRate * frameMs) / 1000);
  const resampled = downsampleMono(input, inputSampleRate, targetRate);
  state.mic.pending.push(...resampled);
  while (state.mic.pending.length >= targetSamples) {
    const frame = state.mic.pending.splice(0, targetSamples);
    sendAudioFrame(frame, targetRate, frameMs);
  }
}

function downsampleMono(input, inputRate, outputRate) {
  if (inputRate === outputRate) {
    return Array.from(input);
  }
  const ratio = inputRate / outputRate;
  const outputLength = Math.floor(input.length / ratio);
  const output = new Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j += 1) {
      sum += input[j];
      count += 1;
    }
    output[i] = count > 0 ? sum / count : 0;
  }
  return output;
}

function sendAudioFrame(samples, sampleRate, frameMs) {
  const pcm = new Int16Array(samples.length);
  let sumSquares = 0;
  let peak = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    peak = Math.max(peak, Math.abs(clamped));
    sumSquares += clamped * clamped;
    pcm[i] = clamped < 0 ? clamped * 32768 : clamped * 32767;
  }
  const rms = Math.sqrt(sumSquares / Math.max(1, samples.length));
  updateInputLevel(rms, peak);
  const bytes = new Uint8Array(pcm.buffer);
  state.ws.send(JSON.stringify({
    type: "audio.frame",
    payload: {
      encoding: "pcm_s16le",
      sample_rate: sampleRate,
      channels: 1,
      frame_ms: frameMs,
      sequence: state.mic.sequence,
      data: base64FromBytes(bytes),
    },
  }));
  state.mic.sequence += 1;
}

function base64FromBytes(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function updateInputLevel(rms, peak) {
  const value = Math.max(rms || 0, (peak || 0) * 0.55);
  levelEl.style.width = `${Math.min(100, Math.round(value * 140))}%`;
}

function updateVadState(speaking, probability) {
  if (speaking !== null) {
    vadStateEl.dataset.speaking = speaking ? "true" : "false";
  }
  const active = vadStateEl.dataset.speaking === "true";
  const prob = probability === undefined || probability === null ? "--" : probability.toFixed(2);
  vadStateEl.textContent = `${active ? "VAD speaking" : "VAD idle"} - ${prob}`;
}

function updateAsrState(active, text) {
  asrStateEl.dataset.active = active ? "true" : "false";
  asrStateEl.textContent = text;
}

function updateTtsState(active, text) {
  ttsStateEl.dataset.active = active ? "true" : "false";
  ttsStateEl.textContent = text;
}

function applySettings(settings) {
  state.settings = settings;
  renderNames();
  renderSampler();
  renderCompanionSettings();
  renderCharacter();
  additionalSystemPromptInput.value = settings.additional_system_prompt || "";
}

function renderNames() {
  if (!state.settings) return;
  if (document.activeElement !== userNameInput) {
    userNameInput.value = state.settings.user_name || "";
  }
  if (document.activeElement !== aiNameInput) {
    aiNameInput.value = state.settings.ai_name || "";
  }
}

function renderSampler() {
  if (!state.settings) return;
  const display = state.settings.sampler_display || {};
  const overrides = state.settings.llm_overrides || {};
  const samplerInputs = {
    temperature: samplerTemperature,
    top_k: samplerTopK,
    top_p: samplerTopP,
    min_p: samplerMinP,
    repeat_penalty: samplerRepeatPenalty,
    frequency_penalty: samplerFrequencyPenalty,
    presence_penalty: samplerPresencePenalty,
    max_tokens: samplerMaxTokens,
  };
  for (const [key, input] of Object.entries(samplerInputs)) {
    if (document.activeElement === input) continue;
    if (key in overrides && overrides[key] !== null && overrides[key] !== undefined) {
      input.value = overrides[key];
    } else if (key in display) {
      input.value = display[key];
    } else {
      input.value = "";
    }
  }
}

function renderCompanionSettings() {
  if (!state.settings) return;
  const companion = state.settings.companion || {};
  if (document.activeElement !== companionUserNameInput) {
    companionUserNameInput.value = companion.user_name || "";
  }
  if (document.activeElement !== companionAiNameInput) {
    companionAiNameInput.value = companion.ai_name || "";
  }
  if (document.activeElement !== companionSystemPromptInput) {
    companionSystemPromptInput.value = companion.system_prompt || "";
  }
  companionMemoryEnabledInput.checked = companion.memory_enabled !== false;
  const display = companion.sampler_display || {};
  const overrides = companion.llm_overrides || {};
  const samplerInputs = {
    temperature: companionSamplerTemperature,
    top_p: companionSamplerTopP,
    min_p: companionSamplerMinP,
    max_tokens: companionSamplerMaxTokens,
  };
  for (const [key, input] of Object.entries(samplerInputs)) {
    if (document.activeElement === input) continue;
    if (key in overrides && overrides[key] !== null && overrides[key] !== undefined) {
      input.value = overrides[key];
    } else if (key in display) {
      input.value = display[key];
    } else {
      input.value = "";
    }
  }
}

function renderCharacter() {
  if (!state.settings) return;
  const card = state.settings.character;
  if (card && card.name) {
    characterInfo.classList.remove("hidden");
    characterDrop.classList.add("hidden");
    characterNameEl.textContent = card.name;
    characterDescEl.textContent = card.description || card.personality || "No description.";
    const parts = [];
    if (card.personality) parts.push(`Personality:\n${card.personality}`);
    if (card.scenario) parts.push(`Scenario:\n${card.scenario}`);
    if (card.first_mes) parts.push(`First message:\n${card.first_mes}`);
    if (card.mes_example) parts.push(`Example dialogue:\n${card.mes_example}`);
    if (card.system_prompt) parts.push(`Card system prompt:\n${card.system_prompt}`);
    characterFullEl.textContent = parts.join("\n\n") || "No additional details.";
  } else {
    characterInfo.classList.add("hidden");
    characterDrop.classList.remove("hidden");
  }
}

function collectSamplerOverrides() {
  const overrides = {};
  const pairs = [
    ["temperature", samplerTemperature],
    ["top_k", samplerTopK],
    ["top_p", samplerTopP],
    ["min_p", samplerMinP],
    ["repeat_penalty", samplerRepeatPenalty],
    ["frequency_penalty", samplerFrequencyPenalty],
    ["presence_penalty", samplerPresencePenalty],
    ["max_tokens", samplerMaxTokens],
  ];
  for (const [key, input] of pairs) {
    const val = input.value.trim();
    if (val === "") {
      overrides[key] = null;
    } else {
      const num = Number(val);
      if (!isNaN(num)) {
        overrides[key] = num;
      }
    }
  }
  return overrides;
}

function collectCompanionSamplerOverrides() {
  const overrides = {};
  const pairs = [
    ["temperature", companionSamplerTemperature],
    ["top_p", companionSamplerTopP],
    ["min_p", companionSamplerMinP],
    ["max_tokens", companionSamplerMaxTokens],
  ];
  for (const [key, input] of pairs) {
    const val = input.value.trim();
    if (val === "") {
      overrides[key] = null;
    } else {
      const num = Number(val);
      if (!isNaN(num)) overrides[key] = num;
    }
  }
  return overrides;
}

async function saveSettingsDebounced() {
  window.clearTimeout(settingsSaveTimer);
  settingsSaveTimer = window.setTimeout(async () => {
    const patch = {
      llm_overrides: collectSamplerOverrides(),
      user_name: userNameInput.value.trim() || "You",
      ai_name: aiNameInput.value.trim() || "Assistant",
      additional_system_prompt: additionalSystemPromptInput.value.trim(),
    };
    try {
      const result = await patchJson("/api/settings", patch);
      applySettings(result);
    } catch (error) {
      audioStatusEl.textContent = `Settings save failed: ${error.message}`;
    }
  }, 400);
}

async function saveCompanionSettingsDebounced() {
  window.clearTimeout(settingsSaveTimer);
  settingsSaveTimer = window.setTimeout(async () => {
    const patch = {
      active_mode: "companion",
      companion: {
        user_name: companionUserNameInput.value.trim() || "You",
        ai_name: companionAiNameInput.value.trim() || "Companion",
        system_prompt: companionSystemPromptInput.value.trim(),
        memory_enabled: companionMemoryEnabledInput.checked,
        llm_overrides: collectCompanionSamplerOverrides(),
      },
    };
    try {
      const result = await patchJson("/api/settings", patch);
      applySettings(result);
    } catch (error) {
      memoryStatusEl.textContent = `Companion settings failed: ${error.message}`;
    }
  }, 400);
}

async function resetSampler() {
  const patch = { llm_overrides: {} };
  try {
    const result = await patchJson("/api/settings", patch);
    applySettings(result);
  } catch (error) {
    audioStatusEl.textContent = `Sampler reset failed: ${error.message}`;
  }
}

async function resetCompanionSampler() {
  try {
    const result = await patchJson("/api/settings", { companion: { llm_overrides: {} } });
    applySettings(result);
  } catch (error) {
    memoryStatusEl.textContent = `Companion sampler reset failed: ${error.message}`;
  }
}

async function patchJson(url, body) {
  const response = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `${response.status}`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch (error) {
      // Fall back to status text.
    }
    throw new Error(message);
  }
  return await response.json();
}

async function uploadCharacterFile(file) {
  const reader = new FileReader();
  reader.onload = async () => {
    const base64Data = reader.result.split(",")[1];
    try {
      const result = await postJson("/api/settings/character/upload", {
        filename: file.name,
        data: base64Data,
      });
      applySettings(result);
    } catch (error) {
      audioStatusEl.textContent = `Character import failed: ${error.message}`;
    }
  };
  reader.readAsDataURL(file);
}

async function clearCharacter() {
  try {
    const response = await fetch("/api/settings/character", { method: "DELETE" });
    const result = await response.json();
    applySettings(result);
  } catch (error) {
    audioStatusEl.textContent = `Character clear failed: ${error.message}`;
  }
}

function switchTab(tab) {
  state.view = tab;
  shellEl.dataset.view = tab;
  tabChatButton.classList.toggle("active", tab === "chat");
  tabCompanionButton.classList.toggle("active", tab === "companion");
  tabSettingsButton.classList.toggle("active", tab === "settings");
  if (tab === "companion") {
    loadMemory();
  }
}

function enqueueAudio(payload) {
  state.audioQueue.push({
    mimeType: payload.mime_type,
    encoding: payload.encoding,
    sampleRate: payload.sample_rate,
    channels: payload.channels,
    durationMs: payload.duration_ms,
    base64: payload.data,
    latencyMs: payload.latency_ms,
    chunkIndex: payload.chunk_index,
    byteLength: payload.byte_length,
    textChars: payload.text_chars,
    turnId: payload.turn_id,
  });
  playAudioQueue();
}

async function playAudioQueue() {
  if (state.playing || state.audioQueue.length === 0) {
    return;
  }
  state.playing = true;

  try {
    const context = getPlaybackContext();
    if (context.state === "suspended") {
      await context.resume();
    }

    while (state.audioQueue.length > 0) {
      const item = state.audioQueue.shift();
      const decodeStarted = performance.now();
      const audioBuffer = await decodeAudioChunk(context, item);
      const decodeMs = Math.round(performance.now() - decodeStarted);
      const source = context.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(context.destination);

      const now = context.currentTime;
      const leadMs = Math.max(0, Math.round((state.nextAudioTime - now) * 1000));
      const startAt = Math.max(now + state.playbackLeadTime, state.nextAudioTime);
      source.start(startAt);
      state.nextAudioTime = startAt + audioBuffer.duration;
      state.scheduledSources.push(source);
      source.addEventListener("ended", () => {
        state.scheduledSources = state.scheduledSources.filter((candidate) => candidate !== source);
      });

      if (!state.playbackStarted) {
        state.playbackStarted = true;
        setLatency("Playback start", item.latencyMs);
      }
      if (item.chunkIndex !== undefined) {
        setLatency("Decode latest", decodeMs);
        setLatency("Queue lead", leadMs);
      }
    }
  } catch (error) {
    audioStatusEl.textContent = `Playback error: ${error.message}`;
  } finally {
    state.playing = false;
    if (state.audioQueue.length > 0) {
      playAudioQueue();
    }
  }
}

function getPlaybackContext() {
  if (!state.playbackContext) {
    state.playbackContext = new AudioContext();
    state.nextAudioTime = state.playbackContext.currentTime;
  }
  return state.playbackContext;
}

async function decodeAudioChunk(context, item) {
  const bytes = bytesFromBase64(item.base64);
  if (item.encoding === "pcm_s16le") {
    return decodePcm16Chunk(context, bytes, item.sampleRate || 24000, item.channels || 1);
  }
  return await context.decodeAudioData(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
}

function decodePcm16Chunk(context, bytes, sampleRate, channels) {
  const bytesPerSample = 2;
  const frameCount = Math.floor(bytes.byteLength / bytesPerSample / channels);
  const buffer = context.createBuffer(channels, frameCount, sampleRate);
  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, frameCount * channels);
  for (let channel = 0; channel < channels; channel += 1) {
    const output = buffer.getChannelData(channel);
    for (let index = 0; index < frameCount; index += 1) {
      output[index] = pcm[index * channels + channel] / 32768;
    }
  }
  return buffer;
}

function bytesFromBase64(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function stopAudio() {
  state.audioQueue = [];
  state.playing = false;
  state.playbackStarted = false;
  for (const source of state.scheduledSources) {
    try {
      source.stop();
    } catch (error) {
      // Source may already have ended.
    }
  }
  state.scheduledSources = [];
  if (state.playbackContext) {
    state.nextAudioTime = state.playbackContext.currentTime;
  }
}

function resetPlaybackClock() {
  if (state.playbackContext) {
    state.nextAudioTime = state.playbackContext.currentTime;
  }
}

function sendSystemPromptUpdate() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  state.ws.send(JSON.stringify({
    type: "system_prompt.update",
    payload: { mode: "chat", system_prompt: systemPromptInput.value },
  }));
}

function sendCompanionPromptUpdate() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  state.ws.send(JSON.stringify({
    type: "system_prompt.update",
    payload: { mode: "companion", system_prompt: companionSystemPromptInput.value },
  }));
}

async function loadMemory() {
  try {
    const response = await fetch("/api/companion/memory");
    const payload = await response.json();
    companionMemoryInput.value = payload.text || "";
    memoryStatusEl.textContent = payload.metadata?.exists
      ? `Memory loaded - ${payload.metadata.chars} chars`
      : "No saved memory yet";
  } catch (error) {
    memoryStatusEl.textContent = `Memory load failed: ${error.message}`;
  }
}

async function saveMemory() {
  try {
    const payload = await putJson("/api/companion/memory", { text: companionMemoryInput.value });
    companionMemoryInput.value = payload.text || "";
    memoryStatusEl.textContent = `Memory saved - ${payload.metadata.chars} chars`;
  } catch (error) {
    memoryStatusEl.textContent = `Memory save failed: ${error.message}`;
  }
}

async function summarizeMemory() {
  memoryStatusEl.textContent = "Summarizing companion chat...";
  try {
    const payload = await postJson("/api/companion/memory/summarize", {});
    companionMemoryInput.value = payload.text || "";
    memoryStatusEl.textContent = `Memory summarized - ${payload.metadata.chars} chars`;
  } catch (error) {
    memoryStatusEl.textContent = `Memory summarize failed: ${error.message}`;
  }
}

async function clearMemory() {
  try {
    const response = await fetch("/api/companion/memory", { method: "DELETE" });
    const payload = await response.json();
    companionMemoryInput.value = payload.text || "";
    memoryStatusEl.textContent = "Memory cleared";
  } catch (error) {
    memoryStatusEl.textContent = `Memory clear failed: ${error.message}`;
  }
}

function applyTheme(theme) {
  state.theme = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = state.theme;
  themeToggleButton.textContent = state.theme === "dark" ? "Light Mode" : "Dark Mode";
  localStorage.setItem("harness-theme", state.theme);
}

async function postJson(url, body = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `${response.status}`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch (error) {
      // Fall back to status text.
    }
    throw new Error(message);
  }
  return await response.json();
}

async function putJson(url, body = {}) {
  const response = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let message = `${response.status}`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch (error) {
      // Fall back to status text.
    }
    throw new Error(message);
  }
  return await response.json();
}

async function withTtsBusy(action) {
  state.ttsBusy = true;
  renderTtsRuntime(state.ttsRuntime);
  try {
    const runtime = await action();
    state.pendingTtsPresetId = null;
    state.pendingTtsVoiceId = null;
    state.ttsRuntime = runtime;
    renderTtsRuntime(runtime);
  } catch (error) {
    state.pendingTtsPresetId = null;
    state.pendingTtsVoiceId = null;
    audioStatusEl.textContent = `TTS runtime error: ${error.message}`;
  } finally {
    state.ttsBusy = false;
    renderTtsRuntime(state.ttsRuntime);
    await loadStatus();
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!text || !state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  latencyEl.innerHTML = "";
  state.playbackStarted = false;
  state.ws.send(JSON.stringify({
    type: "user.text",
    payload: { mode: "chat", text, system_prompt: systemPromptInput.value },
  }));
  textInput.value = "";
});

continueButton.addEventListener("click", () => {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  latencyEl.innerHTML = "";
  state.playbackStarted = false;
  state.ws.send(JSON.stringify({
    type: "user.continue",
    payload: { mode: "chat" },
  }));
});

companionComposer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = companionTextInput.value.trim();
  if (!text || !state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  latencyEl.innerHTML = "";
  state.playbackStarted = false;
  state.ws.send(JSON.stringify({
    type: "user.text",
    payload: { mode: "companion", text, system_prompt: companionSystemPromptInput.value },
  }));
  companionTextInput.value = "";
});

companionContinueButton.addEventListener("click", () => {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  latencyEl.innerHTML = "";
  state.playbackStarted = false;
  state.ws.send(JSON.stringify({
    type: "user.continue",
    payload: { mode: "companion" },
  }));
});

connectButton.addEventListener("click", connect);
themeToggleButton.addEventListener("click", () => {
  applyTheme(state.theme === "dark" ? "light" : "dark");
});
micButton.addEventListener("click", () => {
  toggleMic().catch((error) => {
    audioStatusEl.textContent = `Mic failed: ${error.message}`;
    stopMic();
  });
});
bargeButton.addEventListener("click", () => {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({
      type: "vad.speech_start",
      payload: { mode: state.view === "companion" ? "companion" : "chat", system_prompt: state.view === "companion" ? companionSystemPromptInput.value : systemPromptInput.value },
    }));
  }
});
stopAudioButton.addEventListener("click", () => {
  stopAudio();
  updateTtsState(false, "TTS stopped");
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "tts.cancel", payload: {} }));
  }
});
clearButton.addEventListener("click", () => {
  stopAudio();
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "conversation.clear", payload: { mode: state.view === "companion" ? "companion" : "chat" } }));
  } else {
    conversationForMode(state.view === "companion" ? "companion" : "chat").innerHTML = "";
    latencyEl.innerHTML = "";
  }
});
systemPromptInput.addEventListener("input", () => {
  window.clearTimeout(systemPromptTimer);
  systemPromptTimer = window.setTimeout(sendSystemPromptUpdate, 350);
});
systemPromptInput.addEventListener("change", sendSystemPromptUpdate);
ttsPresetEl.addEventListener("change", () => {
  if (!ttsPresetEl.value) {
    return;
  }
  state.pendingTtsPresetId = ttsPresetEl.value;
  state.pendingTtsVoiceId = null;
  withTtsBusy(() => postJson("/api/tts/select", { preset_id: ttsPresetEl.value }));
});
ttsVoiceEl.addEventListener("change", () => {
  if (!ttsVoiceEl.value) {
    return;
  }
  state.pendingTtsVoiceId = ttsVoiceEl.value;
  withTtsBusy(() => postJson("/api/tts/voice", { voice_id: ttsVoiceEl.value }));
});
ttsLoadButton.addEventListener("click", () => {
  withTtsBusy(() => postJson("/api/tts/load"));
});
ttsUnloadButton.addEventListener("click", () => {
  stopAudio();
  withTtsBusy(() => postJson("/api/tts/unload"));
});

tabChatButton.addEventListener("click", () => switchTab("chat"));
tabCompanionButton.addEventListener("click", () => switchTab("companion"));
tabSettingsButton.addEventListener("click", () => switchTab("settings"));

userNameInput.addEventListener("input", saveSettingsDebounced);
aiNameInput.addEventListener("input", saveSettingsDebounced);
additionalSystemPromptInput.addEventListener("input", saveSettingsDebounced);
companionUserNameInput.addEventListener("input", saveCompanionSettingsDebounced);
companionAiNameInput.addEventListener("input", saveCompanionSettingsDebounced);
companionMemoryEnabledInput.addEventListener("change", saveCompanionSettingsDebounced);
companionSystemPromptInput.addEventListener("input", () => {
  saveCompanionSettingsDebounced();
  window.clearTimeout(systemPromptTimer);
  systemPromptTimer = window.setTimeout(sendCompanionPromptUpdate, 350);
});
companionSystemPromptInput.addEventListener("change", sendCompanionPromptUpdate);

const samplerInputs = [
  samplerTemperature, samplerTopK, samplerTopP, samplerMinP,
  samplerRepeatPenalty, samplerFrequencyPenalty, samplerPresencePenalty, samplerMaxTokens,
];
for (const input of samplerInputs) {
  input.addEventListener("input", saveSettingsDebounced);
}
samplerResetButton.addEventListener("click", resetSampler);
for (const input of [companionSamplerTemperature, companionSamplerTopP, companionSamplerMinP, companionSamplerMaxTokens]) {
  input.addEventListener("input", saveCompanionSettingsDebounced);
}
companionSamplerResetButton.addEventListener("click", resetCompanionSampler);
memorySaveButton.addEventListener("click", saveMemory);
memorySummarizeButton.addEventListener("click", summarizeMemory);
memoryClearButton.addEventListener("click", clearMemory);

characterDrop.addEventListener("click", () => characterFile.click());
characterDrop.addEventListener("dragover", (event) => {
  event.preventDefault();
  characterDrop.classList.add("dragover");
});
characterDrop.addEventListener("dragleave", () => {
  characterDrop.classList.remove("dragover");
});
characterDrop.addEventListener("drop", (event) => {
  event.preventDefault();
  characterDrop.classList.remove("dragover");
  const file = event.dataTransfer.files[0];
  if (file) uploadCharacterFile(file);
});
characterFile.addEventListener("change", () => {
  const file = characterFile.files[0];
  if (file) uploadCharacterFile(file);
  characterFile.value = "";
});
characterClearButton.addEventListener("click", clearCharacter);

loadStatus().catch((error) => {
  profileEl.textContent = `Status failed: ${error.message}`;
});
loadMemory();
applyTheme(localStorage.getItem("harness-theme") || "light");
