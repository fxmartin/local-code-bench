# Series Plan: Benchmarking Local Models for Agentic Coding

This plan operationalizes the threads promised at the end of Part 1: fold in
correctness, push context sizes and model choices further, compare local against
cloud, and put a number on what you give up and gain by bringing the agent home.

A useful starting point is that much of the harness needed for the later parts
already exists from the Part 1 work. The lift per article is smaller than it looks.
Each part below lists its question, the experiments, what is already built versus
what is net-new, the charts, and the decisions it forces.

## The axes

Part 1 measured two axes, speed and energy. The series builds toward five:

1. Speed (prefill tok/s, time to first token): done in Part 1.
2. Energy (joules, tokens per joule): done in Part 1.
3. Correctness (pass@1 on coding suites): Part 2.
4. Cost (dollars for cloud, energy and amortized hardware for local): Part 4.
5. Privacy and operability (qualitative, threaded throughout, synthesized in Part 5).

## Part 0: Setting the scene (the prequel)

Question: before any benchmark means anything, what is agentic coding, why is it
demanding, what do today's models actually look like, and how does running one on
your own machine even work? Part 0 is the primer that makes the rest of the series
legible to a non-specialist. No new measurements: it is explanation and
current-landscape framing. Every architecture and runtime claim below is backed by
a primary source (peer-reviewed paper, arXiv technical report, model card, or
official project repository) listed at the end of this part. Claims about the very
latest models move monthly and must be cited from their own model cards at drafting
time. Part 0 is large and likely splits into two articles: 0a covering what agentic
coding needs and the model landscape and its history (sections 1 to 3), and 0b
covering quantization, memory, and how local inference actually runs (sections 4 to
8).

### 1. What agentic coding demands

An agent does not just answer once. It plans, calls tools, reads and writes files,
runs code, checks results, and iterates over many steps. That imposes a specific
shopping list on the model:

- Reliable tool calling and structured output, so the agent can act, not just talk.
  This is a first-class requirement, not a nice-to-have.
- The largest usable context possible, because each step carries files, logs, and
  history. Effective context matters more than the advertised number.
- Acceptable latency, dominated by prefill (digesting that big context), as Part 1
  showed. A slow reader breaks the loop.
- Enough raw coding quality to be trusted across a long chain, where small errors
  compound.

### 2. The model zoo: dense, MoE, and thinking

Two axes that are often confused:

- Architecture: dense (every parameter fires for every token) versus
  Mixture-of-Experts (only a few expert sub-networks activate per token, so a huge
  model does little work per token).
- Reasoning: whether the model has a thinking mode that reasons step by step before
  answering. This is orthogonal to architecture. A model can be dense or MoE and
  separately have thinking on or off.
- Plus the cross-cutting knobs from Part 1: quantization, and increasingly
  multimodality and very long context.

### 3. The dense-to-MoE story (the 12-month arc)

The accurate history, since it is widely oversimplified, with each claim tied to a
primary source listed at the end of this part:

- MoE is not new. The idea dates to Jacobs, Jordan, Nowlan, and Hinton (1991), and
  Google scaled it for transformers with GShard (2020) and Switch Transformers
  (2021), the latter reaching 1.6 trillion parameters with simplified routing.
- The open-weight MoE moment was Mixtral 8x7B (Mistral, December 2023, paper January
  2024): about 47B total parameters with roughly 13B active per token, matching or
  beating the dense Llama 2 70B and GPT-3.5 at far lower inference cost. That, not
  DeepSeek, is what popularized open MoE.
- DeepSeek's real contribution was scaling MoE efficiently and dragging it to the
  frontier: V2 (May 2024, 236B total and 21B active, introducing multi-head latent
  attention plus the DeepSeekMoE design), V3 (December 2024, 671B total and 37B
  active, with auxiliary-loss-free load balancing), and R1 (January 2025), which
  proved a pure reinforcement-learning recipe could give an open model reasoning on
  par with the best closed models. DeepSeek perfected and proved MoE at scale rather
  than inventing it.
