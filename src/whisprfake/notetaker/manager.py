"""Notetaker: records a meeting (your mic + the system audio), transcribes it with the local ASR engine,
separates speakers on the system channel, summarizes with the local LLM and exports Markdown."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import signal
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from rapidfuzz import fuzz

from .. import config as C
from ..audio.vad import SileroVAD
from ..llm import prompts

log = logging.getLogger(__name__)
MEETINGS_AUDIO = C.DATA_DIR / "meetings"
EXPORT_DIR = Path.home() / "Documents" / "Meetings"


@dataclass
class Seg:
    start: float
    end: float
    speaker: str
    text: str = ""


def vad_segments(audio: np.ndarray, vad: SileroVAD, min_speech=0.4, min_silence=0.6, max_len=25.0) -> list[tuple[float, float]]:
    """Speech regions (seconds) using Silero VAD; long regions are split."""
    frame, sr = 512, 16000
    vad.reset()
    probs = [vad.prob(audio[i:i + frame]) for i in range(0, len(audio) - frame, frame)]
    segs, start, silence = [], None, 0
    for i, p in enumerate(probs):
        t = i * frame / sr
        if p >= 0.5:
            if start is None:
                start = t
            silence = 0
        elif start is not None:
            silence += frame / sr
            if silence >= min_silence:
                end = t - silence + frame / sr
                if end - start >= min_speech:
                    segs.append((start, end))
                start, silence = None, 0
    if start is not None:
        segs.append((start, len(audio) / sr))
    out = []
    for s, e in segs:
        while e - s > max_len:
            out.append((s, s + max_len))
            s += max_len
        out.append((s, e))
    return out


async def diarize(wav_path: Path) -> list[tuple[float, float, int]]:
    import sys

    p = await asyncio.create_subprocess_exec(sys.executable, "-m", "whisprfake.notetaker.diarize_proc", str(wav_path),
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    if p.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip().splitlines()[-1] if err else f"exit {p.returncode}")
    return [(float(a), float(b), int(c)) for a, b, c in json.loads(out)]


class Notetaker:
    def __init__(self, daemon):
        self.d = daemon
        self.procs: list[asyncio.subprocess.Process] = []
        self.meeting_id: int | None = None
        self.paths: tuple[Path, Path] | None = None
        self.suggested: set[str] = set()

    @property
    def recording(self) -> bool:
        return self.meeting_id is not None

    # ------------------------------------------------------------------ recording
    async def start(self, title: str = "", app: str = "") -> int:
        if self.recording:
            return self.meeting_id
        MEETINGS_AUDIO.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        mic, sysa = MEETINGS_AUDIO / f"{stamp}-mic.wav", MEETINGS_AUDIO / f"{stamp}-system.wav"
        args = ["--rate=16000", "--channels=1", "--format=s16le", "--file-format=wav"]
        self.procs = [
            await asyncio.create_subprocess_exec("parecord", "--device=@DEFAULT_SOURCE@", *args, str(mic),
                                                 stderr=asyncio.subprocess.DEVNULL),
            await asyncio.create_subprocess_exec("parecord", "--device=@DEFAULT_MONITOR@", *args, str(sysa),
                                                 stderr=asyncio.subprocess.DEVNULL),
        ]
        self.paths = (mic, sysa)
        self.meeting_id = self.d.store.x(
            "INSERT INTO meetings(title, started, app, audio_path, status) VALUES (?,?,?,?, 'recording')",
            (title or f"Meeting {dt.datetime.now():%d.%m. %H:%M}", time.time(), app, json.dumps([str(mic), str(sysa)])))
        self.d.ipc.emit("meeting_changed", recording=True, id=self.meeting_id)
        log.info("meeting %d recording", self.meeting_id)
        return self.meeting_id

    async def stop(self) -> int | None:
        if not self.recording:
            return None
        mid, self.meeting_id = self.meeting_id, None
        for p in self.procs:
            if p.returncode is None:
                p.send_signal(signal.SIGINT)
        for p in self.procs:
            try:
                await asyncio.wait_for(p.wait(), 5)
            except TimeoutError:
                p.kill()
        self.procs = []
        self.d.store.x("UPDATE meetings SET ended=?, status='processing' WHERE id=?", (time.time(), mid))
        self.d.ipc.emit("meeting_changed", recording=False, id=mid)
        asyncio.create_task(self._process(mid, *self.paths))
        return mid

    # ------------------------------------------------------------------ processing
    async def _process(self, mid: int, mic_path: Path, sys_path: Path) -> None:
        try:
            await self._process_inner(mid, mic_path, sys_path)
        except Exception as e:
            log.exception("meeting processing failed")
            self.d.store.x("UPDATE meetings SET status='failed', summary=? WHERE id=?", (f"Fehler: {e}", mid))
        self.d.ipc.emit("meeting_changed", recording=self.recording, id=mid)

    async def _load(self, p: Path) -> np.ndarray:
        if not p.exists() or p.stat().st_size < 1000:
            return np.zeros(0, np.float32)
        a, _ = await asyncio.to_thread(sf.read, p, dtype="float32")
        return a if a.ndim == 1 else a.mean(axis=1)

    async def _process_inner(self, mid: int, mic_path: Path, sys_path: Path) -> None:
        mic, sysa = await self._load(mic_path), await self._load(sys_path)
        vad = SileroVAD(str(C.MODELS_DIR / "silero_vad.onnx"))
        segs: list[Seg] = []
        # your own voice
        for s, e in await asyncio.to_thread(vad_segments, mic, vad):
            segs.append(Seg(s, e, "Ich"))
        # everyone else, separated by voice
        if len(sysa) > 16000 and float(np.abs(sysa).max()) > 0.01:
            try:
                turns = await diarize(sys_path)
            except Exception as e:
                log.warning("diarization failed (%s); using VAD only", e)
                turns = [(s, e, 0) for s, e in await asyncio.to_thread(vad_segments, sysa, vad)]
            for s, e, spk in turns:
                while e - s > 25:
                    segs.append(Seg(s, s + 25, f"Sprecher {spk + 1}", ""))
                    s += 25
                if e - s >= 0.4:
                    segs.append(Seg(s, e, f"Sprecher {spk + 1}"))
        segs.sort(key=lambda x: x.start)
        await self.d.asr_ready.wait()
        for sg in segs:
            src = mic if sg.speaker == "Ich" else sysa
            chunk = src[int(sg.start * 16000):int(sg.end * 16000)]
            if len(chunk) < 4000:
                continue
            r = await self.d.asr.transcribe(chunk, languages=self.d.cfg.asr.languages)
            sg.text = r.text.strip()
        segs = [s for s in segs if s.text]
        segs = self._drop_echo(segs)
        segs = self._merge(segs)
        transcript = "\n".join(f"[{_ts(s.start)}] {s.speaker}: {s.text}" for s in segs)
        summary = await self._summarize(transcript) if transcript else "_Keine Sprache erkannt._"
        row = self.d.store.q("SELECT * FROM meetings WHERE id=?", (mid,))[0]
        md = self._export(row, summary, transcript)
        self.d.store.x("UPDATE meetings SET segments=?, summary=?, markdown_path=?, status='done' WHERE id=?",
                       (json.dumps([s.__dict__ for s in segs], ensure_ascii=False), summary, str(md), mid))
        from ..context import hypr
        await hypr.notify(f"Meeting-Notizen fertig: {md.name}", 6000)

    @staticmethod
    def _drop_echo(segs: list[Seg]) -> list[Seg]:
        """Without headphones the mic hears the other side too: drop 'Ich' segments that repeat a
        simultaneous system-audio segment."""
        out = []
        for s in segs:
            if s.speaker == "Ich":
                dup = any(o.speaker != "Ich" and o.start < s.end and s.start < o.end
                          and fuzz.partial_ratio(s.text.lower(), o.text.lower()) > 80 for o in segs)
                if dup:
                    continue
            out.append(s)
        return out

    @staticmethod
    def _merge(segs: list[Seg]) -> list[Seg]:
        out: list[Seg] = []
        for s in segs:
            if out and out[-1].speaker == s.speaker and s.start - out[-1].end < 2.0:
                out[-1].end, out[-1].text = s.end, out[-1].text + " " + s.text
            else:
                out.append(Seg(s.start, s.end, s.speaker, s.text))
        return out

    async def _summarize(self, transcript: str) -> str:
        model = self.d.cfg.llm.summary_model
        chunks = _split(transcript, 9000)
        if len(chunks) > 1:
            notes = []
            for c in chunks:
                r = await self.d.llm.chat(model, [{"role": "system", "content": prompts.SUMMARY_SYSTEM},
                                                  {"role": "user", "content": "Teil eines Meeting-Transkripts:\n\n" + c}],
                                          temperature=0.2, timeout=300)
                notes.append(r.text)
            transcript = "Zusammenfassungen der Teile:\n\n" + "\n\n---\n\n".join(notes)
        r = await self.d.llm.chat(model, [{"role": "system", "content": prompts.SUMMARY_SYSTEM},
                                          {"role": "user", "content": transcript}], temperature=0.2, timeout=300)
        return re.sub(r"^<think>.*?</think>\s*", "", r.text, flags=re.S).strip()

    def _export(self, row: dict, summary: str, transcript: str) -> Path:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        start = dt.datetime.fromtimestamp(row["started"])
        dur = int(((row["ended"] or time.time()) - row["started"]) / 60)
        safe = re.sub(r"[^\w äöüÄÖÜß.-]+", "", row["title"] or "Meeting").strip()[:60]
        p = EXPORT_DIR / f"{start:%Y-%m-%d %H%M} {safe}.md"
        p.write_text(f"# {row['title']}\n\n*{start:%d.%m.%Y %H:%M} · {dur} min"
                     f"{' · ' + row['app'] if row.get('app') else ''}*\n\n{summary}\n\n## Transkript\n\n{transcript}\n")
        return p

    # ------------------------------------------------------------------ meeting detection
    MEETING_PATTERNS = [
        (re.compile(r"^zoom", re.I), None, "Zoom"),
        (re.compile(r"teams", re.I), None, "Teams"),
        (None, re.compile(r"^Meet [–-] |Google Meet", re.I), "Google Meet"),
        (None, re.compile(r"\| Microsoft Teams", re.I), "Teams"),
        (re.compile(r"discord", re.I), re.compile(r"voice|stage|call|anruf", re.I), "Discord"),
        (None, re.compile(r"Jitsi Meet|whereby|webex", re.I), "Videocall"),
    ]

    async def watch(self) -> None:
        """Suggest recording when a meeting app appears and something is using the microphone."""
        import os

        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not sig:
            return
        path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "hypr" / sig / ".socket2.sock"
        while True:
            try:
                reader, _ = await asyncio.open_unix_connection(str(path))
                while line := await reader.readline():
                    ev = line.decode(errors="replace").strip()
                    if ev.startswith(("activewindow>>", "windowtitlev2>>", "openwindow>>")):
                        await self._maybe_suggest()
            except Exception as e:
                log.debug("hypr socket2: %s", e)
            await asyncio.sleep(5)

    async def _maybe_suggest(self) -> None:
        if self.recording:
            return
        from ..context import hypr

        app, _ = await hypr.active_window()
        name = None
        for cls_rx, title_rx, label in self.MEETING_PATTERNS:
            if (cls_rx is None or cls_rx.search(app.wm_class)) and (title_rx is None or title_rx.search(app.title)):
                name = label
                break
        key = f"{name}:{app.title[:40]}"
        if not name or key in self.suggested or not await _mic_in_use():
            return
        self.suggested.add(key)
        asyncio.create_task(self._notify(name, app.title))

    async def _notify(self, name: str, title: str) -> None:
        p = await asyncio.create_subprocess_exec(
            "notify-send", "-a", "whisprfake", "-t", "20000", "-A", "record=Aufnehmen", "-A", "no=Nein",
            f"{name}-Meeting erkannt", "Meeting-Notizen lokal aufnehmen?", stdout=asyncio.subprocess.PIPE)
        out, _ = await p.communicate()
        if out.decode().strip() == "record":
            await self.start(title=f"{name}: {title}"[:80], app=name)


async def _mic_in_use() -> bool:
    """True when some other application is capturing audio (PipeWire input stream)."""
    p = await asyncio.create_subprocess_exec("pw-dump", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await p.communicate()
    try:
        nodes = json.loads(out)
    except json.JSONDecodeError:
        return False
    for n in nodes:
        props = (n.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Stream/Input/Audio" and "whisprfake" not in str(props.get("application.name", "")).lower() \
                and props.get("application.process.binary") not in ("python3", "python", "parecord"):
            return True
    return False


def _ts(sec: float) -> str:
    return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{int(sec % 60):02d}" if sec >= 3600 \
        else f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def _split(text: str, n: int) -> list[str]:
    lines, chunks, cur = text.splitlines(), [], ""
    for line in lines:
        if len(cur) + len(line) > n and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    return chunks
