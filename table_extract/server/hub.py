"""Hugging Face Hub client: model search, file listings, GGUF headers and download URLs.

Plain HTTP with requests rather than huggingface_hub, which keeps the frozen
app small and leaves nothing that could send telemetry. Every request goes to
huggingface.co; downloads are redirected to its storage hosts, and requests
drops the Authorization header on a redirect to another host.

The token for gated models (Gemma) is read from HF_TOKEN or from the file
`hf auth login` writes; the app never stores it.
"""

import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from . import gguf
from .storage import read_json, write_json

HUB = "https://huggingface.co"
TIMEOUT = (10, 30)
# Publishers listed by default: GGUF specialists and the model makers' own organisations
TRUSTED_PUBLISHERS = {
    "ggml-org", "unsloth", "bartowski", "lmstudio-community",
    "google", "Qwen", "mistralai", "microsoft", "meta-llama", "ibm-granite", "HuggingFaceTB", "openbmb",
    "allenai", "nvidia", "deepseek-ai", "moonshotai", "zai-org", "LiquidAI", "OpenGVLab", "baidu",
}
SORTS = {"trending": "trendingScore", "downloads": "downloads", "likes": "likes", "updated": "lastModified"}
SPLIT_PART = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
# The quantization in a GGUF file name: Q4_K_M, IQ4_XS, UD-Q4_K_XL, Q8_0, BF16, MXFP4 ...
QUANT = re.compile(
    r"(?:^|[-_.])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4(?:_MOE)?|TQ\d_\d))(?=[-_.]|$)",
    re.IGNORECASE,
)


class HubError(Exception):
    pass


def hf_token() -> Optional[str]:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        return token.strip()
    home = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    try:
        return (home / "token").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def is_projector(path: str) -> bool:
    return Path(path).name.lower().startswith("mmproj")


def quant_of(path: str) -> str:
    name = SPLIT_PART.sub(".gguf", Path(path).name)
    matches = QUANT.findall(name[:-5] if name.lower().endswith(".gguf") else name)
    return matches[-1].upper() if matches else Path(name).stem