- The architecture and reasoning axes then merged into the mainstream. Qwen3 (May
  2025) is instructive: a single family spanning dense and MoE models from 0.6B to a
  235B-total, 22B-active flagship, with thinking and non-thinking modes unified in
  one model. Through 2025 and 2026 the wave continued across the Qwen, DeepSeek, GLM
  (Z.ai), Kimi (Moonshot), and MiniMax families, several reaching one-million-token
  context under permissive licenses. (Cite each of these latest models from its own
  model card or technical report at drafting time; they move monthly and only the
  ones with a primary source below should be stated as fact.)

### 4. Quantization: who does the shrinking, and why "4-bit" is underspecified

Part 1 introduced quantization as the MP3-style compression that makes a model fit.
Two practical questions decide how good a given quantized model actually is.

Who quantized it. Sometimes the model maker ships its own quantized weights: OpenAI
released gpt-oss already in a 4-bit MXFP4 format, and several labs publish FP8 or
4-bit builds alongside the full-precision release. More often the quantized builds
you download are made by third-party specialists who take the original weights and
convert them: Unsloth, the mlx-community organization for Apple's MLX format, and
bartowski and others for GGUF. The same model can exist in many community builds of
varying quality.

How they did it, uniform versus selective. Early quantization was largely uniform,
every weight squeezed to the same low bit-width, simple but lossy at 4-bit and
below. The field then moved to calibration-aware methods that look at real data to
decide what to protect: GPTQ (2022) and AWQ (2023), the latter showing that
protecting roughly the one percent most salient weight channels, identified by their
activations, sharply cuts the error. The current state of the art is mixed-precision,
layer-selective quantization: keep the sensitive parts (attention, embeddings,
certain experts) at higher precision and push the rest lower, guided by a calibration
pass. The GGUF k-quant formats with an importance matrix do this, and Unsloth's
Dynamic quants (4-bit Dynamic in November 2024, Dynamic 2.0 in 2025) take it
furthest, using a custom per-model scheme that selects different layers to preserve
based on each architecture. So non-uniform quantization did arrive later, and it is
why a careful 4-bit build can now perform close to full precision.

Why it matters for the benchmark: "Qwen3-Coder at 4-bit" is underspecified. The
quantizer and the method change both file size and quality, so a fair comparison
must pin the exact build and revision, not just the nominal bit-width. The harness
already records pinned revisions for this reason.

### 5. Memory: the master constraint

On a laptop, memory is the wall everything hits first. A rough model:

memory is approximately (bytes per parameter) times (total parameters), plus the KV
cache, plus overhead.

Quantization sets the bytes per parameter: 2 bytes at full FP16, about 0.5 bytes at
4-bit, so a 4-bit model needs roughly half a gigabyte per billion parameters for its
weights. On top of the weights sits the KV cache, the model's working memory for the
current context, which grows with how many tokens you feed it. The operating system
and the framework take their share too.

Two consequences are easy to miss:

- For MoE models, all experts must be resident, so memory is set by total parameters,
  not active ones. A model advertised as "80B total, 3B active" still needs roughly
  80B worth of memory. Active parameters buy speed, not memory savings.
- Context competes with weights for the same pool. A longer context means a larger KV
  cache, which leaves less room for the model, and pushing context too far is exactly
  what tipped the 48 GB machine into swap in Part 1.

A practical map of what fits, at 4-bit with room for a working context (approximate,
since quant level and context length move the lines):

