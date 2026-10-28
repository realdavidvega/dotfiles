# Voice CLI

`scripts/voice.py` turns text into a Telegram voice note on the hub, with
Kokoro running locally on the CPU, and turns a voice note back into text with
Parakeet. Nothing it reads or writes leaves the machine. It is a generic tool. Callers such as the Black System transport and
the agent bridge run it out of process and read one JSON line back.

## Install

`restoration_scripts/54-voice.sh` sets it up on a Mint host declared a hub
(the same gate as `52-black-system.sh`). It runs as the hub user and needs
sudo once, to create `/srv/services/voice` and give it to that user. Then it:

1. Stages `voice.py` to `bin/`, and `models.lock` and `sample.txt` to the root,
   as plaintext copies, since the home is encrypted at boot.
2. Builds `venv/` and installs the requirements, only when they differ from
   the copy stamped in `venv/requirements.stamp` by the last install.
3. Downloads each file in `models.lock` that is missing or fails its hash, to
   a `.part` name, checks its sha256 and only then renames it into place.
4. Runs `check`, then `speak --no-cache` on the sample paragraph, and prints
   the seconds of audio, the wall time, the real-time factor and peak memory.
   When `check` reports `"hear": true` it also hears the sample back and
   prints the transcript, its real-time factor and peak memory.

Voice is optional. Any failure prints "Optional: …" and the restore carries
on. A second run with nothing changed installs and downloads nothing.

`ffmpeg` and `python3-venv` come from `os/linux/apt/packages.mint.txt`.

### requirements.in and requirements.txt

`os/linux/srv/voice/requirements.in` names the direct dependencies,
`kokoro-onnx`, `onnx-asr[cpu]` and `sentencepiece`, with minimum versions. `requirements.txt` is the frozen set, made on the hub's own
Mint Python from a clean venv:

```bash
/srv/services/voice/venv/bin/python -m pip freeze > "$DOTFILES_PATH/os/linux/srv/voice/requirements.txt"
```

The script installs `requirements.txt` when it exists and `requirements.in`
otherwise, with a reminder to freeze. Another hub on the same Mint release
installs the frozen file as is. A different release refreezes it.

### Pinning the speech-to-text models

`models.lock` pins Kokoro, Parakeet, Silero and the Marian translator. Lines
are written on a host that can reach Hugging Face, by `scripts/voice_pin.py`,
which resolves a revision to its commit and prints lines pinned to it:

```bash
cd "$DOTFILES_PATH"
lock=os/linux/srv/voice/models.lock
python3 scripts/voice_pin.py parakeet istupakov/parakeet-tdt-0.6b-v3-onnx main \
  encoder-model.int8.onnx decoder_joint-model.int8.onnx vocab.txt config.json >>"$lock"
python3 scripts/voice_pin.py silero istupakov/silero-vad-onnx main silero_vad.onnx >>"$lock"
```

A file stored in LFS takes the sha256 Hugging Face records for it. A small
file kept in git is downloaded once and hashed. Commit the lines, then rerun
`54-voice.sh`, which downloads and verifies the files like any other. Until
then `check` reports `"hear": false` and `hear` fails with reason `models`,
while `speak` works as before.

## Layout

```
/srv/services/voice/          mode 0700, owned by the hub user
  bin/voice.py
  models.lock                 <path under models/> <sha256> <url>, one line per file
  models/kokoro/              kokoro-v1.0.onnx, voices-v1.0.bin
  models/parakeet/            encoder-model.int8.onnx, decoder_joint-model.int8.onnx, vocab.txt, config.json
  models/silero/              silero_vad.onnx
  venv/
  cache/tts/                  voiced output, swept after 14 days
  cache/xdg/                  for XDG_CACHE_HOME, never swept
  sample.txt, sample.ogg      the smoke test's input and output
```

The root is `VOICE_HOME` when set. Otherwise it is the parent of `bin/` when
that parent holds `models.lock`. Otherwise the CLI refuses to run, so the copy
in this public checkout can never write models or cache into it.

## The JSON contract

Every verb prints one JSON line, and it is the last non-empty line on stdout.
Anything an engine prints goes to stderr. Failure is
`{"ok": false, "error": "..."}` with a non-zero exit, and a `hear` failure adds
a `reason` (below).

| Verb | Does | Success line |
|---|---|---|
| `speak --text-file F --out X.ogg [--lang en|es] [--voice VOICE] [--speed 1.0] [--no-cache]` | Voices the text and writes mono OGG/Opus at 32 kbps to `--out` | `{"ok": true, "path": "X.ogg", "seconds": 93, "cached": false, "peak_mb": 820}` |
| `translate --text-file F` | Translates marked owner spans locally without speaking | `{"ok": true, "text": "..."}` |
| `hear --in X.ogg` | Decodes the note to 16 kHz mono WAV and transcribes it | `{"ok": true, "text": "call the dentist", "seconds": 4, "peak_mb": 1400}` |
| `check` | Confirms the speaking model files, ffmpeg and the engine imports, with no inference, and says whether `hear` and translation are installed | `{"ok": true, "models": [...], "ffmpeg": "/usr/bin/ffmpeg", "hear": true, "hear_missing": [], "translate": true}` |

- `seconds` is whole seconds rounded up, because Telegram's `duration` is an
  integer.
- `peak_mb` is the larger of the process's and its children's peak resident
  memory.
