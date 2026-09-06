const portSelect = document.getElementById("portSelect");
const refreshBtn = document.getElementById("refreshBtn");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const connectionStatus = document.getElementById("connectionStatus");
const leadStatus = document.getElementById("leadStatus");
const bpmValue = document.getElementById("bpmValue");
const modelLabel = document.getElementById("modelLabel");
const rateLabel = document.getElementById("rateLabel");
const message = document.getElementById("message");
const canvas = document.getElementById("ecgCanvas");
const ctx = canvas.getContext("2d");

const maxSamples = 2500;
const samples = [];
let socket = null;

function setText(el, text, className = "") {
  el.textContent = text;
  el.className = className;
}

async function loadPorts() {
  portSelect.innerHTML = "";
  try {
    const response = await fetch("/api/ports");
    const data = await response.json();
    if (data.error) {
      message.textContent = data.error;
    }
    if (!data.ports || data.ports.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Sin puertos disponibles";
      portSelect.appendChild(option);
      return;
    }
    for (const port of data.ports) {
      const option = document.createElement("option");
      option.value = port.device;
      option.textContent = `${port.device} - ${port.description}`;
      portSelect.appendChild(option);
    }
  } catch (error) {
    message.textContent = `No se pudieron cargar puertos: ${error}`;
  }
}

function connectSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
  socket.onopen = () => {
    setText(connectionStatus, "Web lista", "ok");
  };
  socket.onclose = () => {
    setText(connectionStatus, "Desconectado", "bad");
  };
  socket.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "sample") {
      handleSample(data);
    } else if (data.type === "classification") {
      handleClassification(data);
    } else if (data.type === "status") {
      setText(connectionStatus, data.connected ? "Conectado" : "Desconectado", data.connected ? "ok" : "bad");
      message.textContent = data.message;
    } else if (data.type === "error") {
      setText(connectionStatus, "Error", "bad");
      message.textContent = data.message;
    }
  };
}

function handleSample(sample) {
  samples.push(sample.ecg_raw);
  if (samples.length > maxSamples) {
    samples.shift();
  }
  setText(leadStatus, sample.lead_ok ? "Conectados" : "Desconectados", sample.lead_ok ? "ok" : "bad");
  drawChart();
}

function handleClassification(data) {
  bpmValue.textContent = data.bpm ? data.bpm.toFixed(1) : "--";
  setText(modelLabel, data.model_label || "Calculando", data.model_class === 0 ? "ok" : data.model_class === 1 ? "warn" : "");
  const rateClass = data.rate_label === "Normal" ? "ok" : data.rate_label === "Calculando" ? "" : "warn";
  setText(rateLabel, data.rate_label || "Calculando", rateClass);
  message.textContent = data.message || "";
}

function drawChart() {
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = "#e1e7ea";
  ctx.lineWidth = 1;
  for (let x = 0; x <= width; x += width / 10) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = 0; y <= height; y += height / 6) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  if (samples.length < 2) {
    return;
  }

  const minVal = Math.min(...samples);
  const maxVal = Math.max(...samples);
  const range = Math.max(1, maxVal - minVal);
  ctx.strokeStyle = "#176b87";
  ctx.lineWidth = 2;
  ctx.beginPath();
  samples.forEach((value, index) => {
    const x = (index / (maxSamples - 1)) * width;
    const y = height - ((value - minVal) / range) * height;
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
}

async function startReading() {
  const port = portSelect.value;
  if (!port) {
    message.textContent = "Selecciona un puerto COM.";
    return;
  }
  const response = await fetch("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ port, baudrate: 115200 }),
  });
  const data = await response.json();
  if (!response.ok) {
    message.textContent = data.detail || "No se pudo iniciar la lectura.";
  }
}

async function stopReading() {
  await fetch("/api/stop", { method: "POST" });
}

refreshBtn.addEventListener("click", loadPorts);
startBtn.addEventListener("click", startReading);
stopBtn.addEventListener("click", stopReading);

connectSocket();
loadPorts();
drawChart();