| Machine memory | Comfort zone | Example models |
|---|---|---|
| 16 GB | up to about 8B dense | Qwen3-8B and small coders; gpt-oss-20b only at the edge with short context |
| 24 GB | up to about 14B dense, or a small MoE | Qwen3-14B; gpt-oss-20b; Devstral Small 2 (24B) and Qwen3-Coder-30B-A3B at tight context |
| 48 GB | about 27B to 35B comfortably | Qwen3.6-27B, the 35B-A3B MoE, Devstral Small 2 with long context; Qwen3-Coder-Next (80B-A3B) only as a low-bit stretch |
| 64 GB | about 70B-class, or a 120B MoE tight | a 70B dense; gpt-oss-120b at low bit; Qwen3-Coder-Next comfortably |
| 128 GB | 120B MoE comfortably | gpt-oss-120b, Devstral 2 (123B); approaching Qwen3-235B-A22B at low bit |
| 256 to 512 GB (Mac Studio) | frontier MoE | DeepSeek V3/V4 and Kimi at low bit |

The lesson for the series: the machine sets the menu. Everything benchmarked here
lives inside the 48 GB row, and the cloud comparison in Part 4 is partly about what
you simply cannot run at home. The full per-model shortlist for 48 GB is in
`docs/MODELS-TO-BENCHMARK.md`.

### 6. How local inference works: two hardware worlds

- Apple Silicon unified memory: CPU and GPU share one physical memory pool. You can
  hold very large models in 48, 64, or more gigabytes on a single quiet machine, but
  memory bandwidth is lower than a dedicated GPU, so throughput is capped by it (and,
  as Part 1 showed, you fall off a cliff once you exceed it and swap).
- NVIDIA plus Windows or Linux: a discrete GPU with its own very fast VRAM. Far
  higher bandwidth per gigabyte, but capacity is smaller and pricier, the model must
  fit in VRAM or spill, and scaling means more cards.
- The trade in one line: Macs fit big models cheaply but read them slower; NVIDIA
  reads fast but fits less per dollar.

### 7. Zooming in on macOS: the runtimes

- llama.cpp: the foundational C++ engine with a Metal backend. Uses the GGUF format,
  supports the most architectures (new models often land here first), portable and
  production-stable.
- Ollama: a developer-experience layer over llama.cpp, model pulls like Docker
  images, an OpenAI-compatible server on a local port. Recent versions added an MLX
  backend on Apple Silicon.
- LM Studio: a graphical app for browsing, downloading, and running models with no
  command line, good for non-technical users.
- MLX and mlx-lm: Apple's own framework, built for unified memory, generally the
  fastest on Apple Silicon for smaller models and convergent with llama.cpp on large
  ones where bandwidth dominates. It uses its own format, not GGUF.
- vLLM (and vllm-mlx): production-grade serving optimized for many concurrent users.
- The specialized servers this project uses, DFlash (speculative decoding) and
  TurboQuant (MoE quantized serving), sit in the MLX ecosystem and trade the
  convenience of Ollama or LM Studio for control over the serving details we need to
  measure.
- A practical gotcha worth a sentence: GGUF and MLX formats are not interchangeable,
  so the runtime choice also constrains which model files you can use.

### 8. The punchline that sets up the series

Local performance is not a property of the model alone. It is the product of runtime
times model times quantization times hardware times the specific workload (prefill
versus decode, short versus long context). The clearest proof: when Ollama added an
MLX backend, the same model on the same Mac roughly doubled its prefill speed purely
from the runtime change. That is exactly why this series benchmarks the combination
rather than quoting a single model's number, and why Part 1 chose specialized MLX
servers over the friendlier general-purpose tools.

What it sets up: every later part (speed, energy, correctness, cloud comparison)
measures one slice of this runtime-times-model-times-hardware space. Part 0 hands the
reader the map. Net-new harness work: none. This is research and writing.

### Primary sources for Part 0

Architecture and model history (peer-reviewed papers and arXiv technical reports):

- Jacobs, Jordan, Nowlan, Hinton (1991), "Adaptive Mixtures of Local Experts,"
  Neural Computation 3(1):79-87. https://direct.mit.edu/neco/article/3/1/79/5560/Adaptive-Mixtures-of-Local-Experts
