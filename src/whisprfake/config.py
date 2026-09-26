"""User configuration (~/.config/whisprfake/config.toml)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

APP = "whisprfake"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
MODELS_DIR = DATA_DIR / "models"
AUDIO_DIR = DATA_DIR / "audio"
DB_PATH = DATA_DIR / "whisprfake.db"
SOCKET_PATH = RUNTIME_DIR / "whisprfake.sock"
CONFIG_PATH = CONFIG_DIR / "config.toml"


class Shortcuts(BaseModel):
    # Each action accepts up to four chords, like Wispr. A chord is "+"-joined key names.
    push_to_talk: list[str] = ["CTRL+SUPER"]
    # Omarchy's background switcher is moved to Ctrl+Super+Shift+B by packaging/hypr/whisprfake.lua.
    hands_free: list[str] = ["CTRL+SUPER+SPACE"]
    command: list[str] = ["CTRL+SUPER+ALT"]
    paste_last: list[str] = ["CTRL+SUPER+ALT+V"]
    cancel: str = "ESC"
    double_tap_ms: int = 500
    tap_max_ms: int = 250
    # Feedback (sound/Flow Bar) is delayed so Ctrl+Super+<key> compositor shortcuts stay silent.
    grace_ms: int = 220


class Audio(BaseModel):
    # Ordered preference list of PipeWire/PortAudio device-name substrings; first match wins.
    mic_priority: list[str] = []
    sample_rate: int = 16000
    prewarm: bool = False  # mic opens in ~45 ms anyway; pre-warm only helps on slow devices
    pause_media: bool = True
    sounds: bool = True
    sound_volume: float = 0.5
    max_minutes: int = 20
    warn_minutes: int = 19
    silence_autostop_s: int = 90


def _find_bin(name: str) -> str:
    """Prefer a user build (packaging/install.sh), fall back to the Arch package location."""
    for base in (Path.home() / ".local/opt/src", Path("/opt/whisprfake")):
        sub = "whisper.cpp" if name == "whisper-server" else "llama.cpp"
        p = base / sub / "build/bin" / name
        if p.exists():
            return str(p)
    return str(Path.home() / ".local/opt/src" / ("whisper.cpp" if name == "whisper-server" else "llama.cpp")
               / "build/bin" / name)


class ASR(BaseModel):
    engine: Literal["whisper", "parakeet", "qwen3asr", "parakeet_onnx"] = "qwen3asr"  # best accuracy, ~0.15-0.3 s
    languages: list[str] = ["de", "en"]
    whisper_model: str = "ggml-large-v3-turbo.bin"
    parakeet_model: str = "ggml-parakeet-tdt-0.6b-v3-f16.bin"
    qwen3asr_model: str = "Qwen3-ASR-1.7B-Q8_0.gguf"
    qwen3asr_mmproj: str = "mmproj-Qwen3-ASR-1.7B-Q8_0.gguf"
    whisper_server_bin: str = Field(default_factory=lambda: _find_bin("whisper-server"))
    llama_server_bin: str = Field(default_factory=lambda: _find_bin("llama-server"))
    port: int = 8931


class LLM(BaseModel):
    base_url: str = "http://127.0.0.1:11434"
    cleanup_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    command_model: str = "qwen3:8b"
    summary_model: str = "qwen3:8b"
    keep_alive: str = "-1"
    timeout_s: float = 8.0


class Cleanup(BaseModel):
    level: Literal["none", "light", "medium", "high"] = "medium"
    smart_formatting: bool = True   # lists → bullet points, paragraphs, e-mail layout
    bullet: Literal["auto", "-", "•"] = "auto"  # auto: • in chats/e-mail, - (Markdown) elsewhere
    backtrack: bool = True


class Styles(BaseModel):
    personal: Literal["formal", "casual", "very_casual"] = "casual"
    work: Literal["formal", "casual", "excited"] = "formal"
    email: Literal["formal", "casual", "excited"] = "formal"
    other: Literal["formal", "casual", "excited"] = "formal"
    # window-class / title / url substring -> category; merged over built-in defaults
    app_overrides: dict[str, Literal["personal", "work", "email", "other"]] = {}


class Privacy(BaseModel):
    context_awareness: bool = True
    audio_retention_days: int = 14
    autolearn_suggestions: bool = True


class Config(BaseModel):
    shortcuts: Shortcuts = Field(default_factory=Shortcuts)
    audio: Audio = Field(default_factory=Audio)
    asr: ASR = Field(default_factory=ASR)
    llm: LLM = Field(default_factory=LLM)
    cleanup: Cleanup = Field(default_factory=Cleanup)
    styles: Styles = Field(default_factory=Styles)
    privacy: Privacy = Field(default_factory=Privacy)

    def model_path(self, name: str) -> Path:
        p = Path(name).expanduser()
        return p if p.is_absolute() else MODELS_DIR / p


def load(path: Path = CONFIG_PATH) -> Config:
    if path.exists():
        return Config.model_validate(tomllib.loads(path.read_text()))
    return Config()


def save(cfg: Config, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_to_toml(cfg.model_dump()))


def _to_toml(d: dict, prefix: str = "") -> str:
    scalars, tables = [], []
    for k, v in d.items():
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            scalars.append(f"{k} = {_toml_value(v)}")
    out = "\n".join(scalars)
    for k, v in tables:
        name = f"{prefix}{k}"
        body = _to_toml(v, prefix=f"{name}.")
        out += f"\n\n[{name}]\n{body}" if body.strip() else f"\n\n[{name}]"
    return out.strip() + "\n"


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(type(v))
