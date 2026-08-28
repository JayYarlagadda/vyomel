# 13 — Environment: Verified Facts, Constraints, and Setup Runbook

Status: **Verified 2026-08-28**

This document records what was **actually measured** on the development machine, not what was assumed. Several architecture decisions depend directly on these numbers, so re-verify before changing hardware.

---

## 1. Verified machine facts

Captured 2026-08-28 by direct inspection.

| Property | Value | Verified by |
|---|---|---|
| OS | Windows 11, build 10.0.26200 | `$PSVersionTable` / system info |
| Shell | PowerShell | — |
| Host RAM | 15.77 GB | `Win32_ComputerSystem.TotalPhysicalMemory` |
| CPU cores (visible to WSL) | 8 | `nproc` |
| GPU | NVIDIA GeForce **MX330**, **2048 MiB** VRAM | `nvidia-smi` |
| GPU driver / CUDA | 532.09 / CUDA 12.1 | `nvidia-smi` |
| Python (Windows) | 3.14.0 (default), **3.13.5**, 3.7 | `py -0p` |
| pip | 25.1.1 | `py -3.13 -m pip --version` |
| Git | `C:\Program Files\Git\cmd\git.exe` | `Get-Command git` |
| Node / npm | **not installed** | `Get-Command node` |
| Docker (Windows host) | **not installed** | `Get-Command docker` |
| WSL | WSL2, Ubuntu 22.04.3 LTS, `Running` | `wsl -l -v` |
| Python (WSL) | 3.10.12 | `python3 --version` |
| Docker (WSL) | **Engine 29.1.3, working** | `docker info` |
| Docker Compose plugin (WSL) | **not installed** | `docker compose version` → unknown command |
| WSL RAM | 7.8 GB | `free -m` |
| Target drive | `D:\` — 151.7 GB free | `Get-PSDrive` |

---

## 2. Constraints that follow from these facts

These are the load-bearing consequences. Each one changed a design decision.

### C-1 — Local vLLM is not possible on this machine

The MX330 is a Pascal GP108 part with **compute capability 6.1 and 2 GB VRAM**. vLLM requires compute capability **≥ 7.0** and realistically ≥ 8 GB VRAM for any useful model. There is no configuration in which vLLM runs on this GPU.

**Decision (ADR-0006):** The model layer is built as a provider abstraction with a **vLLM-compatible OpenAI backend adapter**. vLLM is exercised for real on a **rented GPU** (RunPod / Vast.ai / Lambda, ~$0.30–0.80/hr for an A10G or L4) during dedicated benchmarking sessions, and the results are committed to `evals/results/`. Day-to-day development uses CPU-hosted GGUF models via `llama.cpp`/Ollama and hosted APIs.

This is **not** a compromise on the resume claim: "designed a self-hosted AI infrastructure layer with vLLM" is satisfied by a real deployment (Docker + K8s manifests), real adapter code, and real reproducible benchmark numbers — even though the GPU is rented rather than owned. What we must **not** do is claim throughput numbers we never measured.

### C-2 — Local models must be CPU-and-8GB-friendly

With 15.7 GB host RAM (7.8 GB inside WSL), local inference targets are quantized 3B–8B GGUF models (Q4_K_M): roughly 2–5 GB resident. Anything larger routes to cloud. This directly sets the **model router**'s local-eligibility rule.

### C-3 — Infrastructure runs in WSL, application runs on Windows

Docker Engine works inside WSL2 and is the only container runtime available. WSL2 forwards published container ports to Windows `localhost`, so the FastAPI app running under Windows Python 3.13 can reach Postgres on `localhost:5432` and Redis on `localhost:6379` with no extra networking.

**Decision (ADR-0002):** Postgres+pgvector and Redis run as WSL Docker containers. Application, workers, CLI, and tests run on Windows Python 3.13.

### C-4 — Python 3.13, not 3.14

Python 3.14 is the machine default but has thin wheel coverage for the scientific/ML stack (notably `torch`, and some C-extension DB and parsing libraries). **All Astra tooling pins Python 3.13.5.** The virtualenv is created explicitly with `py -3.13`.

### C-5 — Desktop control targets Windows first

The original vision described macOS Accessibility API and AppleScript. This machine is Windows, so the **primary desktop backend is Windows UI Automation (UIA)** via `pywinauto` / `uiautomation`, with `pygetwindow` + `mss` for window and screen capture. The `DesktopBackend` interface is defined so a macOS `AXUIElement` backend can be added later without touching the planner or runtime.

### C-6 — No Node.js yet

Playwright's Python bindings ship their own browser binaries and do **not** require system Node. Node is only needed if/when a desktop UI (Tauri/Electron) or a browser extension is built. Deferred to M11.

### C-8 — WSL idles out and takes the containers with it

**Observed during M0 setup, and it will waste hours if not written down.** WSL2 shuts the VM down once its last process exits. Because Docker Engine runs inside WSL, an idle timeout stops Postgres and Redis, and the next command from Windows fails with `ConnectionRefusedError: [WinError 1225]`. The containers restart on the next `wsl` invocation, which makes the failure look intermittent and unrelated to WSL.

Symptom to recognize: `docker ps` always reports `Up 2 seconds` no matter when you look.

**Fix:** hold the VM open with a long-lived process.

```powershell
# Run once per session, detached. Keeps the WSL VM (and the containers) alive.
Start-Process -WindowStyle Hidden wsl -ArgumentList '-d','Ubuntu','-e','sleep','infinity'
```

`infra/scripts/up.ps1` does this automatically and then waits for both containers to report healthy. Alternatively, set `vmIdleTimeout=-1` under `[experimental]` in `%UserProfile%\.wslconfig` and run `wsl --shutdown` once — machine-wide, so the keepalive is preferred for a per-project fix.

### C-7 — Kubernetes is a deployment artifact, not a dev dependency

16 GB RAM cannot comfortably host a real K8s cluster alongside development. K8s manifests + Helm chart are authored and validated with `kubeval`/`helm template`, and applied for real against a **kind** cluster in CI or a short-lived cloud cluster during a dedicated milestone (M13). This keeps the Kubernetes claim honest without wrecking the dev loop.

---

## 3. Port allocation

Fixed to avoid collisions with anything else on the machine. Non-standard ports are chosen deliberately.

| Service | Port | Notes |
|---|---|---|
| Astra API (FastAPI) | `8080` | Windows host process |
| Postgres 17 + pgvector | `55432` | WSL Docker; non-default to avoid clashing with any local Postgres |
| Redis 7 | `56379` | WSL Docker; non-default |
| Ollama / llama.cpp server | `11434` | optional, local model serving |
| vLLM (remote) | `8000` | on rented GPU host, reached over SSH tunnel |
| Prometheus | `9090` | M10 |
| Grafana | `3000` | M10 |
| Jaeger UI | `16686` | M10 |
| OTLP gRPC collector | `4317` | M10 |

---

## 4. Setup runbook

Run these in order. Each step has a verification command; do not proceed past a failed verification.

### Step 1 — Install the Docker Compose plugin in WSL

Docker here came from Ubuntu's `docker.io` package rather than Docker's own repository, so `apt-get install docker-compose-plugin` fails with *"Unable to locate package"*. Install the plugin binary directly:

```bash
wsl -e bash -c "mkdir -p ~/.docker/cli-plugins && \
  curl -SL https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-x86_64 \
    -o ~/.docker/cli-plugins/docker-compose && \
  chmod +x ~/.docker/cli-plugins/docker-compose"
