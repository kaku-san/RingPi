"use strict";

const dots = document.querySelector("#pin-dots");
const result = document.querySelector("#pin-result");
const keys = document.querySelector("#pin-keys");
let digits = "";
let busy = false;

function render() {
  dots.textContent = digits ? "•".repeat(digits.length) : "Enter a 6-digit PIN";
}

function finish(message) {
  digits = "";
  render();
  result.textContent = message;
  busy = false;
}

async function submitPin() {
  if (busy || digits.length !== 6) return;
  busy = true;
  const entered = digits;
  digits = "";
  render();
  result.textContent = "Checking…";
  try {
    const response = await fetch("/api/guest-pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: entered })
    });
    const body = await response.json();
    const done = body.device_label === "DONE" && body.server_confirmed === true
      && body.real_actuation === false && body.hardware_confirmed === false;
    if (done) finish("Simulated open.");
    else if (body.code === "pin_locked_out") finish("Too many tries. The keypad is locked for 15 minutes.");
    else finish("PIN not accepted. No entry was simulated.");
  } catch (error) {
    finish("PIN not accepted. No entry was simulated.");
  }
}

for (const label of ["1", "2", "3", "4", "5", "6", "7", "8", "9", "Clear", "0", "Enter"]) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", () => {
    result.textContent = "";
    if (label === "Clear") digits = "";
    else if (label === "Enter") { submitPin(); return; }
    else if (digits.length < 6) digits += label;
    render();
  });
  keys.append(button);
}
render();
