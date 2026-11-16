# Voice speed and Uzbek accuracy: what was measured and what is left

Machine: MacBook with an Apple M2, 16 GB RAM, about 15 GiB of free disk.
Everything below that says *synthetic* was spoken by the text-to-speech engine,
not by a person; it is not a real-user test. Real numbers need your voice
(see "Your part").

## Where the time goes (one spoken turn)

Two voice paths exist. The desktop microphone uses `SpeechPipeline`, which
already speaks the brain's reply sentence by sentence. Telegram calls and the
Mini App use `BrowserVoiceSession`; it used to wait for the whole reply before
the first synthesis and now streams sentences too.

| Stage | Before | After this work | Notes |
|---|---|---|---|
| End-of-turn silence | 700 ms | 700 ms | not changed until real recordings show what is safe to cut |
| STT, Faster-Whisper `large-v3-turbo` (CPU) | 5.5-6.0 s, wrong Uzbek (3 synthetic clips: 100 % WER, 30 % CER), +1.96 GB RAM | unchanged | worst on both speed and accuracy |
| STT, Gemini `gemini-3.1-flash-lite` | 1.6-4.1 s, exact on the synthetic clips | unchanged (`thinkingLevel: minimal` gains little) | spikes of 4-7 s are network or free-tier variance |
| Brain first token | 3.7-5.8 s | chat turn 1.5-2 s once warm; first turn of a process 3-4 s; turns that use tools 8-16 s | the model alone answers in 1.2-1.7 s; the rest is prompt size (about 9k tokens, 35 tools) and tool rounds |
| Edge TTS, first audio | 2.0-2.6 s | **1.2-1.5 s** | MP3 is now decoded as it arrives; the service's own first bytes take 1.2-1.35 s |
| Reply length | 17 s of audio, Markdown list | persona asks for one to three short sentences | |
| First reply audio, whole turn (synthetic) | 18.6 s (one run), then median 9.7 s / p95 13.2 s over 4 turns | see the table at the end | |

The 2-4 s target is not reachable with the current parts: end-of-turn silence
0.7 s + Gemini STT about 1.8 s + brain about 1.5 s + Edge TTS 1.2 s is about
5.2 s at best. The one lever that could cut the largest piece is a fast
on-device recognizer, which is why the benchmark below exists.

## A stalled brain

On the question "Tell me about Python in Uzbek" the brain sometimes said nothing
for more than 75 s in the bench (task dump: the turn waited on the brain stream,
the speaking worker on an empty queue; no deadlock in the voice code). Turns
that the router treats as actions, and free-tier throttling, give 8-23 s first
tokens in the same bench. On a call this used to mean silence without end. The
call/Mini App path now speaks a short apology when the brain is silent for 40 s
(25 s mid-reply). The desktop pipeline has its own stall guard already.

## Changes in code

- `BrowserVoiceSession`: the brain stream is cut at the first whole sentence
  (12 characters minimum, later sentences 28) and each sentence is synthesized
  while the brain keeps writing; barge-in cancels the speaking worker as
  before. Per-turn marks (`speech_end`, STT, brain first/total, first audio)
  are logged, sent as a `turn_timing` frame and summarized as median and p95
  (`dodaios/browser_voice/timing.py`).
- Edge TTS decodes its MP3 incrementally (`dodaios/plugins/tts/edge_tts.py`).
- The persona keeps spoken answers short.
- The flash tier no longer names an Ollama server that is not running.
- `[latency] log_jsonl = true` in your `dodaios.toml`: after the app restarts,
  every real microphone turn is written to `state/latency_log.jsonl`;
  `python -m dodaios.tools.latency_report` prints median/p95 per stage.

## Speech recognition candidates

| Candidate | Uzbek | Disk | Notes |
|---|---|---|---|
| Gemini `gemini-3.1-flash-lite` (now) | exact on synthetic clips | none | audio leaves the Mac; free-tier limits |
| Faster-Whisper `large-v3-turbo` (now, fallback) | poor | 1.6 GB (installed) | RAM +1.96 GB while loaded |
| GigaAM Multilingual CTC, INT8 ONNX (`istupakov/gigaam-multilingual-ctc-onnx`, MIT) | model card lists `uz` | 225 MB (the large INT8 is 592 MB) | run with `onnx-asr` 0.12.0 (numpy + the installed onnxruntime); a name "gigaam-multilingual-mlx" does not exist on Hugging Face |
| Whisper large-v3-turbo fine-tuned for Uzbek, CT2 (`hostmepanda/whisper-large-v3-turbo-uzbek-ct2`, MIT) | Uzbek only, quality unknown | 1.6 GB | same engine as today, so no new package |
| MLX Whisper | multilingual Whisper, weak on Uzbek | 0.8-3 GB by size | needs `mlx-whisper` |

Nothing from this table was downloaded. Whether a candidate wins is decided
by `scripts/stt_bench/run.py` on your recordings: WER and CER against the known
sentences, per-clip latency (median and p95), load time and added memory.

## Realtime (Gemini Live) variant

The API lists `gemini-3.8-live`, `gemini-3.1-flash-live-preview` and native-audio
models for this key. Google's pricing page shows no charge for Live on the free
tier, but publishes no per-model rate limits, and Uzbek quality is unverified,
so it was not switched on. Trying it is a decision for you.

## Your part

1. Record the 20 sentences: `python scripts/stt_bench/record.py` in a Terminal
   (macOS asks once for microphone access). Redo it with the fan or music on
   in `--dir ~/voice-bench-noisy` to test noise.
2. `python scripts/stt_bench/run.py --dir ~/voice-bench --engines gemini fwhisper`
   (add `onnx` or another engine only after you approve its download).
3. Restart Dodaios, say the five test sentences, then run
   `python -m dodaios.tools.latency_report`.

## Synthetic bench, same four prompts (not a real-user test)

`scripts/voice_turn_bench.py --synthetic`, Gemini STT and brain, Edge TTS,
one session so the brain remembers. Milliseconds, first audio of each turn.

| Run | STT | Brain first token | TTS first audio | Total to first audio |
|---|---|---|---|---|
| Before streaming and Edge decode, turns 1-4 (median / p95) | 2554 / 3229 | 4293 / 8643 | 2568 / 2722 | 9676 / 13181 |
| After, turn 1 (cold) | 3350 | 7918 | 1353 | 12621 |
| After, turn 2 | 9025 (one slow Gemini call) | 2961 | 1151 | 13137 |
| After, second run, turn 1 | 4038 | 12459 | 1252 | 17749 |
| After, second run, turn 2 (tool turn) | 1747 | 22934 | 2646 | 27327 |

Only the TTS column improved with certainty (2.6 s -> 1.2-1.4 s, outside the
bench: 1.17-1.54 s over six calls). STT and brain times swing by seconds from
call to call on the free tier, so four turns cannot show a median gain there;
the real-voice numbers come from `latency_report` after you use the app.
Peak resident memory of the bench process: about 225 MB.