- Lepikhin et al. (2020), "GShard: Scaling Giant Models with Conditional Computation
  and Automatic Sharding." https://arxiv.org/abs/2006.16668
- Fedus, Zoph, Shazeer (2021), "Switch Transformers: Scaling to Trillion Parameter
  Models with Simple and Efficient Sparsity." https://arxiv.org/abs/2101.03961
- Jiang et al. (2024), "Mixtral of Experts." https://arxiv.org/abs/2401.04088
- DeepSeek-AI (2024), "DeepSeek-V2: A Strong, Economical, and Efficient
  Mixture-of-Experts Language Model." https://arxiv.org/abs/2405.04434
- DeepSeek-AI (2024), "DeepSeek-V3 Technical Report." https://arxiv.org/abs/2412.19437
- DeepSeek-AI (2025), "DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via
  Reinforcement Learning." https://arxiv.org/abs/2501.12948
- Qwen Team (2025), "Qwen3 Technical Report." https://arxiv.org/abs/2505.09388
- (Background survey) "A Survey on Mixture of Experts in Large Language Models."
  https://arxiv.org/abs/2407.06204

Quantization methods and providers:

- Lin et al. (2023), "AWQ: Activation-aware Weight Quantization for LLM Compression
  and Acceleration." https://arxiv.org/abs/2306.00978
- OpenAI (2025), "gpt-oss Model Card" (native MXFP4 4.25-bit MoE weights).
  https://arxiv.org/abs/2508.10925
- Unsloth, "Unsloth Dynamic 2.0 GGUFs" (layer-selective, calibration-based
  quantization). https://docs.unsloth.ai/basics/unsloth-dynamic-2.0-ggufs

Local inference runtimes (official project repositories and sites):

- llama.cpp: https://github.com/ggml-org/llama.cpp
- MLX (Apple machine learning research): https://github.com/ml-explore/mlx
- Ollama: https://github.com/ollama/ollama
- vLLM (UC Berkeley Sky Computing Lab): https://github.com/vllm-project/vllm
- LM Studio: https://lmstudio.ai/

To add at drafting time, each from its own model card or technical report: the
specific latest models named in passing (the most recent DeepSeek, GLM, Kimi, and
MiniMax releases). State them as fact only once a primary source is attached.

## Part 2: Does it actually write correct code?

Question: a fast, efficient model is useless if its code is wrong. How do the two
local models score, and how does quality trade off against the speed and energy
picture from Part 1?

Experiments:
- Run `canary`, then full HumanEval and MBPP, then the EvalPlus differential suites
  on both local models, scored in the existing sandbox.
- Plot the quality-versus-speed tradeoff: pass@1 against prefill tok/s, with marker
  size or color encoding energy. This turns three axes into one picture.

Already built: the suites (`canary`, `humaneval`, `mbpp`, `humaneval-plus`,
`mbpp-plus`), differential scoring, the sandbox, concurrency, warmup, power.

Net-new: the prefill-metric and scoring robustness fix for empty or reasoning-only
responses (the parked Part 1 gap). This matters more here, because a model that
"thinks" past its budget scores a false zero on correctness too, so it must be
resolved before quality numbers are trustworthy.

Decision it forces: how to handle reasoning models for a coding benchmark, measure
them with thinking on and a large budget, or disable thinking for a direct-answer
score. Pick one policy and apply it consistently.

## Part 3: How far can a 48 GB laptop stretch?

Question: where are the limits of the machine, in context length and in model
choice, before quality or usability falls apart?

Experiments:
- Extend the context ladder past 24k until each model hits its swap cliff, and
  record where that cliff is per model. The `--context-sizes` override already
  supports this.
- Add more local entries: different quantization levels of the same models, and at
  least one alternative family or size, to see how quant depth trades size against
  quality and speed.
- Map the usable envelope: maximum context before swap, and quality at each quant.

Already built: the per-model config, `--context-sizes`, the sequential sweep
automation, the swap-avoidance guard.

