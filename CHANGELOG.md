# CHANGELOG

<!-- version list -->

## v0.65.0 (2026-07-17)

### Features

- **macos-native-app**: Swiftui shell hosting the dashboard (#18.1-001)
  ([`99b39a6`](https://github.com/fxmartin/local-code-bench/commit/99b39a64ab4946df6fbacb1d688ab4756cfbadbd))

- **ui-theme-revamp**: Evaluate and (maybe) vendor a (#16.3-001)
  ([`d8fb6de`](https://github.com/fxmartin/local-code-bench/commit/d8fb6dedcbffc9a9c0dcecd71a5dd52c545a217b))

### Testing

- **macos-native-app**: Swiftui shell hosting the dashboard (#18.1-001)
  ([`f0d3ebd`](https://github.com/fxmartin/local-code-bench/commit/f0d3ebdcc1831a9899f801dd3f81be8988f60e60))


## v0.64.0 (2026-07-17)

### Features

- **context-optimization-proxy**: Bare-vs-proxied a/b run (#13.3-001)
  ([`8848442`](https://github.com/fxmartin/local-code-bench/commit/8848442863a043501a3135b9889bdad0dbd46556))

- **ui-theme-revamp**: Designed light and dark modes with (#16.1-002)
  ([`fac0c8b`](https://github.com/fxmartin/local-code-bench/commit/fac0c8be374299756f7fe1f437a74979baada6b2))


## v0.63.0 (2026-07-17)

### Features

- **benchmark-comparison-dashboard**: Comparison-axis (#17.1-002)
  ([`772420c`](https://github.com/fxmartin/local-code-bench/commit/772420c29271596cbeb8f61a4aad0caa2aad1677))


## v0.62.0 (2026-07-17)

### Features

- **ui-theme-revamp**: Design-token layer and base styles (#16.1-001)
  ([`44f9763`](https://github.com/fxmartin/local-code-bench/commit/44f9763474f30bb4e1922a9898afc4296a12b9d9))


## v0.61.0 (2026-07-17)

### Features

- **context-optimization-proxy**: Start/stop a proxy (#13.2-001)
  ([`37c039b`](https://github.com/fxmartin/local-code-bench/commit/37c039b2f7cbdced069e8dcb8a4f1e0d0cda8c28))


## v0.60.0 (2026-07-17)

### Features

- **settings-management**: Audit hardcoded defaults and (#15.5-001)
  ([`9687e0c`](https://github.com/fxmartin/local-code-bench/commit/9687e0c6a2db497513d40b26e82e59e96f253f80))


## v0.59.0 (2026-07-17)

### Features

- **benchmark-comparison-dashboard**: Comparison (#17.1-001)
  ([`4132fa7`](https://github.com/fxmartin/local-code-bench/commit/4132fa749224bb3429c2a92df94ae9d2e5989080))


## v0.58.0 (2026-07-17)

### Documentation

- **stories**: Lock open decisions for epics 15, 17, 18
  ([`c457a24`](https://github.com/fxmartin/local-code-bench/commit/c457a24f506ba78057dc901baac54e1608d003bb))

### Features

- **context-optimization-proxy**: Optimizer config and (#13.1-001)
  ([`01bbf26`](https://github.com/fxmartin/local-code-bench/commit/01bbf268bf15a6a6b88f088c66351543d1f03f97))


## v0.57.1 (2026-07-17)

### Bug Fixes

- **inventory**: Exclude in-flight downloads from model scan
  ([`72767ae`](https://github.com/fxmartin/local-code-bench/commit/72767ae875cf183bf790b7e8e3d9ebde327b95ac))

### Testing

- Add coverage for fix #67
  ([`f19067e`](https://github.com/fxmartin/local-code-bench/commit/f19067e5e3d4fd051460e91c0d4842b0373e8d6c))


## v0.57.0 (2026-07-17)

### Bug Fixes

- **inventory**: Count hardlinked blobs once in directory size
  ([`11aff94`](https://github.com/fxmartin/local-code-bench/commit/11aff94f42e20d2c235482dae985b5327e8280be))

### Documentation

- **stories**: Add Epic-17 benchmark comparison dashboard with PDF export
  ([`1e558d4`](https://github.com/fxmartin/local-code-bench/commit/1e558d463e55d07082ce8c9480a6724163b63bbf))

- **stories**: Add Epic-18 macOS native app shell
  ([`663207c`](https://github.com/fxmartin/local-code-bench/commit/663207c20f1dcf08670bb644cc373cefe7da8e97))

- **stories**: Lock functional dark red for failures in Epic-16
  ([`9cbba87`](https://github.com/fxmartin/local-code-bench/commit/9cbba8704a8224e4dc057f0e8ce27a94f1eba4e4))

### Features

- **models**: Add ornith 9b/35b mlx + ollama benchmark matrix
  ([`637e4a0`](https://github.com/fxmartin/local-code-bench/commit/637e4a0274d1ad6eb6966c7ae9193fe25345bd1f))


## v0.56.0 (2026-07-17)

### Chores

- **configs**: Add Qwen3.6-35B-A3B quant-sensitivity pairs at q4 and q8
  ([`1b3950e`](https://github.com/fxmartin/local-code-bench/commit/1b3950e87f5919abf83562c64884f4dafd085490))

### Documentation

- **stories**: Add Epic-15 settings management (dashboard Settings tab)
  ([`66dd743`](https://github.com/fxmartin/local-code-bench/commit/66dd743ee925847bcee57a0cd9d58a250c39833c))

- **stories**: Add Epic-16 dashboard UI revamp (minimalist monochrome theme)
  ([`b9597c8`](https://github.com/fxmartin/local-code-bench/commit/b9597c84d6ee6e9ad7f49819eef698e419426dd2))

- **stories**: Lock dark blue as the Epic-16 accent color
  ([`b113b83`](https://github.com/fxmartin/local-code-bench/commit/b113b8307bcf236f17b617cf9bc9c129f4305e65))

- **stories**: Make nothing-hardcoded a first-class principle of Epic-15
  ([`22547eb`](https://github.com/fxmartin/local-code-bench/commit/22547eb7fe0d5c29799a76baa950bb31b8c3b34e))

### Features

- **dashboard**: Run tier moves in a background worker with live progress
  ([`b5a7dd1`](https://github.com/fxmartin/local-code-bench/commit/b5a7dd143a1a5e816932b367c170a32eb28095dc))


## v0.55.0 (2026-07-16)

### Features

- Add resumable Qwen benchmark sequence
  ([#75](https://github.com/fxmartin/local-code-bench/pull/75),
  [`e36b732`](https://github.com/fxmartin/local-code-bench/commit/e36b73260ad0593a636dfc5a79946fbd8878a570))


## v0.54.0 (2026-07-16)

### Features

- Add dashboard lifecycle commands
  ([`4efbd74`](https://github.com/fxmartin/local-code-bench/commit/4efbd74e4ac26357865a5fee097b362e43dbf510))


## v0.53.0 (2026-07-16)

### Features

- Record inference engine provenance
  ([`0c6e5b9`](https://github.com/fxmartin/local-code-bench/commit/0c6e5b9c1d96ac361e2e5f66b069878a6643fccd))


## v0.52.3 (2026-07-16)

### Bug Fixes

- Disable Ollama reasoning for endpoint benchmarks
  ([#71](https://github.com/fxmartin/local-code-bench/pull/71),
  [`24f32b7`](https://github.com/fxmartin/local-code-bench/commit/24f32b77c74540a7838c13143daaa7c7787a0a0a))

### Chores

- **configs**: Add five agentic-coding model candidates for M3 Max 48 GB
  ([`275a624`](https://github.com/fxmartin/local-code-bench/commit/275a624a7fb8ce11d6be632974563a6f29a8b1fa))

- **configs**: Pair each candidate model across mlx-lm and ollama
  ([`9355545`](https://github.com/fxmartin/local-code-bench/commit/93555452d295d98d18e476a4858d3514ed7351ce))


## v0.52.2 (2026-07-16)

### Bug Fixes

- Serve local shelf models and repair macOS sandbox
  ([#69](https://github.com/fxmartin/local-code-bench/pull/69),
  [`261b776`](https://github.com/fxmartin/local-code-bench/commit/261b77621f6d6f2ec7854e8a367f1ebfc6993547))


## v0.52.1 (2026-07-16)

### Bug Fixes

- Preserve benchmark evaluation fidelity
  ([#68](https://github.com/fxmartin/local-code-bench/pull/68),
  [`815cf58`](https://github.com/fxmartin/local-code-bench/commit/815cf584467d992dd415a699810682091c2c771a))


## v0.52.0 (2026-07-14)

### Features

- **suites**: Add calc-cli parser mini-app and bugfix-py debugging suites
  ([`59c3cc6`](https://github.com/fxmartin/local-code-bench/commit/59c3cc6d9fd57d1b79b7b00563dad5bf05127cd8))

- **suites**: Add jsondiff-cli mini-app suite and make custom suites runnable
  ([`32cbd90`](https://github.com/fxmartin/local-code-bench/commit/32cbd90131197bfd87fcc78757727fceacdbede1))

- **suites**: Add logclass-cli, a Python port of Task A as ladder rung 1
  ([`472e34f`](https://github.com/fxmartin/local-code-bench/commit/472e34f49e2d4606739d8c69eb46d5db5106bf4e))


## v0.51.0 (2026-07-14)

### Features

- **dashboard**: Show amber starting state while an engine boots
  ([`8ff45d7`](https://github.com/fxmartin/local-code-bench/commit/8ff45d788de338f28d2273c6a9df3f202fb06775))

- **inferencers**: Focus local engines on mlx-lm and ollama
  ([`7799a3f`](https://github.com/fxmartin/local-code-bench/commit/7799a3f0e0131c5b961045e183d6f11ad0f8cba5))

### Breaking Changes

- **inferencers**: Only mlx-lm and ollama remain as managed local inferencer engines; gguf/mlx store
  formats and the removed engines' model entries are no longer valid config.


## v0.50.1 (2026-07-02)

### Bug Fixes

- **dashboard**: Add chat inventory filtering and metrics
  ([`a553290`](https://github.com/fxmartin/local-code-bench/commit/a55329013f49d5b53df33df1aa992424508ffff2))


## v0.50.0 (2026-07-02)

### Features

- **inferencers**: Add omlx engine
  ([`1a528af`](https://github.com/fxmartin/local-code-bench/commit/1a528afc18cf87a6335206221153780a32a6606b))


## v0.49.0 (2026-07-02)

### Features

- **additional-agent-harnesses**: Qwen code agent harness (#14.3-001)
  ([`dbea0b8`](https://github.com/fxmartin/local-code-bench/commit/dbea0b8c9a9aa8a96dbaa79d4530f7f233f9b08d))

### Testing

- **additional-agent-harnesses**: Qwen code agent harness (#14.3-001)
  ([`7746747`](https://github.com/fxmartin/local-code-bench/commit/774674796c30ae36ba80de0c80a416c97dc42e19))


## v0.48.0 (2026-07-02)

### Features

- **additional-agent-harnesses**: Claude code agent (#14.2-001)
  ([`91c3b49`](https://github.com/fxmartin/local-code-bench/commit/91c3b4970facd3a667bec23150ea061fd7dd26cb))

### Testing

- **additional-agent-harnesses**: Claude code agent (#14.2-001)
  ([`9dc41e0`](https://github.com/fxmartin/local-code-bench/commit/9dc41e0710abb8db7ddbaf14d439d2e1e2aad3f1))


## v0.47.0 (2026-07-02)

### Features

- **additional-agent-harnesses**: Generalize the (#14.1-001)
  ([`b185234`](https://github.com/fxmartin/local-code-bench/commit/b185234c1d5169bc60a154e88243905f74274569))

### Testing

- **additional-agent-harnesses**: Generalize the (#14.1-001)
  ([`d9540e8`](https://github.com/fxmartin/local-code-bench/commit/d9540e88d01263ea016abe8cb3f10ad25453b9e0))


## v0.46.0 (2026-06-27)

### Features

- **tiered-model-storage**: Cli tier inventory and move (#12.6-001)
  ([`b89225c`](https://github.com/fxmartin/local-code-bench/commit/b89225c142c1b395c1de70ba5043bd4598467d5c))

- **tiered-model-storage**: Dashboard tier view and move (#12.6-002)
  ([`f3ca04a`](https://github.com/fxmartin/local-code-bench/commit/f3ca04aa092d2c8eae7df04685547eab39fc8bca))

### Testing

- **tiered-model-storage**: Cli tier inventory and move (#12.6-001)
  ([`1d1e941`](https://github.com/fxmartin/local-code-bench/commit/1d1e941291de43eab000f3491a0cac5d9128dbeb))

- **tiered-model-storage**: Dashboard tier view and move (#12.6-002)
  ([`1b56d05`](https://github.com/fxmartin/local-code-bench/commit/1b56d05ab1270f83f36a3f4485b0ea6752617029))


## v0.45.0 (2026-06-27)

### Features

- **tiered-model-storage**: Disk-budget + lru auto-tiering (#12.4-001)
  ([`1997ce2`](https://github.com/fxmartin/local-code-bench/commit/1997ce2e5313fda51207ec5f988b89056856957f))

### Testing

- **tiered-model-storage**: Disk-budget + lru auto-tiering (#12.4-001)
  ([`3d3b6c6`](https://github.com/fxmartin/local-code-bench/commit/3d3b6c62c95757ea91457c7837b11d70f9b44707))


## v0.44.0 (2026-06-27)

### Bug Fixes

- **tiered-model-storage**: Demote / evict a model from (#12.3-002)
  ([`eff227c`](https://github.com/fxmartin/local-code-bench/commit/eff227c977fc581090485efff1f89542c78556eb))

### Features

- **tiered-model-storage**: Demote / evict a model from (#12.3-002)
  ([`7a98a37`](https://github.com/fxmartin/local-code-bench/commit/7a98a37a19a0b1b617db41ccdd94c8e1af07bd09))

### Testing

- **tiered-model-storage**: Demote / evict a model from (#12.3-002)
  ([`a92f003`](https://github.com/fxmartin/local-code-bench/commit/a92f003c236af2c685b24c0203e3a3b7f3ae3304))

- **tiered-model-storage**: Demote / evict a model from (#12.3-002)
  ([`7267e8d`](https://github.com/fxmartin/local-code-bench/commit/7267e8d1d02bdc03b37adbece3342f629de930ed))

- **tiered-model-storage**: Promote a model from external (#12.3-001)
  ([`98194d5`](https://github.com/fxmartin/local-code-bench/commit/98194d5b6495acdad2eed1a702b0bd6a99c8ed3d))

- **tiered-model-storage**: Serve directly from external, (#12.5-001)
  ([`ac9607a`](https://github.com/fxmartin/local-code-bench/commit/ac9607afc154d8fe6351353f627d2243c6cddf41))


## v0.43.0 (2026-06-27)

### Features

- **tiered-model-storage**: Tier-aware inventory merging (#12.2-001)
  ([`3c0f184`](https://github.com/fxmartin/local-code-bench/commit/3c0f18438497ac113a8e812a9e3c42c7362020dd))

### Testing

- **tiered-model-storage**: Tier-aware inventory merging (#12.2-001)
  ([`9ae9df1`](https://github.com/fxmartin/local-code-bench/commit/9ae9df1be443b68999187b7ecd3fd706b5a024de))


## v0.42.0 (2026-06-27)

### Documentation

- Add Ornith-1.0-35B to the local agentic-coding shortlist
  ([`72bf29c`](https://github.com/fxmartin/local-code-bench/commit/72bf29c6b0107f1b329ff96304ab9bb00980c54e))

- **stories**: Add Epic-14 — additional coding-agent harnesses (Claude Code, Qwen Code)
  ([`960db63`](https://github.com/fxmartin/local-code-bench/commit/960db6394ec4877583d655e5b73bdff673fa34fc))

- **stories**: True-up epic progress and tighten epic-13 deps
  ([#49](https://github.com/fxmartin/local-code-bench/pull/49),
  [`424acb9`](https://github.com/fxmartin/local-code-bench/commit/424acb9627d98cd8922dbcb3c3db46658dde8210))

### Features

- **tiered-model-storage**: Configure the external (#12.1-001)
  ([`1e439fd`](https://github.com/fxmartin/local-code-bench/commit/1e439fdb191a948f3d9c95bd599bd6749cf4f2c0))

### Testing

- **tiered-model-storage**: Configure the external (#12.1-001)
  ([`5e1e551`](https://github.com/fxmartin/local-code-bench/commit/5e1e55130b3a41bd5e379fa534a9dedd0e4a2897))


## v0.41.0 (2026-06-27)

### Features

- **local-model-inventory**: Model inventory panel in the (#11.5-001)
  ([`b25d015`](https://github.com/fxmartin/local-code-bench/commit/b25d0153f9f666a94baaa27d903de0049ebd07c4))

### Testing

- **local-model-inventory**: Model inventory panel in the (#11.5-001)
  ([`fee498c`](https://github.com/fxmartin/local-code-bench/commit/fee498c8a2c645cfb634a3edb21b4c06c58c8ebf))


## v0.40.0 (2026-06-27)

### Features

- **local-model-inventory**: `bench inferencer models` (#11.4-001)
  ([#47](https://github.com/fxmartin/local-code-bench/pull/47),
  [`a02f6a3`](https://github.com/fxmartin/local-code-bench/commit/a02f6a3a1718488d66c968697f91f179583edce7))

### Testing

- **local-model-inventory**: `bench inferencer models` (#11.4-001)
  ([#47](https://github.com/fxmartin/local-code-bench/pull/47),
  [`a02f6a3`](https://github.com/fxmartin/local-code-bench/commit/a02f6a3a1718488d66c968697f91f179583edce7))


## v0.39.0 (2026-06-27)

### Features

- **local-model-inventory**: Disk footprint and (#11.6-001)
  ([`79dbd32`](https://github.com/fxmartin/local-code-bench/commit/79dbd32326bbd432567e28724e600b757fa4b67f))

### Testing

- **local-model-inventory**: Disk footprint and (#11.6-001)
  ([`120e701`](https://github.com/fxmartin/local-code-bench/commit/120e7017254f3d2b1e4b1385ddbf4bdfd747e793))


## v0.38.1 (2026-06-27)

### Bug Fixes

- **local-model-inventory**: Detect models usable by (#11.3-001)
  ([`f226b52`](https://github.com/fxmartin/local-code-bench/commit/f226b52819ef8a466d1b32c3e76cdfd345ee9060))

### Testing

- **local-model-inventory**: Detect models usable by (#11.3-001)
  ([`49a11da`](https://github.com/fxmartin/local-code-bench/commit/49a11da0b106ca419dc7ed14980b6077b5d0a130))


## v0.38.0 (2026-06-27)

### Features

- **local-model-inventory**: Normalized localmodel records (#11.2-001)
  ([`476f0fc`](https://github.com/fxmartin/local-code-bench/commit/476f0fc8df6c2f5d185af1437c818062dad70ae9))

### Testing

- **local-model-inventory**: Normalized localmodel records (#11.2-001)
  ([`7f7cde5`](https://github.com/fxmartin/local-code-bench/commit/7f7cde5ed1e82ef42ef4202c7f8aa41a7e49c106))

- **local-model-inventory**: Per-inferencer model-store (#11.1-001)
  ([`3e7e83d`](https://github.com/fxmartin/local-code-bench/commit/3e7e83dbec1756fceb4cf6902ee1f814dbb3df67))


## v0.37.0 (2026-06-26)

### Features

- **opencode-local-benchmark**: Sweep, repeat/variance, (#10.5-001)
  ([`d401499`](https://github.com/fxmartin/local-code-bench/commit/d401499dd3bd7731ce3d95e0027908455304c4bb))

### Testing

- **opencode-local-benchmark**: Sweep, repeat/variance, (#10.5-001)
  ([`c14e1b7`](https://github.com/fxmartin/local-code-bench/commit/c14e1b7b8ebb78e9d837f01633bd9e192a789d61))


## v0.36.0 (2026-06-26)

### Features

- **opencode-local-benchmark**: Comparable scorecard with (#10.4-001)
  ([`c301c53`](https://github.com/fxmartin/local-code-bench/commit/c301c5358de04dd1f7b3593ff801d098758979db))

### Testing

- **opencode-local-benchmark**: Comparable scorecard with (#10.4-001)
  ([`da403c2`](https://github.com/fxmartin/local-code-bench/commit/da403c2b998cf197b5e4280ce6b04726754202e6))


## v0.35.0 (2026-06-26)

### Features

- **opencode-local-benchmark**: Extract, compile, and (#10.2-001)
  ([`f9bafd9`](https://github.com/fxmartin/local-code-bench/commit/f9bafd93ee5ff93358870e64e3b7b5634a205124))

### Testing

- **opencode-local-benchmark**: Extract, compile, and (#10.2-001)
  ([`392e4fa`](https://github.com/fxmartin/local-code-bench/commit/392e4fa85b5be91c3a16f065d839145327e9e814))


## v0.34.0 (2026-06-26)

### Features

- **opencode-local-benchmark**: Structured classification (#10.3-001)
  ([`aed2739`](https://github.com/fxmartin/local-code-bench/commit/aed273970e3d089ecfd143d3a6f681ec4f3efdf8))

### Testing

- **opencode-local-benchmark**: Structured classification (#10.3-001)
  ([`ae5bd63`](https://github.com/fxmartin/local-code-bench/commit/ae5bd63012dd54552e4dca8731f157e37e3037b3))


## v0.33.0 (2026-06-26)

### Features

- **opencode-local-benchmark**: Fixed-prompt invocation (#10.1-001)
  ([`c590fea`](https://github.com/fxmartin/local-code-bench/commit/c590fea4ea7ad646524191186bf11d1dbe3ac1b4))

### Testing

- **opencode-local-benchmark**: Fixed-prompt invocation (#10.1-001)
  ([`eb0a3d7`](https://github.com/fxmartin/local-code-bench/commit/eb0a3d7835cef1ceed0d76d88a1b3195ebe53dda))


## v0.32.0 (2026-06-26)

### Features

- **inferencer-lifecycle**: Register mtplx (native mtp) (#08.7-001)
  ([`3946613`](https://github.com/fxmartin/local-code-bench/commit/39466131a24c7f36f0675f2665a43f1d983d6fd8))

### Testing

- **inferencer-lifecycle**: Register mtplx (native mtp) (#08.7-001)
  ([`0b92672`](https://github.com/fxmartin/local-code-bench/commit/0b92672cf50674d31d10ea6ac21c4d132d408af4))


## v0.31.0 (2026-06-26)

### Features

- **inferencer-lifecycle**: Exclusive start with (#08.3-001)
  ([`3d94ce0`](https://github.com/fxmartin/local-code-bench/commit/3d94ce0a2929413f379876c2d17274b0e95073ff))

### Testing

- **inferencer-lifecycle**: Exclusive start with (#08.3-001)
  ([`2e76360`](https://github.com/fxmartin/local-code-bench/commit/2e763602166ddc0438b0654266fd4b35a4f3ab60))


## v0.30.1 (2026-06-26)

### Bug Fixes

- **inferencers**: Align vllm-mlx entry with the standalone package
  ([#35](https://github.com/fxmartin/local-code-bench/pull/35),
  [`60bc240`](https://github.com/fxmartin/local-code-bench/commit/60bc24007d8f99180252a52171f12a0ae49c8856))

### Documentation

- Add per-engine inferencer installation guide for M3 Max
  ([#34](https://github.com/fxmartin/local-code-bench/pull/34),
  [`b7efc3a`](https://github.com/fxmartin/local-code-bench/commit/b7efc3a70b461e94b43b9d326788a3e31bb67740))


## v0.30.0 (2026-06-26)

### Features

- **unified-dashboard**: Chat panel ui (#09.7-002)
  ([`8a04cec`](https://github.com/fxmartin/local-code-bench/commit/8a04cecb843d49f4dd57946520a46efff5dff88e))

### Testing

- **unified-dashboard**: Chat panel ui (#09.7-002)
  ([`b8bf597`](https://github.com/fxmartin/local-code-bench/commit/b8bf59778abc695ec052401e0de19b73067ef473))


## v0.29.0 (2026-06-26)

### Features

- **unified-dashboard**: Cross-section flow and (#09.6-001)
  ([#32](https://github.com/fxmartin/local-code-bench/pull/32),
  [`dd8e6ed`](https://github.com/fxmartin/local-code-bench/commit/dd8e6ed5c845b63b3fbab0439b8044bcde3d2577))

### Testing

- **unified-dashboard**: Cross-section flow and (#09.6-001)
  ([#32](https://github.com/fxmartin/local-code-bench/pull/32),
  [`dd8e6ed`](https://github.com/fxmartin/local-code-bench/commit/dd8e6ed5c845b63b3fbab0439b8044bcde3d2577))


## v0.28.0 (2026-06-26)

### Features

- **unified-dashboard**: Live run progress and (#09.4-001)
  ([`84fa7dc`](https://github.com/fxmartin/local-code-bench/commit/84fa7dc319e9be9eb633e129c5fb1543f75c7fa3))

### Testing

- **unified-dashboard**: Live run progress and (#09.4-001)
  ([`dd793dd`](https://github.com/fxmartin/local-code-bench/commit/dd793dd240d2634bae522de5b5efc381a2fbfedc))


## v0.27.0 (2026-06-26)

### Features

- **unified-dashboard**: Streaming chat endpoint (#09.7-001)
  ([`2ee6f1f`](https://github.com/fxmartin/local-code-bench/commit/2ee6f1f43ef2512781e76466919170ebafb26a96))

### Testing

- **unified-dashboard**: Streaming chat endpoint (#09.7-001)
  ([`7faf647`](https://github.com/fxmartin/local-code-bench/commit/7faf6475057622cc1d316283de8ef50113e4afc8))


## v0.26.0 (2026-06-26)

### Features

- **unified-dashboard**: Compose a benchmark from model + (#09.2-001)
  ([`883a5bb`](https://github.com/fxmartin/local-code-bench/commit/883a5bbaa6205769317d0e12fddb87c8b7412a71))

### Testing

- **unified-dashboard**: Compose a benchmark from model + (#09.2-001)
  ([`32e6cf6`](https://github.com/fxmartin/local-code-bench/commit/32e6cf63547725896b5be79dd2d94d546c21f6d7))


## v0.25.0 (2026-06-26)

### Features

- **unified-dashboard**: Single-page unified dashboard (#09.1-001)
  ([`1eb699a`](https://github.com/fxmartin/local-code-bench/commit/1eb699a9fec19f764b2c4ed5ee70403f81450651))

### Testing

- **unified-dashboard**: Single-page unified dashboard (#09.1-001)
  ([`1689dcf`](https://github.com/fxmartin/local-code-bench/commit/1689dcf9e758e8d0d39efcb2c76510e96ddaf754))


## v0.24.0 (2026-06-26)

### Features

- **unified-dashboard**: Launch orchestration endpoint (#09.3-001)
  ([`5e642ec`](https://github.com/fxmartin/local-code-bench/commit/5e642ec6a06cf0f2a53333cc432eaccd377ef814))

### Testing

- **unified-dashboard**: Launch orchestration endpoint (#09.3-001)
  ([`8cf86c5`](https://github.com/fxmartin/local-code-bench/commit/8cf86c549208bd51b457647176232df3c2a9bdfc))


## v0.23.0 (2026-06-26)

### Features

- **unified-dashboard**: Available-suites catalog and (#09.5-001)
  ([`1c41ede`](https://github.com/fxmartin/local-code-bench/commit/1c41edecaa07916589109fa686a77e58c35a3148))

### Testing

- **unified-dashboard**: Available-suites catalog and (#09.5-001)
  ([`0190ca5`](https://github.com/fxmartin/local-code-bench/commit/0190ca59d818503a2aa5af16c54e461db7f31228))


## v0.22.0 (2026-06-25)

### Features

- **results-dashboard**: Leaderboard, run history, and (#07.4-001)
  ([`fc5c795`](https://github.com/fxmartin/local-code-bench/commit/fc5c79535a7ffbbf7791ea7d2e9cc1a1064f42d5))


## v0.21.0 (2026-06-25)

### Features

- **results-dashboard**: Basic tradeoff and sweep charts (#07.4-002)
  ([`627bf11`](https://github.com/fxmartin/local-code-bench/commit/627bf11854f4029b1639c56d2cebae755e705f3a))

### Testing

- **results-dashboard**: Basic tradeoff and sweep charts (#07.4-002)
  ([`127724c`](https://github.com/fxmartin/local-code-bench/commit/127724c7f2f3eee78e8046e8fba80ad66cc6bebc))


## v0.20.0 (2026-06-25)

### Features

- **results-dashboard**: Cli dashboard mode (#07.2-002)
  ([`67b5756`](https://github.com/fxmartin/local-code-bench/commit/67b57568aa74007ae47b18abf9065cbadccf05c5))

### Testing

- **results-dashboard**: Cli dashboard mode (#07.2-002)
  ([`a01c69f`](https://github.com/fxmartin/local-code-bench/commit/a01c69fd6dce2ed6c26eb729afbacf52f1a07d93))


## v0.19.0 (2026-06-25)

### Features

- **results-dashboard**: Static html dashboard generator (#07.2-001)
  ([`62ab4aa`](https://github.com/fxmartin/local-code-bench/commit/62ab4aa9642e2749fb8ed2918ab075156ae8110c))

### Testing

- **results-dashboard**: Static html dashboard generator (#07.2-001)
  ([`dd420e9`](https://github.com/fxmartin/local-code-bench/commit/dd420e9581453073a26ccedbb9e8b7ca07f91dd8))


## v0.18.0 (2026-06-25)

### Features

- **results-dashboard**: Live results http endpoints (#07.3-001)
  ([`1bc7c0d`](https://github.com/fxmartin/local-code-bench/commit/1bc7c0dc7c61dd818026f45681af6fb0d205cb2b))

### Testing

- **results-dashboard**: Live results http endpoints (#07.3-001)
  ([`17dbb56`](https://github.com/fxmartin/local-code-bench/commit/17dbb5623f4da5ad4e00b0f9f9f3813ca59f7a58))


## v0.17.0 (2026-06-25)

### Documentation

- Add MTPLX + Headroom research transcripts and proxy epic
  ([`2d508e1`](https://github.com/fxmartin/local-code-bench/commit/2d508e1c476245ae8686308d995980552a3a25c9))

- Add roadmap inputs, article drafts, references, and epic-12 story
  ([`827c70c`](https://github.com/fxmartin/local-code-bench/commit/827c70cfde51fb849d1e591fd7dc811685a9c0f8))

- Mark story 08.3-001 done, epic-08 complete
  ([`a390367`](https://github.com/fxmartin/local-code-bench/commit/a3903677b9b2ac7923e84600ebd5eedf0ab323c3))

### Features

- **results-dashboard**: Dashboard result aggregation model (#07.1-001)
  ([`bf1747e`](https://github.com/fxmartin/local-code-bench/commit/bf1747ea07a75f0984cdba4db44134da56010bab))

### Testing

- **results-dashboard**: Dashboard result aggregation model (#07.1-001)
  ([`e579690`](https://github.com/fxmartin/local-code-bench/commit/e57969090ac5af0301a8077df72db770392dcbfd))


## v0.16.0 (2026-06-25)

### Features

- **inferencer-lifecycle**: `bench inferencer` subcommands (#08.4-001)
  ([`0e24d1b`](https://github.com/fxmartin/local-code-bench/commit/0e24d1b0bcb7bea918716ecf977a28d63e3eaa9d))

### Testing

- **inferencer-lifecycle**: `bench inferencer` subcommands (#08.4-001)
  ([`dbf4320`](https://github.com/fxmartin/local-code-bench/commit/dbf43202cb01402312f521bd562e0eaf6ef4e1dc))


## v0.15.0 (2026-06-25)

### Features

- **inferencer-lifecycle**: Auto-start the inferencer a (#08.5-001)
  ([`57a7c13`](https://github.com/fxmartin/local-code-bench/commit/57a7c13b8bc50a875a5c92824116187f0a5d111f))

### Testing

- **inferencer-lifecycle**: Auto-start the inferencer a (#08.5-001)
  ([`f8ba2d2`](https://github.com/fxmartin/local-code-bench/commit/f8ba2d24f6183504bca93b1d6900986f8178444e))


## v0.14.0 (2026-06-25)

### Features

- **inferencer-lifecycle**: Localhost web dashboard for (#08.6-001)
  ([`49d63b3`](https://github.com/fxmartin/local-code-bench/commit/49d63b38cd1db9d84dcad88df38d88c825b1f4ed))

### Testing

- **inferencer-lifecycle**: Localhost web dashboard for (#08.6-001)
  ([`b9d1ea6`](https://github.com/fxmartin/local-code-bench/commit/b9d1ea672c379395c81a127d674361d57bda99d3))


## v0.13.0 (2026-06-25)

### Features

- **inferencer-lifecycle**: Start, stop, and status with (#08.2-001)
  ([`c0a58f3`](https://github.com/fxmartin/local-code-bench/commit/c0a58f37307e0165c3b6638636d634679d2bf879))

### Testing

- **inferencer-lifecycle**: Start, stop, and status with (#08.2-001)
  ([`7b91749`](https://github.com/fxmartin/local-code-bench/commit/7b9174932629ed51d12698f6da6c0b71cfb139dd))


## v0.12.0 (2026-06-25)

### Documentation

- Add Epic-08 inferencer lifecycle management stories
  ([#7](https://github.com/fxmartin/local-code-bench/pull/7),
  [`79405f1`](https://github.com/fxmartin/local-code-bench/commit/79405f112ac9b7a6b8036da242c0f11a4be32dcf))

- Add Epic-09 unified dashboard stories ([#8](https://github.com/fxmartin/local-code-bench/pull/8),
  [`ced7720`](https://github.com/fxmartin/local-code-bench/commit/ced77201f4d113f13ccc690943d489dbb3a571bc))

- Add Epic-10 (LLMBENCH-1) OpenCode local benchmark stories
  ([#9](https://github.com/fxmartin/local-code-bench/pull/9),
  [`ab4e325`](https://github.com/fxmartin/local-code-bench/commit/ab4e325f4cde094cfa147524f0a65be57f3fb048))

- Add Epic-11 local model inventory and sharing stories
  ([#10](https://github.com/fxmartin/local-code-bench/pull/10),
  [`791744b`](https://github.com/fxmartin/local-code-bench/commit/791744b79a69e8814051dc56bcfa76c78dd4eaf0))

- Add native chat panel feature to Epic-09 dashboard
  ([#11](https://github.com/fxmartin/local-code-bench/pull/11),
  [`98f9f54`](https://github.com/fxmartin/local-code-bench/commit/98f9f54c83b57c8623bf55a2ce84663f15a643a9))

### Features

- **inferencer-lifecycle**: Inferencer config and (#08.1-001)
  ([`9af30c8`](https://github.com/fxmartin/local-code-bench/commit/9af30c82b12b1777cc8b9a8f84ae20e3ba4cbbc7))

### Testing

- **inferencer-lifecycle**: Inferencer config and (#08.1-001)
  ([`8c85cf4`](https://github.com/fxmartin/local-code-bench/commit/8c85cf489a6418696504f3c5d1f054297617b9fa))


## v0.11.0 (2026-06-22)

### Bug Fixes

- Start sweeps from clean run files; surface power in sweep summary
  ([`cd3cea3`](https://github.com/fxmartin/local-code-bench/commit/cd3cea38dddcd68537b923d98e70843697207c6a))

### Documentation

- Record DFlash vs TurboQuant three-axis finding
  ([`f484f08`](https://github.com/fxmartin/local-code-bench/commit/f484f08531fb723e1ae2a96285c81c667b5a7244))

### Features

- Surface power/energy per model in the sweep summary
  ([`be44716`](https://github.com/fxmartin/local-code-bench/commit/be447160b9dceb1a7c3af00c3e6d29b33c8c8d90))


## v0.10.0 (2026-06-21)

### Bug Fixes

- Disable reasoning for cloud coding runs via extra_body passthrough
  ([`e75b8d8`](https://github.com/fxmartin/local-code-bench/commit/e75b8d84179db9d0c3eddb948c97b9861a58b1e3))

### Documentation

- Explain local backend comparison
  ([`15964d9`](https://github.com/fxmartin/local-code-bench/commit/15964d9003fbbdd39f7deace8f539a1bd7bdaf23))

### Features

- Add canary anchor subset, EvalPlus differential suites, and per-task --timeout
  ([`f20de33`](https://github.com/fxmartin/local-code-bench/commit/f20de339b82076e40858bec1ed625979d4c9c1f3))

- Add powermetrics power/energy recording via --power
  ([`a105a41`](https://github.com/fxmartin/local-code-bench/commit/a105a4114ea40cc67e0f24b3f30b16e3c9a39816))

- Make provider request timeout configurable via BENCH_PROVIDER_TIMEOUT_SECONDS
  ([`74f3b76`](https://github.com/fxmartin/local-code-bench/commit/74f3b767a5a94aefd0947ef52963e116fbd47b4e))

- Parallelize cloud endpoint runs and cap generation tokens
  ([`088d48d`](https://github.com/fxmartin/local-code-bench/commit/088d48d4f36a0b922b1048a8e4ea198595039f3c))

- Warm up before timing, tune model caps, gate local readiness on a real completion
  ([`b49cfa0`](https://github.com/fxmartin/local-code-bench/commit/b49cfa05aadef0d6c6707c551a1217c8c2063c42))

- Warm up models before timing and gate local readiness on a real completion
  ([`e123cc5`](https://github.com/fxmartin/local-code-bench/commit/e123cc5a421826dc5a7739bd75d56b1f579d754e))

### Testing

- Add coverage gate and validate local backends
  ([`bcdfdb6`](https://github.com/fxmartin/local-code-bench/commit/bcdfdb6eff8b43eac77178fbae237118c1223b48))


## v0.9.1 (2026-06-21)

### Bug Fixes

- Install uv inside release container
  ([`dab50ac`](https://github.com/fxmartin/local-code-bench/commit/dab50ac11d7a1df033f7727989af099f990f8d55))

- Keep uv lockfile synced in releases
  ([`ff3046a`](https://github.com/fxmartin/local-code-bench/commit/ff3046ac98e882fab841cedf1db5f826fb5be743))

### Chores

- Sync lockfile version
  ([`c1781d3`](https://github.com/fxmartin/local-code-bench/commit/c1781d3680b88c7733fa84f5d75bcc1953a6ced0))

### Documentation

- Track benchmark leaderboard
  ([`a6b3a24`](https://github.com/fxmartin/local-code-bench/commit/a6b3a24835e6c4b039d3073b87d66b34a39b35a9))


## v0.9.0 (2026-06-21)

### Chores

- Sync lockfile version
  ([`bf23203`](https://github.com/fxmartin/local-code-bench/commit/bf23203fa56abafc7b2085d3b0f63fbb86ab6e5e))

### Features

- Resume agent benchmark runs
  ([`d0078cc`](https://github.com/fxmartin/local-code-bench/commit/d0078cc5b8a26cdea1540be009ac2ad6106c5759))


## v0.8.0 (2026-06-21)

### Chores

- Sync lockfile version
  ([`2cc2431`](https://github.com/fxmartin/local-code-bench/commit/2cc243198ab89969b34174c250028822fbb89f4b))

### Features

- Capture codex agent token usage
  ([`bba55cd`](https://github.com/fxmartin/local-code-bench/commit/bba55cd5e7375cbd76575f20e8bcd473ce7fe64b))


## v0.7.1 (2026-06-21)

### Bug Fixes

- Improve mbpp prompts and leaderboard counts
  ([`72e6b66`](https://github.com/fxmartin/local-code-bench/commit/72e6b669e3c04ff900c41f50a39585121c12ec94))

### Chores

- Sync lockfile version
  ([`acff9d6`](https://github.com/fxmartin/local-code-bench/commit/acff9d63e307d166389e0881aa1ff0f7f8bf3f11))


## v0.7.0 (2026-06-21)

### Features

- Show per-task benchmark progress
  ([`de1c0a7`](https://github.com/fxmartin/local-code-bench/commit/de1c0a7188d95a88f334b4009a626c988c635de5))


## v0.6.1 (2026-06-21)

### Bug Fixes

- Quote numeric model revision
  ([`ac6a4b2`](https://github.com/fxmartin/local-code-bench/commit/ac6a4b2e01ae48f267996873681d34937631ec08))


## v0.6.0 (2026-06-21)

### Chores

- Sync lockfile version
  ([`8c6861b`](https://github.com/fxmartin/local-code-bench/commit/8c6861bdb3523207048b0f5bc5e5d1c9f5d024d6))

### Features

- Load API keys from dotenv
  ([`4b081f3`](https://github.com/fxmartin/local-code-bench/commit/4b081f333b70ad91c14432c9be1165bd79e75286))


## v0.5.3 (2026-06-20)

### Bug Fixes

- Harden benchmark implementation ([#6](https://github.com/fxmartin/local-code-bench/pull/6),
  [`8acbcb3`](https://github.com/fxmartin/local-code-bench/commit/8acbcb399a0292151506fa6dcf370022cb9c8838))

### Chores

- Sync lockfile version ([#5](https://github.com/fxmartin/local-code-bench/pull/5),
  [`9046fd1`](https://github.com/fxmartin/local-code-bench/commit/9046fd1a89933264c7c5241f14539664fb597223))


## v0.5.2 (2026-06-20)

### Bug Fixes

- Derive runtime version from package metadata
  ([#4](https://github.com/fxmartin/local-code-bench/pull/4),
  [`f73e444`](https://github.com/fxmartin/local-code-bench/commit/f73e444f633be44a253f3551c6ef7223d19176ad))


## v0.5.1 (2026-06-20)

### Bug Fixes

- Align runtime version with release ([#3](https://github.com/fxmartin/local-code-bench/pull/3),
  [`8f26470`](https://github.com/fxmartin/local-code-bench/commit/8f264709abe6a98eeb0410b95882b1cad17c8f18))


## v0.5.0 (2026-06-20)

### Features

- Complete remaining benchmark epics ([#2](https://github.com/fxmartin/local-code-bench/pull/2),
  [`c856a47`](https://github.com/fxmartin/local-code-bench/commit/c856a47f6d6544fdae5447fc837531a7fb91c96a))


## v0.4.0 (2026-06-20)

### Features

- Complete epic 1 endpoint foundation ([#1](https://github.com/fxmartin/local-code-bench/pull/1),
  [`90d94a0`](https://github.com/fxmartin/local-code-bench/commit/90d94a06211b0a7bfeb0cd70c2552a2de611ae35))


## v0.3.0 (2026-06-20)

### Features

- Make benchmark codex-ready
  ([`086218c`](https://github.com/fxmartin/local-code-bench/commit/086218c88a5f7b4ea18552530925ab2ff5da3f17))


## v0.2.0 (2026-06-20)

### Features

- Scaffold uv benchmark project
  ([`4491594`](https://github.com/fxmartin/local-claude-code/commit/4491594491a9a191c539423614ba8ae2a500474e))


## v0.1.0 (2026-06-20)

- Initial Release
