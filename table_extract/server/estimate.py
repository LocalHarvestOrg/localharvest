"""How much memory a model needs, and whether this computer has it (docs/gui-design.md, "Model picker").

Before download, from the GGUF header: weights + projector + KV cache at the
chosen context + compute buffers.
The KV cache follows llama.cpp's layout: layers with a sliding window
(Gemma) only cache the window, and the linear-attention layers of hybrid
models (Qwen3.5) keep a small fixed state instead of a cache. On the local
Qwen3.5-9B and Gemma 4 12B files this matches llama-fit-params' context
figure to the MiB.

After download, `llama-fit-params` gives llama.cpp's own figures (fit_check);
it leaves out the vision projector, which is added on top.

Verdicts: "gpu" (fits in the memory the GPU can use), "partial" (part of the
model runs from system memory: slower), "cpu" (no GPU: runs on the
processor), "too_big", or "unknown" (no GPU information yet, e.g. before
llama.cpp is installed).
"""

import os
import re
import subprocess
from typing import Any, Dict, List, Sequence

MIB = 1024 * 1024
GIB = 1024 * MIB
UBATCH = 512  # llama-server's default physical batch
KV_TYPE_BYTES = 2  # f16 cache, llama-server's default
CELL_PADDING = 256
# Kept free on each GPU, for the display and other programs (llama.cpp's own --fit keeps 1 GiB)
GPU_MARGIN = 512 * MIB
# Vision encoder buffers for one page image, on top of the projector's weights
PROJECTOR_COMPUTE = 300 * MIB
# Share of system memory a model may use outside the GPU
RAM_SHARE = 0.7
# macOS lets the GPU use about this share of unified memory (recommendedMaxWorkingSetSize)
APPLE_GPU_SHARE = 0.74
DEFAULT_VOCAB = 152064

# Sliding-window layer period for architectures whose files carry no per-layer pattern:
# every period-th layer has full attention, the others only cache the window
SWA_PERIOD = {"gemma2": 2, "gemma3": 6, "cohere2": 4}


def _per_layer(value: Any, n_layer: int) -> List[Any]:
    if isinstance(value, list):
        return (value + [value[-1] if value else 0] * n_layer)[:n_layer]
    return [value] * n_layer


