# Models to Benchmark Locally for Agentic Coding

Curated shortlist for the project's fixed machine, a MacBook Pro M3 Max with 48 GB
of unified memory. Every entry is open-weight, supports tool calling and long
context, and realistically fits in 48 GB at a sensible quantization with room for a
working context window. Sizes and licenses are from the model cards linked at the
end; re-verify at the time you actually pull a model, since the field moves monthly.

## How to read the fit column

Memory for the weights is roughly `bytes-per-parameter x total parameters`, where
4-bit quantization is about 0.5 GB per billion parameters. On top of that sits the
KV cache (which grows with context length) plus framework and OS overhead. On a
48 GB machine the practical budget for weights is about 36 to 40 GB once you leave
room for a real context window. The crucial subtlety for Mixture-of-Experts models:
**all experts must be resident, so memory is set by total parameters, not active
ones.** Active parameters buy speed, not memory. An 80B-total, 3B-active MoE still
needs roughly 80B worth of memory.

## Tier 1: current baselines (already in the harness)

These are what Part 1 measured. Keep them as the reference points.

| Model | Params | Arch | Context | License | Fit on 48 GB |
|---|---|---|---|---|---|
| Qwen3.6-27B (DFlash) | 27B | Dense + speculative | long | open | Comfortable |
| Qwen3.6-35B-A3B (TurboQuant) | 35B total / ~3B active | Sparse MoE | long | open | Comfortable |

## Tier 2: agentic-coding specialists to add (highest priority)

Purpose-built or coder-tuned models that are the natural local competitors.

| Model | Params | Arch | Context | License | Fit on 48 GB | Why |
|---|---|---|---|---|---|---|
| Devstral Small 2 (24B) | 24B | Dense | 256K | Apache 2.0 | Comfortable, room for long context | Mistral's agentic SWE specialist; strong tool-use, designed for single-machine coding agents |
| Qwen3-Coder-30B-A3B | 30B total / 3B active | Sparse MoE | long | Apache 2.0 | Comfortable | Coder-tuned MoE, efficient, a like-for-like MoE coder against the dense Devstral |
| Qwen3-Coder-Next (80B-A3B) | 80B total / 3B active | Hybrid attention + MoE | 256K | Apache 2.0 | Stretch: needs about 3-bit to fit with context | Best efficiency per active parameter, around 70% on SWE-bench Verified; the "how far can 48 GB push" entry |

## Tier 3: architecture and family contrasts (comparative value)

Adds non-Qwen lineage and isolates variables (coder fine-tune versus general, one
family versus another).

| Model | Params | Arch | Context | License | Fit on 48 GB | Why |
|---|---|---|---|---|---|---|
| gpt-oss-20b | 20.9B total / 3.6B active | Sparse MoE (native MXFP4 4.25-bit) | long | Apache 2.0 | Comfortable | Different lineage (OpenAI), ships natively 4-bit; a clean non-Qwen reference |
| Qwen3-32B (general, dense) | 32B | Dense | long | Apache 2.0 | Comfortable | Pair against the coder-tuned models to isolate the effect of coding fine-tuning |

## Tier 4: small and fast (latency floor, and lower-RAM relevance)

| Model | Params | Arch | Context | License | Fit on 48 GB | Why |
|---|---|---|---|---|---|---|
| Qwen3-8B | 8B | Dense | long | Apache 2.0 | Trivial | The "how small can you go and stay useful" floor; also the model that runs on 16 GB machines |

## Out of local range on 48 GB (reserve for the Part 4 cloud comparison)

These do not fit 48 GB and belong in the cloud-versus-local comparison, run through
hosted endpoints rather than locally:

- gpt-oss-120b (116.8B total / 5.1B active): needs roughly 64 GB or more.
- Devstral 2 (123B), Qwen3-235B-A22B: 128 GB-class machines.
- DeepSeek V3/V4, GLM-5.x, Kimi K2.x, MiniMax M3: frontier MoE, hundreds of GB to
  over a terabyte; cloud only for this project.

## Suggested benchmarking order

1. Add the two Tier 2 specialists (Devstral Small 2, Qwen3-Coder-30B-A3B) first;
   they are the strongest local agentic coders that fit comfortably.
2. Add gpt-oss-20b for a non-Qwen architecture point.
3. Add Qwen3-Coder-Next as the deliberate "stretch the 48 GB envelope" case, using a
   low-bit quant, and watch for the swap cliff seen in Part 1.
4. Add Qwen3-8B and Qwen3-32B (general) only if you want the size-scaling and
   coder-fine-tune-effect curves.

## Practical notes

- Format: the project's MLX servers (DFlash, TurboQuant, mlx-lm) need MLX-format
  weights; llama.cpp and Ollama need GGUF. Community MLX and GGUF conversions exist
  for all of the above, but confirm one is published or convert it before adding the
  model to `configs/models.yaml`.
- Quantizer matters as much as bit-width. A build can come from the model maker
  (gpt-oss ships native 4-bit MXFP4) or from a third-party specialist (Unsloth,
  mlx-community, bartowski), and modern builds are often layer-selective rather than
  uniform, so quality varies between two "4-bit" files of the same model. Pin the
  exact build and revision in the config, and prefer a calibration-based or dynamic
  quant where available. See Part 0, section 4, in the series plan.
- Each new local model is one `models` entry (name, base_url, model_id, pinned
  revision, prices set to zero). Keep `concurrency: 1` for all local entries.
- Reasoning models: decide the thinking policy (Part 0 and the methodology doc) per
  model; some of these default to a thinking mode that inflates token budgets.

## Sources (model cards and reports)

- Qwen3-Coder-Next (80B-A3B): https://huggingface.co/Qwen/Qwen3-Coder-Next
- Qwen3-Coder-30B-A3B-Instruct: https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct
- Devstral Small 2 (24B): https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512
- Devstral 2 (123B): https://huggingface.co/mistralai/Devstral-2-123B-Instruct-2512
- gpt-oss (20b and 120b) model card: https://arxiv.org/abs/2508.10925
- gpt-oss-20b: https://huggingface.co/openai/gpt-oss-20b
- gpt-oss-120b: https://huggingface.co/openai/gpt-oss-120b
- Qwen3 Technical Report (family sizes, dense and MoE, thinking modes): https://arxiv.org/abs/2505.09388
