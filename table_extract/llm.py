"""Client for an OpenAI-compatible server (llama-server or vLLM) running on this machine."""

import base64
import io
import ipaddress
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from PIL import Image

CONNECT_TIMEOUT = 10  # seconds; generation itself may legitimately take minutes


def is_loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def image_to_base64(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@dataclass
class Completion:
    content: str
    prompt_tokens: int
    completion_tokens: int
    # "length" means the model hit max_tokens and the output is almost certainly
    # truncated (incomplete JSON)
    finish_reason: Optional[str]


class ChatClient:
    def __init__(self, url: str, model: str, api_key: Optional[str] = None,
                 sampling: Optional[Dict[str, Any]] = None):
        self.url = url
        self.model = model
        self.sampling = sampling or {}
        self.base_url = url.rsplit("/v1/", 1)[0]
        self.session = requests.Session()
        if is_loopback(url):
            self.session.trust_env = False  # a system proxy must not get the requests for this machine
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def check(self, timeout: float = 3.0) -> None:
        """Fail fast if the endpoint is unavailable."""
        models_url = f"{self.base_url}/v1/models"
        try:
            response = self.session.get(models_url, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(
                f"The model server is not reachable at {models_url}. Start it first "
                "(see the README: 'The vLLM server' or 'Running locally with llama.cpp')."
            ) from e

    def server_info(self) -> Dict[str, Any]:
        """What the server says about itself: context per slot, slots, model, build.

        llama-server answers /props; vLLM reports max_model_len in /v1/models.
        Missing values are None.
        """
        info: Dict[str, Any] = {"n_ctx": None, "slots": None, "model_path": None, "build": None}
        try:
            response = self.session.get(f"{self.base_url}/props", timeout=3)
            if response.ok:
                props = response.json()
                info["n_ctx"] = (props.get("default_generation_settings") or {}).get("n_ctx")
                info["slots"] = props.get("total_slots")
                info["model_path"] = props.get("model_path")
                info["build"] = props.get("build_info")
        except (requests.RequestException, ValueError):
            pass
        if info["n_ctx"] is None:
            try:
                models = self.session.get(f"{self.base_url}/v1/models", timeout=3).json().get("data") or []
                info["n_ctx"] = next((m.get("max_model_len") for m in models if m.get("max_model_len")), None)
            except (requests.RequestException, ValueError):
                pass
        return info

    def count_tokens(self, text: str) -> Optional[int]:
        """Token count of a text, or None if the server has no /tokenize."""
        try:
            response = self.session.post(f"{self.base_url}/tokenize", json={"content": text}, timeout=10)
            if response.ok:
                return len(response.json().get("tokens") or [])
        except (requests.RequestException, ValueError):
            pass
        return None

    def complete(self, content: List[Dict[str, Any]], max_tokens: int) -> Completion:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            **self.sampling,
        }
        response = self.session.post(self.url, json=payload, timeout=(CONNECT_TIMEOUT, None))
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return Completion(
            content=(choice["message"].get("content") or "").strip(),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason"),
        )


def page_content(prompt: str, b64_image: str, reminder: str = "") -> List[Dict[str, Any]]:
    """Fixed instructions first, so the server reuses them from its prompt cache;
    only the image and any retry reminder are new for each request."""
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}},
    ]
    if reminder:
        content.append({"type": "text", "text": reminder})
    return content
