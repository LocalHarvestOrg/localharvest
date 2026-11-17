# Running a model server yourself

The app installs and starts llama.cpp on its own. You only need this page for the [command line](command-line.md), or to point the app at a server you started ("Use my own model server" in Settings, addresses on this computer only).

Both servers expose the same OpenAI-compatible endpoint, by default `http://localhost:8000/v1/chat/completions`.

## llama.cpp (laptop or workstation)

Example with Qwen3.5-9B Q8, one of the app's tested models. It needs about 12 GB of memory the GPU can use: an Apple Silicon Mac with 24 GB (32 GB recommended), or an NVIDIA GPU with 16 GB of VRAM plus 16 GB of RAM. With less VRAM, llama.cpp splits the model between GPU and RAM at a large cost in speed.

**1. Install llama.cpp** (a build with Qwen3.5 vision support; tested with 0.6.0, build 11429):

```bash
brew install llama.cpp     # macOS
```

On Linux and Windows, unpack a prebuilt release from the [llama.cpp releases page](https://github.com/ggml-org/llama.cpp/releases) (CUDA builds for NVIDIA GPUs).

**2. Download the model and its vision projector** (10.5 GB; without the projector the server cannot read images):

```bash
pip install huggingface_hub
hf download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-Q8_0.gguf mmproj-BF16.gguf --local-dir ./models/qwen3.5-9b
```

`models/` is git-ignored.

**3. Start the server** from the project root and leave it running:

```bash
llama-server -m models/qwen3.5-9b/Qwen3.5-9B-Q8_0.gguf --mmproj models/qwen3.5-9b/mmproj-BF16.gguf --offline --host 127.0.0.1 --port 8000 -c 16384 -np 1 --alias qwen3.5-9b-q8 --reasoning off
```

- `--offline --host 127.0.0.1`: no network access, reachable from this computer only. The server has no API key, so never bind it to another address.
- `-c 16384`: input plus output. A 200 DPI page and the prompt take about 5,800 tokens, leaving about 10,500 for the answer, less than the third attempt's 12,288 budget; the run warns when this happens. Raise `-c` or lower the budgets to avoid it.
- `-np 1`: one request at a time. More slots divide the context, so raise `-c` in proportion.
- `--alias`: the name to put in `server.model` in `run.json`.
- `--reasoning off`: thinking mode would eat the output budget and distort the tokens-per-entity check.

The server is ready when `curl http://localhost:8000/health` returns `{"status":"ok"}`, after about 15 seconds.

## vLLM (GPU server)

> **Not finished.** The pipeline can talk to a vLLM server, but the app has no proper flow for a remote vLLM server yet.

vLLM needs an NVIDIA GPU with a recent CUDA driver, and its own virtual environment (it pulls in PyTorch, CUDA and Transformers):

```bash
python3.12 -m venv .venv_vllm && source .venv_vllm/bin/activate
pip install -r requirements_vllm.txt     # vllm 0.17.1, torch 2.10.0, transformers 4.57.6
```

Download the weights and serve one model:

```bash
hf download Qwen/Qwen3-VL-8B-Instruct --local-dir ./models/qwen3-vl-8b
export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1          # vLLM sends usage stats by default
HF_HUB_OFFLINE=1 vllm serve models/qwen3-vl-8b --port 8000 --max-model-len 16384 --enforce-eager
```

For Qianfan OCR (`baidu/Qianfan-OCR` into `./models/qianfan-ocr`), add `--trust-remote-code`. Set `server.model` to the same path you served (or the shortcut `qwen` / `qianfan`).

- `--max-model-len 16384`: must exceed the page's image tokens plus the largest output budget (12,288).
- `--enforce-eager`: less GPU memory and faster start, slightly slower runs. Drop it if you have headroom.
- `HF_HUB_OFFLINE=1`: load the local copy and make no Hub calls.

Keep the server on the same machine as the documents. A server elsewhere means every page image crosses the network, which is why the pipeline refuses non-local addresses unless `server.allow_remote` is set.
