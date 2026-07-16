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

## 2026-07-15: Nemotron 120B TurboQuant-MLX On 48 GB MacBook

- Article: https://medium.com/data-science-collective/nemotron-120b-on-a-48-gb-macbook-27-tok-s-with-turboquant-hybrid-quantization-mlx-529d93cbc960
- Saved PDF: `/Users/fxmartin/Downloads/Nemotron 120B on a 48 GB MacBook: 27 tok:s with TurboQuant Hybrid Quantization (MLX) | by Manjunath Janardhan | Data Science Collective | Medium.pdf`
- Extracted text during review: `/private/tmp/nemotron-medium.txt`
- Author: Manjunath Janardhan
- Published: 2026-05-01
- TurboQuant-MLX GitHub: https://github.com/manjunathshiva/turboquant-mlx
- TurboQuant-MLX PyPI: https://pypi.org/project/turboquant-mlx-full
- Prebuilt model: https://huggingface.co/manjunathshiva/Nemotron-3-Super-120B-A12B-tq3a-tq2e-g32
- Original BF16 model: https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16

Notes:

- The article claims a 120B total / 12B active Nemotron-3 Super model can run on
  a 48 GB Apple Silicon Mac using TurboQuant-MLX per-path hybrid quantization.
- Claimed quantization policy is `tq3a-tq2e-g32`: 3-bit attention, 2-bit
  expert/MoE path, group size 32.
- Claimed footprint is about 36 GB on disk and 40.8 GB peak unified memory,
  with 27.2 tok/s decode on a 779-token generation.
- The article reports uniform 3-bit group-size-32 did not fit the 48 GB target:
  about 50 GB on disk and about 55 GB peak memory.
- Recommended default generation settings are `temp=0.7`,
  `rep_penalty=1.04`, `rep_ctx=256`, and `min_tokens=50`.
- Known caveat: arithmetic/numeric reasoning degrades under the 2-bit expert
  path, especially with repetition penalty. Numeric prompts should drop
  repetition penalty until a calibrated-codebook variant exists.

Feature ideas to revisit:

- Add an extreme 48 GB stretch tier for ultra-compressed 100B+ local models,
  separate from normal MLX/Ollama local candidates.
- Add a `turboquant-mlx` inferencer/runtime class so TurboQuant kernel results
  are not mixed with ordinary `mlx-lm` results.
- Extend local model metadata with quantizer, quantization policy, attention
  bits, expert bits, group size, disk size, peak memory, sampler defaults, and
  known caveats.
- Add a preflight fit-check mode before full benchmark runs: load, generate a
  tiny response, record peak memory, and fail early on Metal memory errors.
- Add a numeric-reasoning canary so heavily compressed expert-path models are
  not ranked as coding-ready if arithmetic regressions are severe.