def hparams(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """What the memory estimate needs from a model's GGUF header."""
    arch = metadata.get("general.architecture")
    if not arch:
        raise ValueError("the GGUF header names no architecture")

    def get(key: str, default: Any = None) -> Any:
        return metadata.get(f"{arch}.{key}", default)

    n_layer = int(get("block_count") or 0)
    n_embd = int(get("embedding_length") or 0)
    n_head = get("attention.head_count") or 1
    n_head_first = (n_head[0] if isinstance(n_head, list) and n_head else n_head) or 1
    key_length = get("attention.key_length") or (n_embd // n_head_first if n_embd else 128)
    value_length = get("attention.value_length") or key_length

    window = get("attention.sliding_window")
    pattern = get("attention.sliding_window_pattern")
    if isinstance(pattern, list):
        swa = [bool(p) for p in _per_layer(pattern, n_layer)]
    elif window:
        period = pattern if isinstance(pattern, int) and pattern > 0 else SWA_PERIOD.get(arch)
        swa = [bool(period) and (i + 1) % period != 0 for i in range(n_layer)]
    else:
        swa = [False] * n_layer

    interval = get("full_attention_interval")
    recurrent = [bool(interval) and (i + 1) % interval != 0 for i in range(n_layer)]

    ssm = None
    if get("ssm.inner_size"):
        ssm = {k: int(get(f"ssm.{k}") or 0) for k in ("inner_size", "state_size", "conv_kernel", "group_count")}

    return {
        "architecture": arch,
        "layers": n_layer,
        "embedding_length": n_embd,
        "head_count_kv": [int(h or 0) for h in _per_layer(get("attention.head_count_kv", n_head), n_layer)],
        "key_length": int(key_length),
        "value_length": int(value_length),
        "key_length_swa": int(get("attention.key_length_swa") or key_length),
        "value_length_swa": int(get("attention.value_length_swa") or value_length),
        "sliding_window": int(window or 0),
        "swa_layers": swa,
        "recurrent_layers": recurrent,
        "ssm": ssm,
        "causal": get("attention.causal", True) is not False,
        "vocab_size": int(metadata.get("tokenizer.ggml.tokens.count") or DEFAULT_VOCAB),
        "context_length": int(get("context_length") or 0),
    }


def _pad(cells: int) -> int:
    return -(-cells // CELL_PADDING) * CELL_PADDING


def kv_cache_bytes(hp: Dict[str, Any], context: int) -> int:
    """KV cache plus recurrent state for one sequence (-np 1)."""
    if not hp["causal"]:
        return 0  # encoder-only models keep no cache
    total = 0
    for i in range(hp["layers"]):
        heads = hp["head_count_kv"][i]
        if hp["recurrent_layers"][i]:
            ssm = hp["ssm"]
            if ssm:
                conv = max(ssm["conv_kernel"] - 1, 0) * (ssm["inner_size"] + 2 * ssm["group_count"] * ssm["state_size"])
                total += 4 * (conv + ssm["inner_size"] * ssm["state_size"])  # f32
            continue
        if heads == 0:
            continue
        if hp["swa_layers"][i]:
            cells = min(_pad(context), _pad(hp["sliding_window"] + UBATCH))
            per_cell = heads * (hp["key_length_swa"] + hp["value_length_swa"])
        else:
            cells = _pad(context)
            per_cell = heads * (hp["key_length"] + hp["value_length"])
        total += cells * per_cell * KV_TYPE_BYTES
    return total


def compute_bytes(hp: Dict[str, Any], context: int) -> int:
    """llama.cpp's compute buffers: mostly one batch of logits, plus a little per context token.

    Fitted to llama-fit-params on Qwen3.5-9B and Gemma 4 12B at 16k and 32k (within 2%).
    """
    return UBATCH * hp["vocab_size"] * 4 + 100 * MIB + context * 3 * 1024


def estimate(model_bytes: int, hp: Dict[str, Any], context: int, mmproj_bytes: int = 0) -> Dict[str, Any]:
    parts = {
        "weights": model_bytes,
        "projector": mmproj_bytes + (PROJECTOR_COMPUTE if mmproj_bytes else 0),
        "kv_cache": kv_cache_bytes(hp, context),
        "compute": compute_bytes(hp, context),
    }
    return {"context": context, "parts": parts, "total_bytes": sum(parts.values())}


def budget(hardware: Dict[str, Any]) -> Dict[str, Any]:
    """Memory available to a model, from the hardware probe (hardware.probe)."""
    gpus = [d for d in hardware.get("devices", []) if d.get("gpu")]
    ram = int(hardware.get("memory", {}).get("total_bytes") or 0)
    unified = bool(hardware.get("system", {}).get("unified_memory"))
    gpu = sum(max(d["memory_total_bytes"] - GPU_MARGIN, 0) for d in gpus)
    source = "llama.cpp" if gpus else None
    if not gpus and unified and not hardware.get("llama_server", {}).get("found"):
        # Before llama.cpp is installed: macOS's usual share of unified memory
        gpu, source = max(int(ram * APPLE_GPU_SHARE) - GPU_MARGIN, 0), "estimated"
    known = bool(gpus) or source == "estimated" or bool(hardware.get("llama_server", {}).get("found"))
    return {"gpu_bytes": gpu, "ram_bytes": ram, "unified_memory": unified, "has_gpu": gpu > 0,
            "gpu_source": source, "known": known}


def verdict(need: int, mem: Dict[str, Any]) -> str:
    if not mem["known"]:
        return "too_big" if need > mem["ram_bytes"] * RAM_SHARE else "unknown"
    if mem["has_gpu"] and need <= mem["gpu_bytes"]:
        return "gpu"
    if mem["unified_memory"]:
        # One pool: past the GPU's share, llama.cpp runs the rest on the CPU from the same memory
        return "partial" if need <= mem["ram_bytes"] * 0.85 else "too_big"
    if mem["has_gpu"]:
        return "partial" if need <= mem["gpu_bytes"] + mem["ram_bytes"] * RAM_SHARE else "too_big"
    return "cpu" if need <= mem["ram_bytes"] * RAM_SHARE else "too_big"


def assess(model_bytes: int, hp: Dict[str, Any], context: int, mem: Dict[str, Any],
           mmproj_bytes: int = 0) -> Dict[str, Any]:
    """Estimate and verdict."""
    result = estimate(model_bytes, hp, context, mmproj_bytes)
    return {**result, "verdict": verdict(result["total_bytes"], mem)}


# Flags that change memory use and that llama-fit-params accepts (it refuses server-only flags)
FIT_FLAGS = {"-c", "--ctx-size", "-np", "--parallel", "-ctk", "--cache-type-k", "-ctv", "--cache-type-v",
             "-fa", "--flash-attn", "-b", "--batch-size", "-ub", "--ubatch-size", "--swa-full", "-kvu", "--kv-unified"}
FIT_SWITCHES = {"--swa-full", "-kvu", "--kv-unified"}
FIT_LINE = re.compile(r"^(?P<device>\S+)\s+(?P<model>\d+)\s+(?P<context>\d+)\s+(?P<compute>\d+)\s*$")
NGL = re.compile(r"(?:^|\s)-ngl\s+(-?\d+)")


def fit_flags(flags: Sequence[str]) -> List[str]:
    kept, i = [], 0
    while i < len(flags):
        flag = flags[i]
        if flag in FIT_SWITCHES:
            kept.append(flag)
        elif flag in FIT_FLAGS and i + 1 < len(flags):
            kept += [flag, flags[i + 1]]
            i += 1
        i += 1
    return kept


def _run(command: List[str], timeout: float) -> subprocess.CompletedProcess:
    options: Dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, **options)


def fit_check(fit_params: str, model_path: str, context: int, devices: List[Dict[str, Any]],
              mmproj_bytes: int = 0, flags: Sequence[str] = (), timeout: float = 60) -> Dict[str, Any]:
    """llama.cpp's verdict on a downloaded model, from llama-fit-params (under a second).

    It reports per-device memory for the model, context and compute buffers,
    and how many layers would go on the GPU (-ngl, -1 = all).
    """
    base = [fit_params, "-m", model_path, "-c", str(context), "-np", "1", *fit_flags(flags)]
    try:
        memory = _run(base + ["-fitp", "on"], timeout)
        layers = _run(base, timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "llama-fit-params did not answer in time"}
    except OSError as e:
        return {"ok": False, "error": f"Cannot run llama-fit-params: {e}"}
    if memory.returncode != 0 or layers.returncode != 0:
        lines = [line for line in (memory.stderr + layers.stderr).splitlines() if line.strip()]
        return {"ok": False, "error": "llama-fit-params failed" + (f": {lines[-1].strip()}" if lines else "")}

    per_device = {}
    for line in memory.stdout.splitlines():
        match = FIT_LINE.match(line.strip())
        if match:
            per_device[match["device"]] = {k: int(match[k]) * MIB for k in ("model", "context", "compute")}
    ngl_match = NGL.search(layers.stdout)
    ngl = int(ngl_match.group(1)) if ngl_match else None

    gpus = {d["name"]: d for d in devices if d.get("gpu")}
    fits = ngl == -1 and bool(gpus)
    projector = mmproj_bytes + (PROJECTOR_COMPUTE if mmproj_bytes else 0)
    first_gpu = next((name for name in per_device if name in gpus), None)
    for name, parts in per_device.items():
        if name not in gpus:
            continue
        used = sum(parts.values()) + (projector if name == first_gpu else 0)
        parts["total_with_projector"] = used
        if used + GPU_MARGIN > gpus[name]["memory_total_bytes"]:
            fits = False
    if fits:
        result = "gpu"
    elif gpus and (ngl is None or ngl != 0):
        result = "partial"
    else:
        result = "cpu"
    return {"ok": True, "verdict": result, "gpu_layers": ngl, "devices": per_device, "projector_bytes": projector}
