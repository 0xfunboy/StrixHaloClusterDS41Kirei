# StrixHaloClusterDS41Kirei

Implementazione e configurazione su **Dual AMD Strix Halo** (2× Ryzen AI Max+ 395 / Radeon 8060S `gfx1151`, 256 GB Unified Memory totale) per l'esecuzione di **DeepSeek V4.1 Flash** (552B totali, ~8B/16B attivi + Engram tables) basata sul custom HIP engine ad altissime prestazioni sviluppato da **KireiStudios** ([hyukudan/strix-halo-engine-lab](https://github.com/hyukudan/strix-halo-engine-lab)) e sul driver di interconnessione DMA USB4 ([hyukudan/strix-halo-usb4-driver](https://github.com/hyukudan/strix-halo-usb4-driver)).

---

## 🎯 Obiettivi Prestazionali Verificati (KireiStudios 20–26 Ottobre 2026)

| Metrica / Scenario | Target KireiStudios | Note / Configurazione |
|---|---|---|
| **Decode Code (DSpark MTP)** | **70.6 tok/s** | Speculative decoding MTP con vocabolario filtrato e keep-64 |
| **Decode Single User (Generale)** | **~50.0 tok/s** | Richiesta singola chat/ragionamento |
| **Decode Prose** | **42 – 45 tok/s** | Testo discorsivo in lingua naturale |
| **Decode Greedy (Baseline)** | **32 – 35 tok/s** | Decode puro senza speculazione |
| **Prefill Speed** | **~600 tok/s** | Fino a 64K token di contesto, bit-exact rispetto al target |
| **Throughput Aggregato (8 utenti)** | **108 – 113.9 tok/s** | 8 richieste concorrenti in batch interleaving |

---

## 🧩 Architettura e Componenti

1. **Base Model (Q2 Experts):**
   * File: `DeepSeek-V4.1-Flash-Abliterated-Q2-imatrix.gguf` (~341 GiB)
   * Quantizzazione imatrix Q2 per i 552B di esperti MoE sharded tra i due nodi (Rank 0 e Rank 1).

2. **DSpark Speculative Drafter:**
   * Modello: `DeepSeek-V4.1-1017-DSpark-MXFP4.gguf` (~7.6 GB)
   * Vocabolari ottimizzati: `aux/vocab/*.bin` (`core16k` ... `core64k`, `pack-code.bin`, `pack-it.bin`, ecc.)
   * Statistiche di routing: `aux/draft-keep-stats.jsonl` (taglio keep-64: -45% VRAM drafter e +16% velocità su codice).

3. **Dense U6 Overlay:**
   * File: `c-u6.gguf` (~7.6 GB)
   * Sostituisce a runtime 321 tensori densi critici (attention `q_a`, `q_b`, `kv`, `wo_a`, `wo_b`, shared expert e LM head) con una quantizzazione 6-bit uniforme calibrata su imatrix, riducendo la bandwidth di 1.11 GB per token e aumentando la fedeltà e la velocità (+6.6%).

4. **Trellis Dense Mix (EXL3-style):**
   * Pesi: `ours-K4` e `ours-K5` da `hyukudan/DeepSeek-V4.1-Flash-1017-strix-trellis-EXL3`.
   * Accelerazione delle proiezioni dense a 4-bit trellis.

5. **Interconnessione USB4 / Thunderbolt:**
   * Collegamento diretto point-to-point a 40 Gbps:
     * Node 01 (Coordinator / Rank 0): `10.55.0.1/30`
     * Node 02 (Worker / Rank 1): `10.55.0.2/30`
   * Driver: `strix-halo-usb4-driver` per DMA GPU-to-GPU diretto bypassando lo stack socket, con fallback TCP ad altissima velocità su PCIe Thunderbolt.

---

## 🚀 Istruzioni Rapide (Makefile)

```bash
# 1. Verifica la connettività del cluster, GPU, memoria e link thunderbolt
make check

# 2. Compila i binari custom HIP (strix-deepseek41-rank e strix-deepseek41-serve)
make build

# 3. Sincronizza i binari e le configurazioni sul nodo secondario (02-evo-x3-tb)
make deploy-node02

# 4. Monitora la coda automatica dei download (gestita dal downloader Go di HaloClu)
make download-status

# 5. Avvia il cluster dual-node (Rank 0 su locale, Rank 1 via SSH su node02)
make serve

# 6. Esegui la suite di benchmark completa (singolo utente, code, prose, 8 concorrenti)
make bench
```

---

## 📂 Struttura del Repository

```text
StrixHaloClusterDS41Kirei/
├── Makefile                          # Comandi di build, check, deploy e benchmark
├── README.md                         # Documentazione dettagliata
├── config.json                       # Configurazione gateway HaloClu (porta 18231)
├── model-profile.json                # Profilo modello standard strix-model-profile-v1
├── bin/                              # Binari compilati per RDNA 3.5 (gfx1151)
│   ├── strix-deepseek41-rank         # Processo worker MoE tensor-parallel
│   └── strix-deepseek41-serve        # Server API OpenAI/Anthropic compatibile
├── scripts/
│   ├── check-cluster.sh              # Verifica integrità hardware e rete del cluster
│   ├── deploy-node02.sh              # Deploy su 02-evo-x3-tb
│   ├── launch-pair.sh                # Avvio coordinato dual-node
│   ├── benchmark-kirei.py            # Suite di benchmark e verifica token
│   └── download-manager-queue.py     # Gestore a catena dei download HaloClu API
└── vendor/
    ├── strix-halo-engine-lab/        # Engine C++/HIP di KireiStudios
    └── strix-halo-usb4-driver/       # Driver USB4 direct DMA
```