Net-new: a memory and swap sampler analogous to the power sampler, so swap pressure
becomes a recorded column rather than a screenshot. New config entries for the extra
models and quant levels, which require the corresponding server setup.

Decision it forces: which additional models and quantizations are worth the time,
and where to draw the "no longer usable" line (a TTFT or swap threshold).

## Part 4: Local versus the cloud

Question: the comparison everyone actually wants. How do these local setups stack up
against the hosted models people use today, on quality, latency, and cost?

Experiments:
- Run the same correctness suites against the cloud entries (GLM-4.6, Kimi K2, and a
  frontier baseline), which the harness already drives.
- Compare quality, end-to-end latency, and cost: real dollars per task for cloud
  against zero marginal dollars plus measured energy for local.
- Layer in the qualitative axes that do not reduce to a number: privacy, offline
  capability, rate limits, and control.

Already built: cloud providers, concurrency, the dated price table and cost
calculation, the reasoning-disable passthrough.

Net-new: a combined results view that merges quality, speed, energy, and cost into a
single comparison table or leaderboard, rather than separate per-axis outputs.

Decision it forces: which cloud models represent "what people use today," and the
pricing snapshot and date to cite, since prices move.

## Part 5: What you give up, what you gain

Question: synthesis. Given all of the above, when is running the agent locally the
right call, and when is it not?

Deliverable: a decision framework rather than a single winner. A Pareto view across
quality, speed, cost, energy, and privacy, plus plain recommendations by use case
(sensitive code and offline work, cost-sensitive high-volume work, frontier-quality
work). This is the article that justifies the whole series.

Net-new: mostly writing and synthesis on top of Parts 2 to 4. No major harness work.

## Part 6: The near future, a model on every device

Question: zoom out. Is local AI a niche for tinkerers, or the direction the whole
consumer industry is heading? As of 2026 it is clearly the latter, and this part
ends the series on where it is all going.

Thesis: within a short horizon, almost everyone will carry one or more models running
locally, on phones and computers, delivering generative AI without an internet
connection and with data that never leaves the device. The benchmark this series
builds is, in that light, not a hobby. It is the way to assess the models that are
about to ship to everyone.

The evidence, from 2026 vendor announcements (cite each from its official newsroom at
drafting time, since this is the most time-sensitive material in the series):

- Apple. At WWDC 2026 Apple introduced the third generation of its on-device
  Foundation Models, rebuilt from the ground up, now accepting image input, exposed
  to every developer through a single Swift API and a new Core AI layer, with an
  optional Private Cloud Compute model for heavier reasoning. The on-device model
  runs locally and privately by default.
- Google. Gemini Nano, built on the open Gemma family, is already on more than 140
  million Android devices. The next generation (Gemma 4, the base for Gemini Nano 4)
  is up to four times faster using up to 60 percent less battery, running on
  dedicated accelerators from Google, MediaTek, and Qualcomm, and Google frames the
  local-first approach explicitly as privacy-centric and cost-effective.
- Samsung. Galaxy AI runs a hybrid of on-device and cloud, keeps sensitive features
  on the phone, lets users disable cloud processing entirely, and adds new Knox
  hardware protections for personalized AI on the Galaxy S26.
- NVIDIA. With Microsoft, NVIDIA launched RTX Spark and DGX Spark, personal AI
  machines with 128 GB of unified memory able to run 120-billion-parameter models
  locally with million-token context, shipping in mainstream laptops and desktops.
  Notably, NVIDIA highlights the very Qwen 3.6 27B and 35B models this series
  benchmarks as reference local agentic models.

The through-line: every one of these answers the same questions this series asks,
which model, which runtime, how much memory, how fast is prefill, how much energy,
now being decided by the largest companies in the industry on behalf of billions of
devices. The convergence on unified-memory machines (Apple Silicon, NVIDIA's 128 GB
Spark) is the same architecture our 48 GB benchmark sits on, one tier down.

