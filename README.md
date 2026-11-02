<p align="center">
  <img src="https://img.shields.io/badge/Platform-Dual%20AMD%20Strix%20Halo-ED1C24?style=flat-square&logo=amd&logoColor=white" alt="AMD Strix Halo">
  <img src="https://img.shields.io/badge/Architecture-RDNA%203.5%20gfx1151-black?style=flat-square&logo=amd&logoColor=ED1C24" alt="gfx1151">
  <img src="https://img.shields.io/badge/Engine-KireiStudios%20HIP%20C%2B%2B-blue?style=flat-square" alt="KireiStudios HIP">
  <img src="https://img.shields.io/badge/Model-DeepSeek%20V4.1%20Flash%20(552B)-8A2BE2?style=flat-square" alt="DeepSeek V4.1">
  <img src="https://img.shields.io/badge/Interconnect-40Gbps%20USB4%2FDMA-0078D7?style=flat-square&logo=thunderbolt&logoColor=white" alt="USB4 Interconnect">
  <img src="https://img.shields.io/badge/Decode-50.0%20%7C%2070.6%20tok%2Fs-00ff66?style=flat-square" alt="Decode Speed">
  <img src="https://img.shields.io/badge/Context-131%2C072%20Tokens-orange?style=flat-square" alt="131k Context">
  <img src="https://img.shields.io/badge/License-0xfunboy%20Non--Commercial-purple?style=flat-square" alt="0xfunboy License">
</p>

<h1 align="center">⚡ StrixHaloClusterDS41Kirei</h1>

<p align="center">
  <b>Dual-Node Tensor-Parallel Distributed MoE Runtime for DeepSeek V4.1 Flash</b><br>
  <i>Engineered for paired AMD Strix Halo APUs (2× Ryzen AI Max+ 395, 256 GB LPDDR5X, 40 Gbps USB4 DMA).</i>
</p>

<p align="center">
  <a href="#overview"><strong>Overview</strong></a> ·
  <a href="#performance-targets">Performance Benchmarks</a> ·
  <a href="#deep-architecture">Architecture & Components</a> ·
  <a href="#cluster-interconnect">USB4 Interconnect</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#service-management">Service Management</a> ·
  <a href="#license">License</a>
</p>

---

## 🎯 Overview

**StrixHaloClusterDS41Kirei** is the production-grade, distributed Tensor-Parallel (TP2) inference environment running **DeepSeek V4.1 Flash** (552B total parameters, ~8B/16B active per token + Engram tables) across a paired cluster of two **AMD Strix Halo** workstations:

- **Node 01 (Coordinator / Rank 0)**: `01-EVO-X3` (`10.55.0.1/30` / `192.168.178.77`)
- **Node 02 (Worker / Rank 1)**: `02-EVO-X3` (`10.55.0.2/30` / `192.168.178.22`)
- **Interconnect**: Point-to-point 40 Gbps PCIe Thunderbolt / USB4 DMA with zero-copy buffer passing.

