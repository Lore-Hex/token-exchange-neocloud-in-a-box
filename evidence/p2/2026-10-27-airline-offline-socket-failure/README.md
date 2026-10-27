# Offline Airline Replay: Socket Failure

Original producer: `6a8a4f5f18163612d89bce367912994c37c83792`.
The isolated controller could not reach Docker through the macOS-forwarded
socket mounted inside its Linux container. It stopped at image inspection,
before any scorer or inference. The original journal retains a synthetic
$70.044864 hold and failed cleanup classification; this is not released or
rewritten as success, nor treated as confirmed cleanup by the original run.

A read-only version query reproduced the connection failure. Using Docker's
daemon-native socket instead returned matching client/server API version 1.54.
The subsequent distinct run is retained in
[the reward-expectation attempt](../2026-10-27-airline-offline-reward-expectation/README.md).

All original journal, receipt and stdout bytes are preserved. `run.json` is
the committed recorder's evidence envelope. No API credentials, networking,
paid counting/generation, GPU, serving-profile or production changes occurred.
This failed local run is not benchmark, quality or confidential evidence.