def group_files(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Model files grouped by quantization (split files together), and the vision projectors."""
    quants: Dict[str, Dict[str, Any]] = {}
    projectors = []
    for f in files:
        path = f["path"]
        if not path.lower().endswith(".gguf"):
            continue
        if is_projector(path):
            projectors.append(f)
            continue
        quant = quant_of(path)
        # The same quantization in two folders (rare) stays apart
        key = quant if quant not in quants or Path(quants[quant]["files"][0]["path"]).parent == Path(path).parent \
            else f"{quant} ({Path(path).parent})"
        entry = quants.setdefault(key, {"quant": key, "files": [], "size": 0})
        entry["files"].append(f)
        entry["size"] += f.get("size") or 0
    for entry in quants.values():
        entry["files"].sort(key=lambda f: f["path"])  # part 00001 first: llama-server is given that one
    return {"quants": sorted(quants.values(), key=lambda q: q["size"]), "projectors": projectors}


def preferred_projector(projectors: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """F16 runs on every backend; BF16 is slow on some, F32 is twice the size."""
    order = ["F16", "BF16", "Q8_0", "F32"]
    def rank(f):
        q = quant_of(f["path"])
        return (order.index(q) if q in order else len(order), f.get("size") or 0)
    return min(projectors, key=rank) if projectors else None


class HubClient:
    def __init__(self, cache_dir: Path, session: Optional[requests.Session] = None, base_url: str = HUB):
        self.cache_dir = cache_dir
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "LocalHarvest"
        self.base_url = base_url
        self._lock = threading.Lock()

    def _auth(self) -> Dict[str, str]:
        token = hf_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _get(self, path: str, params: Any = None) -> Any:
        return self._request(f"{self.base_url}{path}", params).json()

    def _request(self, url: str, params: Any = None) -> requests.Response:
        try:
            response = self.session.get(url, params=params, headers=self._auth(), timeout=TIMEOUT)
        except requests.RequestException as e:
            raise HubError(f"Cannot reach huggingface.co: {e}") from e
        if response.status_code == 404:
            raise HubError("Not found on huggingface.co.")
        if response.status_code in (401, 403):
            raise HubError("huggingface.co refused access. The model may require logging in with `hf auth login`.")
        if not response.ok:
            raise HubError(f"huggingface.co answered HTTP {response.status_code}.")
        return response

    def search(self, query: str = "", sort: str = "trending", trusted_only: bool = True,
               limit: int = 30) -> List[Dict[str, Any]]:
        """Vision models with GGUF files and a projector, most popular first."""
        params: List[Any] = [
            ("pipeline_tag", "image-text-to-text"), ("filter", "gguf"),
            ("sort", SORTS.get(sort, "trendingScore")), ("direction", "-1"), ("limit", "100"),
        ] + [("expand[]", e) for e in ("siblings", "downloads", "likes", "trendingScore", "gated", "sha",
                                        "lastModified", "author")]
        if query:
            params.append(("search", query))
        results: List[Dict[str, Any]] = []
        url, page_params = f"{self.base_url}/api/models", params
        for _ in range(3):  # trusted publishers are a minority of the trending list
            response = self._request(url, page_params)
            for model in response.json():
                author = model.get("author") or model["id"].split("/")[0]
                files = [s["rfilename"] for s in model.get("siblings") or []]
                if not any(is_projector(f) and f.lower().endswith(".gguf") for f in files):
                    continue
                if trusted_only and author not in TRUSTED_PUBLISHERS:
                    continue
                quants = sorted({quant_of(f) for f in files if f.lower().endswith(".gguf") and not is_projector(f)})
                results.append({
                    "repo": model["id"], "author": author, "trusted": author in TRUSTED_PUBLISHERS,
                    "revision": model.get("sha"), "gated": bool(model.get("gated")),
                    "downloads": model.get("downloads"), "likes": model.get("likes"),
                    "trending": model.get("trendingScore"), "updated": model.get("lastModified"),
                    "quants": quants,
                })
            following = response.links.get("next", {}).get("url")
            if len(results) >= limit or not following:
                break
            url, page_params = following, None  # the next page's URL carries every parameter
        return results[:limit]

    def model_info(self, repo: str) -> Dict[str, Any]:
        info = self._get(f"/api/models/{repo}", [("expand[]", "sha"), ("expand[]", "gated")])
        return {"repo": info.get("id", repo), "revision": info.get("sha"), "gated": bool(info.get("gated"))}

    def files(self, repo: str, revision: str) -> List[Dict[str, Any]]:
        """Every file of a repo at a commit, with its size and SHA-256 (for LFS files)."""
        cache = self.cache_dir / "hub" / repo / f"{revision}.json"
        cached = read_json(cache)
        if isinstance(cached, list):
            return cached
        tree = self._get(f"/api/models/{repo}/tree/{revision}", {"recursive": "true"})
        files = [{"path": f["path"], "size": (f.get("lfs") or {}).get("size", f.get("size")),
                  "sha256": (f.get("lfs") or {}).get("oid")}
                 for f in tree if f.get("type") == "file"]
        write_json(cache, files)  # a commit never changes
        return files

    def header(self, repo: str, revision: str, path: str) -> Dict[str, Any]:
        """A remote GGUF file's metadata, read with range requests (a few MB at most)."""
        cache = self.cache_dir / "gguf" / repo / revision / (path.replace("/", "__") + ".json")
        cached = read_json(cache)
        if isinstance(cached, dict):
            return cached
        try:
            metadata = gguf.read_url(self.session, self.download_url(repo, revision, path), headers=self._auth())
        except requests.RequestException as e:
            raise HubError(f"Cannot read {path} from huggingface.co: {e}") from e
        except gguf.GGUFError as e:
            raise HubError(f"{path} is not a usable GGUF file: {e}") from e
        write_json(cache, metadata)
        return metadata

    def download_url(self, repo: str, revision: str, path: str) -> str:
        return f"{self.base_url}/{repo}/resolve/{revision}/{quote(path)}"

    def auth_headers(self) -> Dict[str, str]:
        return self._auth()
