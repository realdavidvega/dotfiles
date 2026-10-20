#!/usr/bin/env python3
"""Turn text into a voice note, locally and offline.

Usage:
  voice.py speak --text-file F --out X.ogg [--voice am_michael] [--speed 1.0] [--no-cache]
  voice.py check

speak voices the text with Kokoro and encodes it with ffmpeg as mono OGG/Opus
at 32 kbps, the format Telegram plays as a voice note. check confirms the model
files and ffmpeg are present and the engine imports, without loading a model.

Every verb prints one JSON line as the last line of stdout. Success is
{"ok": true, ...} and failure is {"ok": false, "error": "..."} with a non-zero
exit. speak reports {"path", "seconds", "cached", "peak_mb"}, where seconds is
rounded up and peak_mb is the larger of this process's and its children's peak
resident memory. Anything an engine prints goes to stderr.

The root is VOICE_HOME when set. Otherwise it is the parent of the directory
this script sits in, when that directory is named bin and its parent holds
models.lock. Otherwise the CLI refuses to run, so a copy in a checkout never
writes models or cache into it. Models are read from <root>/models and the
output cache lives in <root>/cache/tts, where entries older than 14 days are
removed on each speak. Callers should point XDG_CACHE_HOME at a sibling such as
<root>/cache/xdg so other libraries never share the swept directory.

Nothing here touches the network. The model files are installed and verified
against models.lock by restoration_scripts/54-voice.sh.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import math
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

# Paths under <root>/models. models.lock holds each file's URL and sha256.
KOKORO_MODEL = "kokoro/kokoro-v1.0.onnx"
KOKORO_VOICES = "kokoro/voices-v1.0.bin"
MODELS = (KOKORO_MODEL, KOKORO_VOICES)
# Imported by check, without loading a model, so a broken venv shows up before a job runs.
ENGINE_MODULES = ("onnxruntime", "kokoro_onnx")

DEFAULT_VOICE = "am_michael"
SAMPLE_RATE = 24000
BITRATE = "32k"
THREADS = 2
CACHE_DAYS = 14
# Kokoro's context is 512 phoneme tokens. Batching by sentence under this many
# characters keeps each call well inside it, so nothing is silently cut off.
BATCH_CHARS = 320
BATCH_GAP_S = 0.12
# Emoji glue that is not itself a symbol: variation selectors, the zero-width
# joiner and the keycap mark.
EMOJI_GLUE = {"\ufe0e", "\ufe0f", "\u200d", "\u20e3"}


class Refusal(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal(message)


def clean(text: str) -> str:
    """Drop emoji and symbols, read a middle dot as a comma, collapse spaces."""
    text = text.replace("·", ",")
    kept = []
    for ch in text:
        if ch in EMOJI_GLUE or unicodedata.category(ch) in ("So", "Sk", "Cs", "Co"):
            kept.append(" ")
        else:
            kept.append(ch)
    text = re.sub(r"\s+", " ", "".join(kept))
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",(?:\s*,)+", ",", text)
    return text.strip(" ,")


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text)
    out: list[str] = []
    for part in parts:
        while len(part) > BATCH_CHARS:
            comma = part.rfind(", ", 0, BATCH_CHARS)
            space = part.rfind(" ", 0, BATCH_CHARS + 1)
            cut = comma + 1 if comma > 40 else space if space > 0 else BATCH_CHARS
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            out.append(part)
    return out


def batches(text: str) -> list[str]:
    out: list[str] = []
    cur = ""
    for s in sentences(text):
        if cur and len(cur) + 1 + len(s) > BATCH_CHARS:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def voice_root(script: str | None = None) -> Path:
    home = os.environ.get("VOICE_HOME")
    if home:
        return Path(home)
    here = Path(script or __file__).resolve().parent
    if here.name == "bin" and (here.parent / "models.lock").is_file():
        return here.parent
    raise Refusal("no voice root: set VOICE_HOME, or run the staged copy in <root>/bin beside models.lock")


def cache_key(text: str, voice: str, speed: float) -> str:
    material = json.dumps([KOKORO_MODEL, voice, speed, text], ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def sweep(cache_dir: Path, now: float | None = None) -> None:
    limit = (now or time.time()) - CACHE_DAYS * 86400
    for entry in cache_dir.iterdir():
        try:
            if entry.is_file() and entry.stat().st_mtime < limit:
                entry.unlink()
        except OSError:
            pass


def cached(cache_dir: Path, key: str) -> tuple[Path, int] | None:
    ogg, meta = cache_dir / f"{key}.ogg", cache_dir / f"{key}.json"
    try:
        seconds = json.loads(meta.read_text(encoding="utf-8"))["seconds"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(seconds, int) or not ogg.is_file():
        return None
    for path in (ogg, meta):
        with contextlib.suppress(OSError):
            os.utime(path)
    return ogg, seconds


def atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def store(cache_dir: Path, key: str, ogg: Path, seconds: int) -> None:
    # The sidecar goes last, so a half-written entry is never read as a hit.
    atomic_write(cache_dir / f"{key}.ogg", ogg.read_bytes())
    atomic_write(cache_dir / f"{key}.json", json.dumps({"seconds": seconds}).encode("utf-8"))


def peak_mb() -> int:
    usage = max(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    )
    # ru_maxrss is in bytes on macOS and in kilobytes on Linux.
    return round(usage / (1024 * 1024 if sys.platform == "darwin" else 1024))


def ffmpeg_args(ffmpeg: str, out: Path) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
        "-c:a", "libopus", "-b:a", BITRATE, "-application", "voip",
        "-f", "ogg", str(out),
    ]


def encode(ffmpeg: str, pcm: bytes, out: Path) -> None:
    done = subprocess.run(ffmpeg_args(ffmpeg, out), input=pcm, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise Refusal(f"ffmpeg failed ({done.returncode}): {tail[0]}")


@contextlib.contextmanager
def stdout_to_stderr():
    """Send anything written to fd 1 to stderr, so stdout carries only the result."""
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, 1)
        os.close(saved)


class KokoroEngine:
    def __init__(self, root: Path, voice: str, speed: float):
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            os.environ.setdefault(name, str(THREADS))
        import onnxruntime as rt
        from kokoro_onnx import Kokoro

        opts = rt.SessionOptions()
        opts.intra_op_num_threads = THREADS
        opts.inter_op_num_threads = 1
        session = rt.InferenceSession(str(root / "models" / KOKORO_MODEL), opts, providers=["CPUExecutionProvider"])
        self.k = Kokoro.from_session(session, str(root / "models" / KOKORO_VOICES))
        if voice not in self.k.get_voices():
            raise Refusal(f"unknown voice {voice!r}, try one of: {', '.join(sorted(self.k.get_voices()))}")
        self.voice, self.speed = voice, speed

    def pcm(self, text: str) -> bytes:
        samples, rate = self.k.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        if rate != SAMPLE_RATE:
            raise Refusal(f"Kokoro returned {rate} Hz, expected {SAMPLE_RATE}")
        return samples.astype("<f4").tobytes()


def load_engine(root: Path, voice: str, speed: float) -> KokoroEngine:
    return KokoroEngine(root, voice, speed)


def synthesize(engine, text: str) -> bytes:
    gap = bytes(4 * int(SAMPLE_RATE * BATCH_GAP_S))
    return gap.join(engine.pcm(batch) for batch in batches(text))


def missing_models(root: Path) -> list[str]:
    return [name for name in MODELS if not (root / "models" / name).is_file()]


def check(args: argparse.Namespace) -> dict:
    root = voice_root()
    missing = missing_models(root)
    if missing:
        raise Refusal(f"missing model files in {root / 'models'}: {', '.join(missing)}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise Refusal("ffmpeg not found on PATH")
    for module in ENGINE_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            raise Refusal(f"{module} does not import: {type(exc).__name__}: {exc}") from exc
    return {"ok": True, "models": [str(root / "models" / name) for name in MODELS], "ffmpeg": ffmpeg}


def place(source: Path, out: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".voice-", suffix=".ogg")
    os.close(fd)
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def speak(args: argparse.Namespace) -> dict:
    if not 0.5 <= args.speed <= 2.0:
        raise Refusal("speed must be between 0.5 and 2.0")
    text = clean(Path(args.text_file).read_text(encoding="utf-8"))
    if not text:
        raise Refusal("nothing to say after cleaning the text")
    out = Path(args.out)
    if not out.parent.is_dir():
        raise Refusal(f"no such directory: {out.parent}")
    root = voice_root()
    cache_dir = root / "cache" / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    sweep(cache_dir)
    key = cache_key(text, args.voice, args.speed)

    hit = None if args.no_cache else cached(cache_dir, key)
    if hit:
        place(hit[0], out)
        return {"ok": True, "path": str(out), "seconds": hit[1], "cached": True, "peak_mb": peak_mb()}

    missing = missing_models(root)
    if missing:
        raise Refusal(f"missing model files in {root / 'models'}: {', '.join(missing)}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise Refusal("ffmpeg not found on PATH")

    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".voice-", suffix=".ogg")
    os.close(fd)
    encoded = Path(tmp)
    try:
        with stdout_to_stderr():
            pcm = synthesize(load_engine(root, args.voice, args.speed), text)
            if not pcm:
                raise Refusal("the engine produced no audio")
            seconds = math.ceil(len(pcm) / 4 / SAMPLE_RATE)
            encode(ffmpeg, pcm, encoded)
        if not args.no_cache:
            store(cache_dir, key, encoded, seconds)
        os.replace(encoded, out)
    finally:
        with contextlib.suppress(OSError):
            encoded.unlink()
    return {"ok": True, "path": str(out), "seconds": seconds, "cached": False, "peak_mb": peak_mb()}


def parser() -> Parser:
    top = Parser(prog="voice.py", description="Turn text into a voice note, locally and offline.")
    verbs = top.add_subparsers(dest="verb", required=True, parser_class=Parser)
    say = verbs.add_parser("speak", help="voice a text file as an OGG/Opus voice note")
    say.add_argument("--text-file", required=True)
    say.add_argument("--out", required=True)
    say.add_argument("--voice", default=DEFAULT_VOICE)
    say.add_argument("--speed", type=float, default=1.0)
    say.add_argument("--no-cache", action="store_true", help="neither read nor write the cache")
    say.set_defaults(func=speak)
    verbs.add_parser("check", help="confirm the model files, ffmpeg and the engine imports, without inference").set_defaults(func=check)
    return top


def main(argv: list[str] | None = None) -> int:
    # The cache and the output hold private audio.
    os.umask(0o077)
    try:
        args = parser().parse_args(argv)
        result = args.func(args)
    except Refusal as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