Based on the bleeding-edge custom HIP runtime developed by **KireiStudios** ([hyukudan/strix-halo-engine-lab](https://github.com/hyukudan/strix-halo-engine-lab)) and paired with the specialized `strix-halo-usb4-driver`, this cluster delivers up to **70.6 tok/s decode** with speculative decoding and **~600 tok/s prefill** up to 131K context.

---

## 📊 Performance Benchmarks (Verified on Cluster)

| Metric / Workload Scenario | Measured Output | Configuration Details |
|---|:---:|---|
| **Code Generation (DSpark MTP)** | **70.6 tok/s** | Speculative decoding with keep-64 filtered code vocabulary |
| **Single-User General Chat** | **50.0 tok/s** | Standard reasoning and prose generation |
| **Technical Prose / Translation** | **42 – 45 tok/s** | Natural language synthesis |
| **Greedy Pure Decode (Baseline)** | **32 – 35 tok/s** | Raw decode without speculative draft |
| **Context Prefill Throughput** | **~600 tok/s** | Device-path prefill, bit-exact up to 64K–131K tokens |
| **Multi-User Throughput (8 streams)**| **108 – 114 tok/s** | Batched interleaving across dual-GPU compute queues |

---

## 🧩 Architecture & Components

```
                01-EVO-X3 (Rank 0 - Coordinator)
 ┌─────────────────────────────────────────────────────────────┐
 │ - Base Model: DeepSeek-V4.1-Flash-Abliterated-Q2-imatrix    │
 │ - DSpark Drafter: DeepSeek-V4.1-1017-DSpark-MXFP4.gguf      │
 │ - Dense U6 Overlay: c-u6.gguf (Attention + LM Head)         │
 │ - Trellis EXL3 Dense Projections: ours-K4 / ours-K5         │
 │ - Context Capacity: 131,072 Tokens (f16 KV Cache)           │
 │ - ROCm SDK / HIP Engine: .venv-rocm (C++ HIP binary)        │
 └──────────────────────────────┬──────────────────────────────┘
                                │
                                │ 40 Gbps USB4 DMA Link
                                │ (10.55.0.1 <-> 10.55.0.2)
                                │
                02-EVO-X3 (Rank 1 - Worker)
 ┌─────────────────────────────────────────────────────────────┐
 │ - Worker Daemon: strix-deepseek41-serve (Rank 1)            │
 │ - Mirrored MoE Shards: Q2_K imatrix + Kirei auxiliary packs │
 │ - Memory Allocation: ~118 GB VRAM dedicated to Rank 1       │
 └─────────────────────────────────────────────────────────────┘
```

1. **Base MoE Sharded Weights (`ds41-abliterated-q2-imatrix`)**:
   `DeepSeek-V4.1-Flash-Abliterated-Q2-imatrix.gguf` (~341 GiB). Uncensored, calibrated imatrix Q2 MoE experts partitioned evenly across Node 01 and Node 02.
2. **DSpark Speculative Drafter (`DSpark-MXFP4`)**:
   `DeepSeek-V4.1-1017-DSpark-MXFP4.gguf` (~7.6 GB) utilizing optimized sparse vocabularies (`aux/vocab/*.bin`). With the `keep-64` routing filter, drafter VRAM is reduced by 45% while boosting code decode speed by +16%.
3. **Dense U6 Overlay (`c-u6.gguf`)**:
   Replaces 321 critical dense tensors at runtime (attention `q_a`, `q_b`, `kv`, `wo_a`, `wo_b`, shared experts, and LM head) with uniform 6-bit quantization, saving 1.11 GB/token of memory bandwidth and improving accuracy.
4. **Trellis Dense Mix (EXL3)**:
   Weights in `ours-K4` and `ours-K5` accelerate dense projections with custom 4-bit trellis kernels.
5. **USB4 / Thunderbolt Direct DMA**:
   Dedicated direct link at `10.55.0.1/30` ↔ `10.55.0.2/30`. Uses kernel-level DMA bypassing standard TCP socket overhead with instant PCIe fallback.

---

## 🚀 Quickstart & Commands

```bash
# 1. Check cluster hardware, Thunderbolt link, memory and ROCm
make check

# 2. Build or verify the C++ HIP engine binaries
make build

# 3. Synchronize binaries and configuration to Node 02
make deploy-node02

# 4. Launch the dual-node cluster (Rank 0 locally, Rank 1 on 02 via SSH)
make serve

# 5. Run complete benchmark suite (single-user, code, multi-stream)
make bench
```

---

## 🔧 Service Management

The engine is managed via user-level systemd:

```bash
# Start cluster service
systemctl --user start strix-ds41-kirei.service

# Inspect live cluster logs
journalctl --user -fu strix-ds41-kirei.service

# Stop cluster cleanly across both nodes
systemctl --user stop strix-ds41-kirei.service
```

### Script Orchestration:
- `scripts/run-cluster.sh`: Foreground runner invoked by systemd. Kills zombie instances, launches the Rank 1 worker on `02-evo-x3-tb`, and starts the Rank 0 coordinator on `01-EVO-X3:18231`.
- `scripts/launch-worker.sh`: Remote bootstrap for Rank 1 on Node 02.

---

## 📜 License

Distributed under the **0xfunboy Non-Commercial License**. See [LICENSE.md](LICENSE.md) for full terms.  
Commercial deployment and redistribution require written authorization from `@0xfunboy`.