# verify
wsl -e bash -c "docker compose version"     # -> Docker Compose version v2.32.4
```

### Step 2 — Start data infrastructure

```powershell
.\infra\scripts\up.ps1
```

This starts the containers *and* the WSL keepalive (constraint C-8). The raw equivalent is:

```bash
wsl -e bash -c "cd /mnt/d/Astra/infra && docker compose up -d"
wsl -e bash -c "docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

### Step 3 — Create the Windows virtualenv

```powershell
cd D:\Astra
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# verify
.\.venv\Scripts\python.exe -c "import astra; print(astra.__version__)"
```

### Step 4 — Configure secrets

```powershell
Copy-Item .env.example .env
# then edit .env and fill in provider API keys
```

`.env` is git-ignored and must never be committed. See `06-SECURITY-PERMISSIONS.md` §6.

### Step 5 — Apply database migrations

```powershell
.\.venv\Scripts\alembic.exe upgrade head
# verify
.\.venv\Scripts\python.exe -m astra.cli db check
```

### Step 6 — Run the test suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

### Step 7 — Start the API

```powershell
.\.venv\Scripts\python.exe -m astra.cli serve
# verify, in another shell:
curl http://localhost:8080/healthz
```

---

## 5. Re-verification script

`infra/scripts/doctor.py` re-runs every check in §1 and prints a pass/fail table. Run it after any machine change, driver update, or when something behaves unexpectedly. It is also wired into `make doctor`.
