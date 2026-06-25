# Roadmap Inputs

This file stores external research and feature ideas that may inform future
planning. Entries here are not accepted scope, epics, or implementation stories.

## 2026-06-24: Luke's Dev Lab Gemma4 12B Coder Benchmark

- Video: https://www.youtube.com/watch?v=BGZjs1dQfsk
- Title: `Gemma4 12B Coder - Composer 2.5 x Fable 5 v2 vs base - 16GB Local LLM setup`
- Channel: Luke's Dev Lab
- Published: 2026-06-22
- GitHub reference: https://github.com/lukesdevlab/youtube
- Transcript: [lukes-dev-lab-gemma4-12b-coder-transcript.en-orig.vtt](references/lukes-dev-lab-gemma4-12b-coder-transcript.en-orig.vtt)

Model links from the video description:

- Base model: https://huggingface.co/unsloth/gemma-4-12b-it-GGUF
- Fine-tune model: https://huggingface.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF

Notes:

- Transcript source is YouTube auto-captions, not creator-provided subtitles.
- The linked GitHub repo was observed without a license. Future work should reuse
  ideas and task shapes, not copy prompt text or code verbatim unless licensing is
  clarified.
- The video compares Gemma 4 12B base Q4_K_M against a Fable 5 / Composer 2.5
  fine-tune on 16GB VRAM.
- Reported result pattern: similar prefill/decode speed, better long-context
  retrieval for the fine-tune, much worse tool-use/agency behavior for the
  fine-tune, and stronger HumanEval performance from the base model.

Feature ideas to revisit:

- Long-context memory suite with configurable depth probes, repeat count, and
  per-depth pass/fail reporting.
- Tool-use/agency suite with deterministic fake-company tools, trap tools, and
  scoring for both final answer and tool trace quality.
- Richer artifact coding tasks, such as browser-game or simulator builds, kept
  separate from HumanEval/MBPP because they need runtime/UI acceptance checks.
- Local runtime metadata for quantization, context length, KV cache, sampling,
  reasoning mode, speculative decoding, runner, and hardware notes.
- Failure taxonomy for agent-like runs: invalid tool call, unnecessary tool,
  trap-tool use, loop, incomplete chain, runtime crash, and wrong final answer.