- A blank line is a paragraph break, and the voice pauses there for 0.75
  seconds. `speak` cleans each paragraph first. It drops emoji and symbols,
  reads `·` as a comma and collapses whitespace, and a paragraph left empty is
  dropped with its pause. Callers with markup of their own strip it before
  calling.
- Each paragraph is voiced in sentence groups of about 320 characters with a
  short pause between them, which keeps each call inside Kokoro's context. No
  group crosses a paragraph break.
- `seconds` includes the pauses.
- `--speed` runs from 0.5 to 2.0.
- `hear` transcribes 20 seconds or less in one pass. Longer audio is split at
  pauses by Silero VAD into pieces of at most 20 seconds, and their texts are
  joined with spaces. It never caches, and the decoded WAV lives in a
  temporary directory removed before it exits.
- A `hear` failure's `reason` is `models` (a Parakeet or Silero file is
  missing), `audio` (no such file, or ffmpeg could not decode it), `empty`
  (nothing was heard) or `engine` (anything else, ffmpeg missing included).
- `check` fails only for what `speak` needs. Missing speech-to-text files set
  `"hear": false` and list them in `hear_missing`, and an `onnx_asr` that does
  not import sets `hear_error`.

A process killed from outside, or one that espeak-ng aborts, prints no JSON
line at all. Callers treat a missing or unparseable last line as a failure.

## The cache

`speak` caches by a hash of the cleaned paragraphs joined by a blank line, the
model file, language, voice and speed. A text with no break hashes exactly as
the whole cleaned text. Each entry is the OGG plus a small JSON file holding `seconds`, written
last and atomically, so a half-written entry is never a hit. For English the cache is read
before any engine is imported, so a hit costs no model load. Spanish prepares
the translated text before checking its cache. Every `speak`
removes files in `cache/tts/` older than 14 days, and a hit refreshes its
entry. `--no-cache` neither reads nor writes it.

The process sets umask 077, so the cache and the output are readable only by
the hub user. The cache is local to the hub and outside every backup.

## Offline and resource rules

The CLI never touches the network. It loads its models from explicit paths
under the root, so it works at boot with the encrypted home locked. It caps
onnxruntime at two threads.

A caller running it as a service gives it a fixed environment, for example:

```bash
env -i PATH=/usr/bin:/bin LANG=C.UTF-8 HOME=/srv/services/voice \
  VOICE_HOME=/srv/services/voice XDG_CACHE_HOME=/srv/services/voice/cache/xdg \
  OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  /srv/services/voice/venv/bin/python /srv/services/voice/bin/voice.py speak \
  --text-file in.txt --out out.ogg
```

Point `XDG_CACHE_HOME` at `cache/xdg`, never at `cache/` itself, so a library's
own cache is not swept with the voice notes.

## Verification

```bash
python3 scripts/test_voice.py                 # offline, with fake engines
python3 scripts/test_voice_pin.py             # offline, with canned Hugging Face replies
```

On the hub, after `54-voice.sh`:

```bash
/srv/services/voice/venv/bin/python /srv/services/voice/bin/voice.py check
ffplay -nodisp -autoexit /srv/services/voice/sample.ogg
```

The sample includes a date, 12-hour times and counts, so listen for how those
read. Speech should take less time to make than it lasts (a real-time factor
under 1.0), and peak memory should stay under 1,500 MB. If it is slower, the
Kokoro int8 model is the next step, with a new line in `models.lock`. Once
`hear` is pinned, the smoke run's transcript should read as the sample does.

## Troubleshooting

**speak exits 1 with no JSON and stderr mentions `phontab`.** espeak-ng ignores
a data path longer than 160 characters and falls back to a path compiled into
the wheel. The venv under `/srv/services/voice` is well inside that limit. A
venv or uv cache under a deep temporary directory is not.

**check reports missing model files.** Rerun `54-voice.sh`, which downloads and
verifies them. A hash mismatch leaves the old file and deletes the download.

## Spanish speech and translation

`speak --lang es` uses Kokoro's Spanish phonemizer and `em_alex`. English defaults
to `am_michael`, and `--voice` overrides either. System text outside `⟦…⟧` is
spoken as written. Only marked spans pass through the offline English-to-Spanish
translator. Stops belong outside the spans. Empty spans vanish, a nested opening
marker is ignored, a stray closing marker is dropped, and an opening marker with
no closing marker translates through the end of the input. No translated text
is returned by `speak` or saved outside its optional audio cache.

Spanish dates are expanded after translation, so `16/07/24` is said as
`16 de julio de 2024`. Invalid calendar dates and longer slash-separated values
are left alone. The `translate` verb prints translated text for an explicit
inspection and never creates audio.

The five pinned files live under `models/marian/`: the quantized encoder and
decoder retain their `onnx/` subfolder, and `source.spm`, `target.spm` and
`vocab.json` sit beside it. Pin them with:

```bash
python3 scripts/voice_pin.py marian Xenova/opus-mt-en-es main onnx/encoder_model_quantized.onnx onnx/decoder_model_quantized.onnx
python3 scripts/voice_pin.py marian Helsinki-NLP/opus-mt-en-es main source.spm target.spm vocab.json
```

The pin tool lists nested paths recursively. Commit its output in `models.lock`
and rerun `54-voice.sh`. Marian uses onnxruntime and the pinned `sentencepiece`
dependency in `requirements.txt`. `check` sets `translate: true` only when all
five files exist and `sentencepiece` imports. Missing translator files or its
library make Spanish `speak` refuse with `reason: "models"`, while English stays
available. The installer runs a fixed-span translation smoke check when ready.
