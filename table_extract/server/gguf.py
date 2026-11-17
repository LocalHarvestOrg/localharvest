"""Reads the metadata at the start of a GGUF file, locally or over HTTP range requests.

Only the key-value header is read, not the tensors. Large arrays (the
tokenizer's vocabulary) are skipped, and reading stops once the
architecture's hyperparameters are known, so a remote header costs a few
megabytes at most rather than the whole file.

Format: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
"""

import struct
from typing import Any, Callable, Dict, Optional

MAGIC = b"GGUF"
# Arrays longer than this are not kept (vocabularies, merges); per-layer arrays are
MAX_KEPT_ARRAY = 4096
MAX_HEADER_BYTES = 64 * 1024 * 1024

_SCALARS = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
}
STRING, ARRAY = 8, 9


class GGUFError(ValueError):
    pass


class Source:
    """Sequential reads over a file or a remote resource."""

    def __init__(self, read_at: Callable[[int, int], bytes]):
        self._read_at = read_at
        self.offset = 0
        self._buffer = b""
        self._buffer_start = 0
        self._chunk = 256 * 1024

    def read(self, size: int) -> bytes:
        if self.offset + size > MAX_HEADER_BYTES:
            raise GGUFError("the GGUF header is too large")
        end = self._buffer_start + len(self._buffer)
        if not (self._buffer_start <= self.offset and self.offset + size <= end):
            length = max(size, self._chunk)
            self._buffer = self._read_at(self.offset, length)
            self._buffer_start = self.offset
            self._chunk = min(self._chunk * 2, 8 * 1024 * 1024)  # few round trips for big vocabularies
        start = self.offset - self._buffer_start
        data = self._buffer[start:start + size]
        if len(data) < size:
            raise GGUFError("the file ends inside the GGUF header")
        self.offset += size
        return data

    def skip(self, size: int) -> None:
        self.offset += size

    def unpack(self, fmt: str) -> Any:
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        length = self.unpack("<Q")
        return self.read(length).decode("utf-8", errors="replace")


def _value(src: Source, kind: int) -> Any:
    if kind in _SCALARS:
        return src.unpack(_SCALARS[kind])
    if kind == STRING:
        return src.string()
    if kind == ARRAY:
        item_kind = src.unpack("<I")
        count = src.unpack("<Q")
        if count > MAX_KEPT_ARRAY:
            if item_kind in _SCALARS:
                src.skip(count * struct.calcsize(_SCALARS[item_kind]))
            elif item_kind == STRING:
                for _ in range(count):
                    src.skip(src.unpack("<Q"))
            else:
                raise GGUFError(f"unsupported GGUF array of type {item_kind}")
            return {"array_of": item_kind, "count": count}
        return [_value(src, item_kind) for _ in range(count)]
    raise GGUFError(f"unknown GGUF value type {kind}")


VOCAB_KEY = "tokenizer.ggml.tokens"


def _has_hparams(metadata: Dict[str, Any]) -> bool:
    arch = metadata.get("general.architecture")
    return bool(arch) and f"{arch}.block_count" in metadata


def read_metadata(read_at: Callable[[int, int], bytes]) -> Dict[str, Any]:
    """The header's key-value pairs.

    llama.cpp writes general.*, then the architecture's keys, then tokenizer.*.
    Reading stops at the vocabulary once the architecture's keys are known; its
    length is kept as "tokenizer.ggml.tokens.count" (the compute buffer depends on it).
    """
    src = Source(read_at)
    if src.read(4) != MAGIC:
        raise GGUFError("not a GGUF file")
    version = src.unpack("<I")
    if version == 1:
        raise GGUFError("GGUF version 1 is not supported")
    tensor_count = src.unpack("<Q")
    kv_count = src.unpack("<Q")
    metadata: Dict[str, Any] = {"gguf.version": version, "gguf.tensor_count": tensor_count}
    for _ in range(kv_count):
        key = src.string()
        kind = src.unpack("<I")
        if key == VOCAB_KEY and kind == ARRAY:
            item_kind = src.unpack("<I")
            count = src.unpack("<Q")
            metadata[VOCAB_KEY + ".count"] = count
            if _has_hparams(metadata):
                break
            if item_kind != STRING:
                raise GGUFError("unexpected vocabulary type")
            for _ in range(count):  # unusual order: skip it and read on
                src.skip(src.unpack("<Q"))
            continue
        metadata[key] = _value(src, kind)
    return metadata


def read_file(path: str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        def read_at(offset: int, size: int) -> bytes:
            f.seek(offset)
            return f.read(size)
        return read_metadata(read_at)


def read_url(session: Any, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30) -> Dict[str, Any]:
    """The header of a remote GGUF file, with HTTP range requests (session: a requests.Session)."""
    def read_at(offset: int, size: int) -> bytes:
        response = session.get(url, headers={**(headers or {}), "Range": f"bytes={offset}-{offset + size - 1}"},
                               timeout=timeout, stream=True)
        try:
            response.raise_for_status()
            if response.status_code != 206:
                # No range support: read only what we asked for from the start
                if offset:
                    raise GGUFError("the server does not support range requests")
                return response.raw.read(size, decode_content=True)
            return response.content
        finally:
            response.close()
    return read_metadata(read_at)
