"""whisprfake daemon: hotkeys → recording → streaming ASR → cleanup/command → insert, plus IPC for the
omarchy-shell plugin (Flow Bar, bar widget, answer popup) and the Hub."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .. import config as C
from ..asr import make_engine
from ..audio.capture import Recorder
from ..audio.feedback import Sounds
from ..audio.media import MediaPauser
from ..audio.vad import Chunker, SileroVAD
from ..command import router
from ..context import hypr
from ..context.atspi import FocusTracker, TextContext, set_a11y_enabled
from ..context.categories import AppInfo, categorize
from ..input.fsm import Action, HotkeyFSM
from ..input.hotkeys import KeyboardListener
from ..llm import prompts
from ..llm.ollama import Ollama
from ..output import inject
from ..output.primary_watch import PrimaryWatcher
from ..pipeline import cleanup, dictionary, guard
from ..store.db import Store
from .ipc import IPCServer

log = logging.getLogger("whisprfake")


@dataclass
class Snapshot:
    app: AppInfo = field(default_factory=AppInfo)
    is_terminal: bool = False
    category: str = "other"
    text: TextContext = field(default_factory=TextContext)


@dataclass
class Session:
    id: int
    mode: str
    t_start: float
    chunker: Chunker
    confirmed: bool = False
    locked: bool = False
    warned: bool = False
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    texts: list[str] = field(default_factory=list)
    langs: list[str] = field(default_factory=list)
    asr_s: float = 0.0
    worker: asyncio.Task | None = None
    snap: asyncio.Task | None = None


class Daemon:
    def __init__(self, cfg: C.Config):
        self.cfg = cfg
        C.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        self.store = Store()
        from ..store.seed import seed
        seed(self.store)
        self.ipc = IPCServer(C.SOCKET_PATH)
        self.fsm = HotkeyFSM.from_config(cfg.shortcuts)
        self.sounds = Sounds(cfg.audio.sounds, cfg.audio.sound_volume)
        self.media = MediaPauser()
        self.recorder = Recorder(cfg.audio.mic_priority, on_level=self._level_from_thread)
        vad_path = C.MODELS_DIR / "silero_vad.onnx"
        self.vad_path = str(vad_path) if vad_path.exists() else None
        self.asr = make_engine(cfg)
        self.llm = Ollama(cfg.llm.base_url, cfg.llm.keep_alive, cfg.llm.timeout_s)
        self.focus = FocusTracker()
        self.primary = PrimaryWatcher()
        self.session: Session | None = None
        self.state = "idle"
        self.processing = 0
        self.seq = 0
        self.last_answer: dict | None = None
        self.asr_ready = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._register_ipc()
        self._register_ipc_extra()

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        await self.ipc.start()
        self.ipc.snapshot = lambda: [self._state_event()]
        if self.cfg.privacy.context_awareness:
            await asyncio.to_thread(set_a11y_enabled, True)
            self.focus.start()
        tasks = [
            asyncio.create_task(KeyboardListener(self._on_key).run()),
            asyncio.create_task(self._ticker()),
            asyncio.create_task(self.primary.run()),
            asyncio.create_task(self._retention()),
            asyncio.create_task(self._warmup()),
        ]
        log.info("whisprfake ready (socket %s)", C.SOCKET_PATH)
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.asr.stop()

    async def _warmup(self) -> None:
        try:
            await self.asr.start()
            # a tiny silent clip loads kernels/shaders so the first real dictation is fast
            await self.asr.transcribe(np.zeros(16000, np.float32))
            self.asr_ready.set()
            log.info("ASR engine %s ready", self.asr.name)
        except Exception:
            log.exception("ASR engine failed to start")
            self._emit_error("Spracherkennung konnte nicht starten")
        try:
            await self.llm.warm(self.cfg.llm.cleanup_model)
            # same system prompt + dictionary as real dictations, so Ollama caches the prefix now
            await self._clean("ähm das ist ein test", Snapshot())
            log.info("LLM %s ready", self.cfg.llm.cleanup_model)
        except Exception as e:
            log.warning("LLM warmup failed (%s) – cleanup falls back to rules", e)
        await self._recover()

    # ------------------------------------------------------------------ events out
    def _state_event(self) -> dict:
        s = self.session
        return {"event": "state", "state": self.state,
                "mode": s.mode if s else None, "locked": bool(s and s.locked),
                "elapsed": round(time.monotonic() - s.t_start, 1) if s else 0,
                "max_s": self.cfg.audio.max_minutes * 60, "processing": self.processing}

    def _set_state(self, state: str) -> None:
        self.state = state
        ev = self._state_event()
        self.ipc.emit(**ev)

    def _emit_error(self, msg: str) -> None:
        self.ipc.emit("error", message=msg)
        asyncio.create_task(hypr.notify(msg))

    def _level_from_thread(self, v: float) -> None:
        if self.loop and self.session and self.session.confirmed:
            self.loop.call_soon_threadsafe(lambda: self.ipc.emit("level", v=round(v, 3)))

    # ------------------------------------------------------------------ hotkeys
    def _on_key(self, name: str, down: bool, now: float) -> None:
        for e in self.fsm.key(name, down, now):
            self._act(e.action, e.mode)

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.025)
            for e in self.fsm.tick(time.monotonic()):
                self._act(e.action, e.mode)
            s = self.session
            if s and s.confirmed:
                el = time.monotonic() - s.t_start
                if s.locked and not s.warned and el > self.cfg.audio.warn_minutes * 60:
                    s.warned = True
                    self.sounds.play("warn")
                    self.ipc.emit("warning", message="Noch 1 Minute")
                silence = s.chunker.silence_run / 16000
                if el > self.cfg.audio.max_minutes * 60 or (s.locked and silence > self.cfg.audio.silence_autostop_s):
                    self.fsm.external_stop()
                    self._act(Action.STOP)
            else:
                self.recorder.close_if_idle(3.0)

    def _act(self, a: Action, mode: str | None = None) -> None:
        s = self.session
        if a is Action.PREWARM:
            if self.cfg.audio.prewarm:
                self.loop.run_in_executor(None, self.recorder.open)
        elif a is Action.START:
            self._start(mode or "dictate")
        elif a is Action.PASTE_LAST:
            asyncio.create_task(self._paste_last())
        elif s is None:
            return
        elif a is Action.CONFIRM:
            s.confirmed = True
            self.sounds.play("command" if s.mode == "command" else "start")
            self._set_state("recording")
            asyncio.create_task(hypr.grab_escape(True))
            if self.cfg.audio.pause_media:
                asyncio.create_task(self.media.pause_all())
        elif a is Action.UPGRADE_COMMAND:
            s.mode = "command"
            if s.confirmed:
                self.sounds.play("command")
                self._set_state("recording")
        elif a is Action.LOCK:
            s.locked = True
            self.sounds.play("lock")
            self._set_state("recording")
        elif a in (Action.DISCARD, Action.CANCEL):
            self._end(s)
            s.queue.put_nowait(None)  # let the ASR worker exit
            if a is Action.CANCEL:
                self.sounds.play("cancel")
            self._set_state("processing" if self.processing else "idle")
        elif a is Action.STOP:
            audio = self._end(s)
            if s.confirmed:
                self.sounds.play("stop")
            asyncio.create_task(self._finish(s, audio))

    def _start(self, mode: str) -> None:
        if self.session:
            self._end(self.session)
        self.seq += 1
        vad = SileroVAD(self.vad_path) if self.vad_path else None
        s = Session(self.seq, mode, time.monotonic(), Chunker(vad))
        self.session = s
        loop = self.loop

        def feed(block: np.ndarray) -> None:
            loop.call_soon_threadsafe(self._feed, s, block)

        self.recorder.listeners = [feed]
        try:
            self.recorder.start()
        except Exception as e:
            log.exception("mic failed")
            self.session = None
            self.fsm.external_stop()
            self._emit_error(f"Mikrofon-Fehler: {e}")
            return
        s.worker = asyncio.create_task(self._asr_worker(s))
        s.snap = asyncio.create_task(self._snapshot())

    def _feed(self, s: Session, block: np.ndarray) -> None:
        for chunk in s.chunker.feed(block):
            s.queue.put_nowait(chunk)

    def _end(self, s: Session) -> np.ndarray:
        audio = self.recorder.stop()
        self.recorder.listeners = []
        if self.session is s:
            self.session = None
        if s.confirmed:
            asyncio.create_task(hypr.grab_escape(False))
        if self.cfg.audio.pause_media:
            asyncio.create_task(self.media.resume())
        return audio

    # ------------------------------------------------------------------ ASR
    async def _asr_worker(self, s: Session) -> None:
        await self.asr_ready.wait()
        terms = self.store.terms()
        base_prompt = dictionary.asr_prompt(terms)
        while True:
            chunk = await s.queue.get()
            if chunk is None:
                return
            if len(chunk) < 1600:
                continue
            prev = " ".join(s.texts)[-200:]
            prompt = (base_prompt + ". " + prev).strip(". ") if prev or base_prompt else ""
            try:
                r = await self.asr.transcribe(chunk, prompt=prompt, languages=self.cfg.asr.languages)
            except Exception as e:
                log.exception("ASR failed")
                self._emit_error(f"Spracherkennung fehlgeschlagen: {e}")
                continue
            s.asr_s += r.seconds
            if r.text:
                s.texts.append(r.text)
                s.langs.append(r.language)
                self.ipc.emit("partial", text=" ".join(s.texts))

    async def _snapshot(self) -> Snapshot:
        app, is_term = await hypr.active_window()
        snap = Snapshot(app=app, is_terminal=is_term or app.is_terminal)
        if self.cfg.privacy.context_awareness:
            snap.text = await asyncio.to_thread(self.focus.snapshot, app.pid)
            app.url = snap.text.url
        snap.category = categorize(app, self.cfg.styles.app_overrides)
        return snap

    # ------------------------------------------------------------------ finish
    async def _finish(self, s: Session, audio: np.ndarray) -> None:
        t_stop = time.perf_counter()
        dur = len(audio) / 16000
        if not s.confirmed or dur < 0.3 or (s.chunker.vad and not s.chunker.has_speech):
            s.queue.put_nowait(None)
            self._set_state("processing" if self.processing else "idle")
            return
        self.processing += 1
        self._set_state("processing")
        audio_path = C.AUDIO_DIR / f"{int(time.time() * 1000)}.flac"
        did = None
        try:
            await asyncio.to_thread(sf.write, audio_path, audio, 16000, format="FLAC")
            did = self.store.add_dictation(mode=s.mode, status="pending", audio_path=str(audio_path),
                                           duration_s=round(dur, 2), asr_engine=self.asr.name)
            if (last := s.chunker.flush()) is not None:
                s.queue.put_nowait(last)
            s.queue.put_nowait(None)
            await s.worker
            raw = " ".join(s.texts).strip()
            try:
                snap = await asyncio.wait_for(s.snap, 0.5)
            except (TimeoutError, Exception):
                snap = Snapshot()
            lang = next((x for x in s.langs if x), "")
            self.store.update_dictation(did, raw=raw, language=lang, app_class=snap.app.wm_class,
                                        app_title=snap.app.title[:200], category=snap.category,
                                        asr_ms=int(s.asr_s * 1000))
            if not raw:
                self.store.update_dictation(did, status="empty")
                return
            if s.mode == "command":
                await self._command(did, raw, snap)
            else:
                await self._dictate(did, raw, snap, t_stop)
        except Exception as e:
            log.exception("processing failed")
            if did:
                self.store.update_dictation(did, status="failed")
            self.sounds.play("error")
            self._emit_error(f"Verarbeitung fehlgeschlagen: {e}")
        finally:
            if self.cfg.privacy.audio_retention_days == 0:
                audio_path.unlink(missing_ok=True)
                if did:
                    self.store.update_dictation(did, audio_path=None)
            self.processing -= 1
            self._set_state("recording" if self.session and self.session.confirmed else
                            "processing" if self.processing else "idle")

    def _style_for(self, category: str) -> str:
        return getattr(self.cfg.styles, category, "formal")

    async def _clean(self, raw: str, snap: Snapshot) -> cleanup.Result:
        style = self._style_for(snap.category)
        ctx = cleanup.Context(app=snap.app.wm_class or snap.app.title, category=snap.category,
                              before_cursor=snap.text.before, after_cursor=snap.text.after,
                              names=snap.text.names, is_terminal=snap.is_terminal)
        return await cleanup.process(raw, llm=self.llm, model=self.cfg.llm.cleanup_model,
                                     level=self.cfg.cleanup.level, style=style, ctx=ctx,
                                     terms=self.store.terms(), snips=self.store.snippets())

    async def _dictate(self, did: int, raw: str, snap: Snapshot, t_stop: float) -> None:
        res = await self._clean(raw, snap)
        ok = await inject.paste_text(res.text, snap.is_terminal, submit=res.submit)
        latency = int((time.perf_counter() - t_stop) * 1000)
        self.store.update_dictation(did, cleaned=res.text, status="inserted" if ok else "failed",
                                    style=self._style_for(snap.category), llm_model=self.cfg.llm.cleanup_model
                                    if res.used_llm else None, guard=res.guard_reason,
                                    llm_ms=int(res.llm_seconds * 1000), latency_ms=latency,
                                    words=len(res.text.split()))
        log.info("dictation %d: %d ms (asr %s, llm %d ms) %r", did, latency, self.asr.name,
                 int(res.llm_seconds * 1000), res.text[:80])
        self.ipc.emit("inserted", id=did, text=res.text, latency_ms=latency)

    # ------------------------------------------------------------------ command mode
    async def _selection(self, snap: Snapshot) -> str:
        if snap.text.selection:
            return snap.text.selection
        if snap.text.pid:
            return ""  # AT-SPI sees this app and reports no selection: trust it over PRIMARY
        if await self.primary.fresh_for(45.0):
            got = await inject.read_clipboard(primary=True)
            if got:
                return got[1].decode(errors="replace")
        return ""

    async def _command(self, did: int, raw: str, snap: Snapshot, ask_only: bool = False) -> None:
        instruction = guard.strip_wrapping(raw)
        selection = "" if ask_only else await self._selection(snap)
        transforms = {r["name"]: r["prompt"] for r in self.store.q("SELECT name, prompt FROM transforms")}
        r = router.route(instruction, selection, transforms)
        self.store.update_dictation(did, instruction=instruction, mode="command")
        log.info("command %r → %s", instruction, r.kind)
        if r.kind == "search":
            await hypr._run("xdg-open", r.arg)
            self.store.update_dictation(did, status="inserted", cleaned=r.arg)
        elif r.kind == "dictionary":
            self.store.add_term(r.arg)
            await hypr.notify(f"„{r.arg}“ zum Wörterbuch hinzugefügt")
            self.store.update_dictation(did, status="inserted", cleaned=r.arg)
        elif r.kind in ("edit", "transform"):
            system = prompts.TRANSFORM_SYSTEM if r.kind == "transform" else prompts.COMMAND_EDIT_SYSTEM
            user = (f"TRANSFORM PROMPT:\n{r.prompt}\n\nTEXT:\n{selection}" if r.kind == "transform"
                    else f"SELECTED TEXT:\n{selection}\n\nINSTRUCTION:\n{instruction}")
            out = await self.llm.chat(self.cfg.llm.command_model,
                                      [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                      temperature=0.2, timeout=60)
            text = guard.strip_wrapping(out.text)
            ok = await inject.paste_text(text, snap.is_terminal)
            self.store.update_dictation(did, cleaned=text, status="inserted" if ok else "failed",
                                        llm_model=self.cfg.llm.command_model, llm_ms=int(out.seconds * 1000))
        elif r.kind == "ask":
            self.ipc.emit("answer", id=did, question=instruction, text="", pending=True)
            out = await self.llm.chat(self.cfg.llm.command_model,
                                      [{"role": "system", "content": prompts.ANSWER_SYSTEM},
                                       {"role": "user", "content": instruction}], temperature=0.3, timeout=90)
            text = guard.strip_wrapping(out.text)
            self.last_answer = {"id": did, "question": instruction, "text": text, "is_terminal": snap.is_terminal}
            self.store.update_dictation(did, mode="answer", cleaned=text, status="answered",
                                        llm_model=self.cfg.llm.command_model, llm_ms=int(out.seconds * 1000))
            if any(True for _ in self.ipc.subscribers):
                self.ipc.emit("answer", id=did, question=instruction, text=text, pending=False)
            else:
                await inject.copy_only(text)
                await hypr.notify(text[:300] + ("…" if len(text) > 300 else "") + "\n(in Zwischenablage)", 15000)

    async def _paste_last(self) -> None:
        last = self.store.last_inserted()
        if last and last.get("cleaned"):
            _, is_term = await hypr.active_window()
            await inject.paste_text(last["cleaned"], is_term)

    # ------------------------------------------------------------------ recovery / retention
    async def _recover(self) -> None:
        pend = self.store.pending_recovery()
        for row in pend:
            p = Path(row["audio_path"])
            if not p.exists():
                self.store.update_dictation(row["id"], status="failed")
                continue
            try:
                audio, _ = await asyncio.to_thread(sf.read, p, dtype="float32")
                r = await self.asr.transcribe(audio, languages=self.cfg.asr.languages)
                res = await self._clean(r.text, Snapshot())
                self.store.update_dictation(row["id"], raw=r.text, cleaned=res.text, status="recovered",
                                            words=len(res.text.split()))
            except Exception:
                log.exception("recovery of %s failed", p)
        if pend:
            await hypr.notify(f"{len(pend)} unterbrochene(s) Diktat(e) wiederhergestellt – siehe Hub › Verlauf")

    async def _retention(self) -> None:
        while True:
            cutoff = time.time() - self.cfg.privacy.audio_retention_days * 86400
            for row in self.store.q("SELECT id, audio_path FROM dictations WHERE ts < ? AND audio_path IS NOT NULL",
                                    (cutoff,)):
                Path(row["audio_path"]).unlink(missing_ok=True)
                self.store.update_dictation(row["id"], audio_path=None)
            await asyncio.sleep(3600)

    # ------------------------------------------------------------------ IPC API
    def _register_ipc(self) -> None:
        m = self.ipc.method
        st = self.store

        @m("status")
        async def _(p):
            return {**self._state_event(), "asr": self.asr.name, "asr_ready": self.asr_ready.is_set(),
                    "llm": self.cfg.llm.cleanup_model}

        @m("toggle")
        async def _(p):
            if self.session:
                self.fsm.external_stop()
                self._act(Action.STOP)
            else:
                self._act(Action.START, p.get("mode", "dictate"))
                self._act(Action.CONFIRM)
                self._act(Action.LOCK)
                self.fsm.external_lock()
            return True

        @m("stop")
        async def _(p):
            if self.session:
                self.fsm.external_stop()
                self._act(Action.STOP)
            return True

        @m("cancel")
        async def _(p):
            if self.session:
                self.fsm.external_stop()
                self._act(Action.CANCEL)
            return True

        @m("paste_last")
        async def _(p):
            await self._paste_last()
            return True

        @m("answer.insert")
        async def _(p):
            if self.last_answer:
                _, is_term = await hypr.active_window()
                await inject.paste_text(self.last_answer["text"], is_term)
            return True

        @m("answer.copy")
        async def _(p):
            if self.last_answer:
                await inject.copy_only(self.last_answer["text"])
            return True

        @m("history")
        async def _(p):
            return st.history(int(p.get("limit", 200)), p.get("search", ""))

        @m("stats")
        async def _(p):
            return st.stats()

        @m("retry")
        async def _(p):
            row = st.q("SELECT * FROM dictations WHERE id=?", (int(p["id"]),))
            if not row or not row[0]["audio_path"] or not Path(row[0]["audio_path"]).exists():
                raise RuntimeError("Audio nicht mehr vorhanden")
            audio, _ = await asyncio.to_thread(sf.read, row[0]["audio_path"], dtype="float32")
            r = await self.asr.transcribe(audio, prompt=dictionary.asr_prompt(st.terms()),
                                          languages=self.cfg.asr.languages)
            snap = Snapshot(app=AppInfo(wm_class=row[0]["app_class"] or ""), category=row[0]["category"] or "other")
            res = await self._clean(r.text, snap)
            st.update_dictation(row[0]["id"], raw=r.text, cleaned=res.text)
            return {"raw": r.text, "cleaned": res.text}

        @m("dictionary.list")
        async def _(p):
            return st.q("SELECT * FROM dictionary ORDER BY starred DESC, term")

        @m("dictionary.add")
        async def _(p):
            st.add_term(p["term"], p.get("sounds_like", []), p.get("starred", False))
            return True

        @m("dictionary.remove")
        async def _(p):
            st.x("DELETE FROM dictionary WHERE id=?", (int(p["id"]),))
            return True

        @m("suggestions.list")
        async def _(p):
            return st.q("SELECT * FROM dictionary_suggestions WHERE status='new' ORDER BY created DESC")

        @m("suggestions.resolve")
        async def _(p):
            row = st.q("SELECT * FROM dictionary_suggestions WHERE id=?", (int(p["id"]),))
            if row and p.get("accept"):
                st.add_term(row[0]["term"], [row[0]["heard"]] if row[0]["heard"] else [])
            st.x("UPDATE dictionary_suggestions SET status=? WHERE id=?",
                 ("accepted" if p.get("accept") else "dismissed", int(p["id"])))
            return True

        @m("snippets.list")
        async def _(p):
            return st.q("SELECT * FROM snippets ORDER BY trigger")

        @m("snippets.add")
        async def _(p):
            st.x("INSERT INTO snippets(trigger, text, created) VALUES (?,?,?) ON CONFLICT(trigger) "
                 "DO UPDATE SET text=excluded.text", (p["trigger"][:60], p["text"][:4000], time.time()))
            return True

        @m("snippets.remove")
        async def _(p):
            st.x("DELETE FROM snippets WHERE id=?", (int(p["id"]),))
            return True

        @m("transforms.list")
        async def _(p):
            return st.q("SELECT * FROM transforms ORDER BY name")

        @m("transforms.add")
        async def _(p):
            st.x("INSERT INTO transforms(name, prompt, created) VALUES (?,?,?) ON CONFLICT(name) "
                 "DO UPDATE SET prompt=excluded.prompt", (p["name"], p["prompt"], time.time()))
            return True

        @m("transforms.remove")
        async def _(p):
            st.x("DELETE FROM transforms WHERE id=?", (int(p["id"]),))
            return True

        @m("transforms.run")
        async def _(p):
            """Apply a transform to the current selection (from the bar-widget menu)."""
            row = st.q("SELECT prompt FROM transforms WHERE name=?", (p["name"],))
            if not row:
                raise RuntimeError("unknown transform")
            snap = await self._snapshot()
            sel = await self._selection(snap)
            if not sel:
                await hypr.notify("Kein Text markiert")
                return False
            out = await self.llm.chat(self.cfg.llm.command_model, [
                {"role": "system", "content": prompts.TRANSFORM_SYSTEM},
                {"role": "user", "content": f"TRANSFORM PROMPT:\n{row[0]['prompt']}\n\nTEXT:\n{sel}"}], timeout=60)
            await inject.paste_text(guard.strip_wrapping(out.text), snap.is_terminal)
            return True

        @m("config.get")
        async def _(p):
            return json.loads(self.cfg.model_dump_json())

        @m("config.set")
        async def _(p):
            new = C.Config.model_validate({**json.loads(self.cfg.model_dump_json()), **p})
            C.save(new)
            restart_asr = new.asr != self.cfg.asr
            self.cfg = new
            self.fsm = HotkeyFSM.from_config(new.shortcuts)
            self.sounds.enabled, self.sounds.volume = new.audio.sounds, new.audio.sound_volume
            self.recorder.priority = new.audio.mic_priority
            if restart_asr:
                self.asr_ready.clear()
                await self.asr.stop()
                self.asr = make_engine(new)
                asyncio.create_task(self._warmup())
            return True

        @m("devices")
        async def _(p):
            from ..audio.capture import list_inputs
            return list_inputs()

    def _register_ipc_extra(self) -> None:
        m = self.ipc.method
        st = self.store

        @m("history.delete")
        async def _(p):
            row = st.q("SELECT audio_path FROM dictations WHERE id=?", (int(p["id"]),))
            if row and row[0]["audio_path"]:
                Path(row[0]["audio_path"]).unlink(missing_ok=True)
            st.x("DELETE FROM dictations WHERE id=?", (int(p["id"]),))
            return True

        @m("history.clear")
        async def _(p):
            for row in st.q("SELECT audio_path FROM dictations WHERE audio_path IS NOT NULL"):
                Path(row["audio_path"]).unlink(missing_ok=True)
            st.x("DELETE FROM dictations")
            return True

        @m("audio.play")
        async def _(p):
            row = st.q("SELECT audio_path FROM dictations WHERE id=?", (int(p["id"]),))
            if not row or not row[0]["audio_path"] or not Path(row[0]["audio_path"]).exists():
                raise RuntimeError("Audio nicht mehr vorhanden")
            await asyncio.create_subprocess_exec("pw-play", row[0]["audio_path"])
            return True

        @m("dictionary.star")
        async def _(p):
            st.x("UPDATE dictionary SET starred=? WHERE id=?", (int(bool(p.get("starred"))), int(p["id"])))
            return True

        @m("dictionary.update")
        async def _(p):
            st.x("UPDATE dictionary SET term=?, sounds_like=? WHERE id=?",
                 (p["term"].strip(), json.dumps(p.get("sounds_like", [])), int(p["id"])))
            return True

        # ---- notes (scratchpad) ------------------------------------------------
        @m("notes.list")
        async def _(p):
            return st.q("SELECT id, title, substr(body, 1, 200) AS preview, updated, position FROM notes "
                        "WHERE archived=0 ORDER BY position, updated DESC")

        @m("notes.get")
        async def _(p):
            r = st.q("SELECT * FROM notes WHERE id=?", (int(p["id"]),))
            return r[0] if r else None

        @m("notes.save")
        async def _(p):
            now = time.time()
            body, title = p.get("body", ""), p.get("title") or ""
            if not title:
                title = (body.strip().splitlines() or ["Neue Notiz"])[0][:60] or "Neue Notiz"
            if p.get("id"):
                nid = int(p["id"])
                last = st.q("SELECT ts, body FROM note_versions WHERE note_id=? ORDER BY ts DESC LIMIT 1", (nid,))
                if not last or (now - last[0]["ts"] > 120 and last[0]["body"] != body):
                    st.x("INSERT INTO note_versions(note_id, body, ts) VALUES (?,?,?)", (nid, body, now))
                st.x("UPDATE notes SET title=?, body=?, updated=? WHERE id=?", (title, body, now, nid))
            else:
                nid = st.x("INSERT INTO notes(title, body, created, updated, position) VALUES (?,?,?,?, "
                           "(SELECT COALESCE(MAX(position),0)+1 FROM notes))", (title, body, now, now))
                st.x("INSERT INTO note_versions(note_id, body, ts) VALUES (?,?,?)", (nid, body, now))
            self.ipc.emit("notes_changed", id=nid)
            return nid

        @m("notes.delete")
        async def _(p):
            st.x("DELETE FROM notes WHERE id=?", (int(p["id"]),))
            self.ipc.emit("notes_changed", id=int(p["id"]))
            return True

        @m("notes.versions")
        async def _(p):
            return st.q("SELECT id, ts, body FROM note_versions WHERE note_id=? ORDER BY ts DESC LIMIT 50",
                        (int(p["id"]),))

        # ---- misc ------------------------------------------------------------
        @m("command.run")
        async def _(p):
            """Run Command Mode with typed text instead of speech (testing, scripts)."""
            snap = await self._snapshot()
            did = self.store.add_dictation(mode="command", status="pending", raw=p["text"])
            await self._command(did, p["text"], snap, ask_only=p.get("ask_only", False))
            return True

        @m("models.list")
        async def _(p):
            r = await self.llm.client.get(self.cfg.llm.base_url + "/api/tags")
            return sorted(x["name"] for x in r.json().get("models", []))

        @m("service.restart")
        async def _(p):
            import os
            import sys

            async def later():
                await asyncio.sleep(0.3)
                await self.asr.stop()
                os.execv(sys.executable, [sys.executable, *sys.argv])
            asyncio.create_task(later())
            return True



def main() -> None:
    import os

    logging.basicConfig(level=os.environ.get("WHISPRFAKE_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    (C.RUNTIME_DIR / "whisprfake.pid").write_text(str(os.getpid()))
    cfg = C.load()
    if not C.CONFIG_PATH.exists():
        C.save(cfg)
    asyncio.run(Daemon(cfg).run())