Why it matters: vendor announcements are marketing. What they do not provide is
independent, like-for-like measurement of whether a given local model is actually
fast enough, accurate enough, and efficient enough for real agentic coding on
hardware you own. That gap is exactly what this series fills. Part 6 closes the loop:
the rest of the industry is betting on local AI, and this series is how you check the
bet. Net-new harness work: none.

### Primary sources for Part 6

- Apple Newsroom (June 2026), "Apple aids app development with new intelligence
  frameworks and advanced tools." https://www.apple.com/newsroom/2026/06/apple-aids-app-development-with-new-intelligence-frameworks-and-advanced-tools/
- Apple Machine Learning Research, "Introducing the Third Generation of Apple's
  Foundation Models." https://machinelearning.apple.com/research/introducing-third-generation-of-apple-foundation-models
- Android Developers Blog, "Gemma 4: The new standard for local agentic intelligence
  on Android." https://android-developers.googleblog.com/2026/04/gemma-4-new-standard-for-local-agentic-intelligence.html
- Samsung Global Newsroom, "Your Privacy, Secured: How Galaxy AI Empowers You to
  Take Control of Your Data." https://news.samsung.com/global/your-privacy-secured-how-galaxy-ai-empowers-you-to-take-control-of-your-data
- NVIDIA Newsroom, "NVIDIA and Microsoft Reinvent Windows PCs for the Age of Personal
  AI." https://nvidianews.nvidia.com/news/nvidia-microsoft-windows-pcs-agents-rtx-spark

## Cross-cutting engineering prerequisites

These support multiple parts and are worth scheduling early:

- Prefill and scoring robustness for empty or reasoning-only responses (blocks Part 2).
- A consistent reasoning-model policy (blocks Parts 2 and 4).
- Repeated runs with reported variance, so small gaps carry confidence bands. Part 1
  leaned on large, obvious gaps; later parts will have closer calls.
- Memory and swap recording (supports Part 3).
- Contamination guards before quality claims go public: prefer EvalPlus over vanilla
  suites, add a LiveCodeBench slice, and a small private tripwire set. The first is
  built; the latter two are on the roadmap in the methodology doc.

## Status at a glance

| Capability | Status |
|---|---|
| Speed and energy measurement | Built (Part 1) |
| Correctness suites and sandbox scoring | Built |
| EvalPlus differential suites | Built, pending live dataset check |
| Cloud providers, cost table, concurrency | Built |
| Context-ladder override, sweep automation | Built |
| Empty / reasoning-only robustness fix | To build (Part 2) |
| Memory and swap recording | To build (Part 3) |
| Multi-run variance aggregation | To build (cross-cutting) |
| LiveCodeBench slice and private tripwire | To build (cross-cutting) |
| Combined quality-speed-energy-cost view | To build (Part 4) |

## Open decisions for you

1. Reasoning-model policy: thinking on with a large budget, or disabled for a direct
   answer. This choice shapes Parts 2 and 4.
2. Extra local models and quant levels for Part 3, and the usability cutoff.
3. Which cloud models and which pricing snapshot for Part 4.
4. Series length and cadence: four more parts as above, or a tighter merge (for
   example, combine correctness and limits into one).
5. Whether to keep the same accessible, layman's tone throughout, or shift to a more
   technical register for the middle parts.

## Suggested order

Reader-facing order: Part 0 (scene-setter, possibly split into 0a and 0b) opens, Part
1 (done) is the first measurement, then the measurement arc and the outlook closer:
Part 2 (correctness, plus the robustness fix) → Part 3 (limits and more models) →
Part 4 (local versus cloud) → Part 5 (synthesis) → Part 6 (the near-future outlook).
Part 0 and Part 6 are research-and-writing only; the measurement work lives in Parts
2 to 5, and the cross-cutting prerequisites slot in just before the part that first
needs them.
