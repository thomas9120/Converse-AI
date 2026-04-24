const state = {
  ws: null,
  assistantNode: null,
  audioQueue: [],
  playing: false,
  playbackStarted: false,
  playbackContext: null,
  nextAudioTime: 0,
  scheduledSources: [],
  providersReady: false,
  profileAudio: { sample_rate: 16000, channels: 1, frame_ms: 30 },
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
const micButton = document.querySelector("#mic");
const bargeButton = document.querySelector("#barge");
const sendButton = document.querySelector("#send");
const deviceSelect = document.querySelector("#device");
const levelEl = document.querySelector("#level");
const vadStateEl = document.querySelector("#vad-state");
const asrStateEl = document.querySelector("#asr-state");
const ttsStateEl = document.querySelector("#tts-state");
const audioStatusEl = document.querySelector("#audio-status");
const composer = document.querySelector("#composer");
const textInput = document.querySelector("#text");

async function loadStatus() {
  const response = await fetch("/api/status");
  const status = await response.json();
  state.profileAudio = status.profile.audio || state.profileAudio;
  profileEl.textContent = `${status.profile.name} - ${status.profile.description}`;
  renderProviders(status.providers);
  await loadInputDevices();
}

function renderProviders(providers) {
  providersEl.innerHTML = "";
  state.providersReady = providers.every((provider) => provider.ready);
  updateSendState();
  for (const provider of providers) {
    const node = document.createElement("article");
    node.className = "provider";
    node.dataset.ready = provider.ready ? "true" : "false";
    node.innerHTML = `
      <strong>${provider.kind}: ${provider.name}</strong>
      <span>${provider.ready ? "ready" : "not ready"}</span>
      <p>${provider.message}</p>
    `;
    providersEl.appendChild(node);
  }
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
    addSystemMessage("Connected");
  });
  state.ws.addEventListener("close", () => {
    connectButton.textContent = "Connect";
    sendButton.disabled = true;
    bargeButton.disabled = true;
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
  if (event.type === "providers.status") {
    renderProviders(payload.providers);
  } else if (event.type === "turn.started") {
    latencyEl.innerHTML = "";
    state.playbackStarted = false;
  } else if (event.type === "asr.transcript" && payload.final) {
    updateAsrState(false, "ASR done");
    setLatency("ASR final", payload.latency_ms);
    addMessage("user", payload.text);
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
    state.assistantNode = addMessage("assistant", "");
  } else if (event.type === "llm.token") {
    if (!state.assistantNode) {
      state.assistantNode = addMessage("assistant", "");
    }
    state.assistantNode.textContent = payload.accumulated;
    conversationEl.scrollTop = conversationEl.scrollHeight;
  } else if (event.type === "tts.first_chunk") {
    updateTtsState(true, "TTS playing");
    setLatency("TTS first chunk", payload.latency_ms);
  } else if (event.type === "tts.audio") {
    enqueueAudio(payload.mime_type, payload.data, payload.latency_ms);
  } else if (event.type === "tts.progress") {
    updateTtsState(true, payload.message || `TTS ${payload.stage}`);
  } else if (event.type === "tts.error") {
    updateTtsState(false, `TTS error: ${payload.message}`);
  } else if (event.type === "audio.input_level") {
    updateInputLevel(payload.rms, payload.peak);
    audioStatusEl.textContent = `Mic ${Math.round((payload.rms || 0) * 100)}% · ${payload.received_frames} frames`;
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
    state.assistantNode = null;
    updateTtsState(false, "TTS idle");
  } else if (event.type === "turn.error") {
    addSystemMessage(`Error: ${payload.message}`);
  } else if (event.type === "tts.cancelled") {
    stopAudio();
    updateTtsState(false, "TTS cancelled");
  }
}

function addMessage(kind, text) {
  const node = document.createElement("div");
  node.className = `message ${kind}`;
  node.textContent = text;
  conversationEl.appendChild(node);
  conversationEl.scrollTop = conversationEl.scrollHeight;
  return node;
}

function addSystemMessage(text) {
  addMessage("system", text);
}

function updateSendState() {
  const connected = state.ws && state.ws.readyState === WebSocket.OPEN;
  sendButton.disabled = !connected || !state.providersReady;
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
  let row = latencyEl.querySelector(`[data-label="${label}"]`);
  if (!row) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.dataset.label = label;
    latencyEl.append(dt, dd);
    row = dd;
  }
  row.textContent = `${value} ms`;
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
  audioStatusEl.textContent = `Mic active · ${context.sampleRate} Hz browser input`;
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
  vadStateEl.textContent = `${active ? "VAD speaking" : "VAD idle"} · ${prob}`;
}

function updateAsrState(active, text) {
  asrStateEl.dataset.active = active ? "true" : "false";
  asrStateEl.textContent = text;
}

function updateTtsState(active, text) {
  ttsStateEl.dataset.active = active ? "true" : "false";
  ttsStateEl.textContent = text;
}

function enqueueAudio(mimeType, base64, latencyMs) {
  state.audioQueue.push({ mimeType, base64, latencyMs });
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
      const audioBuffer = await decodeAudioChunk(context, item);
      const source = context.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(context.destination);

      const now = context.currentTime;
      const startAt = Math.max(now + 0.035, state.nextAudioTime);
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
  return await context.decodeAudioData(bytes.buffer);
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

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!text || !state.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  latencyEl.innerHTML = "";
  state.playbackStarted = false;
  state.ws.send(JSON.stringify({ type: "user.text", payload: { text } }));
  textInput.value = "";
});

connectButton.addEventListener("click", connect);
micButton.addEventListener("click", () => {
  toggleMic().catch((error) => {
    audioStatusEl.textContent = `Mic failed: ${error.message}`;
    stopMic();
  });
});
bargeButton.addEventListener("click", () => {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "vad.speech_start", payload: {} }));
  }
});

loadStatus().catch((error) => {
  profileEl.textContent = `Status failed: ${error.message}`;
});
