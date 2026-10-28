# Makefile for StrixHaloClusterDS41Kirei
# Custom HIP Engine on Dual AMD Strix Halo (Ryzen AI Max+ 395, gfx1151)

SHELL := /bin/bash
ROCM_DIR := /home/funboy/StrixHaloClusterGLM/.engine/venv/lib/python3.14/site-packages/_rocm_sdk_devel
ENGINE_DIR := vendor/strix-halo-engine-lab
BUILD_DIR := $(ENGINE_DIR)/build

.PHONY: all build check deploy-node02 serve bench download-status clean

all: check build

build:
	@echo "=== Building Custom HIP Engine (gfx1151) ==="
	export PATH=$(ROCM_DIR)/bin:$(ROCM_DIR)/lib/llvm/bin:$$PATH && \
	export LD_LIBRARY_PATH=$(ROCM_DIR)/lib:$${LD_LIBRARY_PATH:-} && \
	cd $(ENGINE_DIR) && \
	cmake -S . -B build -G Ninja \
	  -DCMAKE_BUILD_TYPE=Release \
	  -DCMAKE_HIP_ARCHITECTURES=gfx1151 \
	  -DCMAKE_HIP_COMPILER=$(ROCM_DIR)/lib/llvm/bin/amdclang++ \
	  -DCMAKE_PREFIX_PATH=$(ROCM_DIR) \
	  -DSTRIX_WARNINGS_AS_ERRORS=OFF \
	  -DSTRIX_ENABLE_HIP_RUNTIME=ON \
	  -DSTRIX_ENABLE_EXPERIMENTAL_Q4K_A8_RUNTIME=ON \
	  -DSTRIX_BUILD_EXPERIMENTAL_Q4K_A8_PROBE=ON \
	  -DSTRIX_USE_OPENSSL_SHA256=ON \
	  -DBUILD_TESTING=OFF && \
	ninja -C build -j 16 strix-deepseek41-rank strix-deepseek41-serve
	mkdir -p bin
	cp $(BUILD_DIR)/strix-deepseek41-rank bin/
	cp $(BUILD_DIR)/strix-deepseek41-serve bin/
	@echo "=== Engine build complete. Binaries installed in bin/ ==="

check:
	@./scripts/check-cluster.sh

deploy-node02:
	@echo "=== Syncing binaries and configs to node02 (02-evo-x3-tb) ==="
	ssh -o BatchMode=yes 02-evo-x3-tb "mkdir -p /home/funboy/StrixHaloClusterDS41Kirei/bin /home/funboy/models/ds41/kirei"
	rsync -avzP bin/ 02-evo-x3-tb:/home/funboy/StrixHaloClusterDS41Kirei/bin/
	rsync -avzP config.json model-profile.json 02-evo-x3-tb:/home/funboy/StrixHaloClusterDS41Kirei/
	@echo "=== Node02 sync completed ==="

serve:
	@./scripts/launch-pair.sh

bench:
	@python3 scripts/benchmark-kirei.py --all

download-status:
	@tail -n 20 download_chain.log 2>/dev/null || echo "No active download log."
	@curl -s -H "Authorization: Bearer d12489558d108cc647158d7385fe8cc10979acba28879c44b3edebe0c375802c" \
	  http://127.0.0.1:18093/v1/downloads/jobs | python3 -c 'import sys, json; [print(f"Job {j[\"id\"]}: {j[\"status\"]} - {j.get(\"repo\",\"-\")} ({j.get(\"bytes\",0)/1024**2:.1f}/{j.get(\"total_bytes\",0)/1024**2:.1f} MB)") for j in json.load(sys.stdin).get("jobs",[])]' 2>/dev/null || true

clean:
	rm -rf $(BUILD_DIR)

