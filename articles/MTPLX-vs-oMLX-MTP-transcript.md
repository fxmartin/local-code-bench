# Run MLX LLMs 23% Faster on a Mac with MTP — Transcript

> **Source video:** [Run MLX LLMs 23% Faster on a Mac with MTP](https://www.youtube.com/watch?v=jU02xG69jXI)
> **Channel:** Joe Maddalone
> **Published:** 2026-06-15 · **Duration:** 7:24 (444 s)
> **Retrieved:** 2026-06-25 via `yt-dlp` English captions (auto-generated, lightly de-duplicated).

## Summary

Joe Maddalone benchmarks **MTPLX** — a native Multi-Token Prediction (MTP) inference
runtime for MLX on Apple Silicon — head-to-head against **oMLX** (his daily driver),
running the *same* Qwen 3.6 model through the **Pi coding agent**. MTP works by pairing a
**verifier** (the main model) with a lightweight **drafter** that predicts several tokens
ahead, which the verifier then accepts/rejects (the same speculative-decoding idea, but
MTPLX bakes draft + verify into a *single* model rather than running two). MTPLX ships a
DMG / Homebrew install, an OpenAI-compatible server (so it drops into the same APIs as
oMLX / llama.cpp), ready-to-go models (Gemma 4, Qwen 3.5 / 3.6) from its own Hugging Face
repo, and a per-machine auto-tuning step. Result: **~23% more tokens/sec** on generation
(peak 54.2 tok/s) plus apparently improved prefill/response time — with the caveat that he
was running two inference engines + OBS simultaneously, so it is an at-a-glance, not
controlled, comparison.

## Description (from YouTube)

Compares oMLX against MTPLX — an MLX inference engine with Multi-Token Prediction support
— benchmarked head-to-head on Apple Silicon. MTPLX produced almost 23% more tokens per
second than oMLX.

**Timestamps**
- 0:00 — What is MTP?
- 1:09 — What is MTPLX?
- 2:51 — Tuning for Your Machine
- 3:42 — MTP in oMLX
- 4:54 — Connect Pi Coding Agent to MTPLX
- 6:05 — MTPLX vs oMLX Results

**Related videos referenced:** oMLX (2× faster), Apfel (Apple built-in LLM), Zed IDE +
oMLX, Headroom (cut prompt processing 30%).

## Transcript

Today, we're going to take a look at MTPLX, which is a new and very interesting project. If you're
running uh MLX on your M-series Apple Silicon laptop, you are running the fastest possible version
of local AI you can. MTP is a newer technology. Uh it's multi-token prediction. And the way it works
in a nutshell is it basically ends up giving you two models. So, you grab an MTP model, uh but what
you get is two models. You get a verifier and a drafter. Verifier is like your main model, and the
drafter is this lightweight model that uh tries to predict multiple tokens, so multi-token
prediction. And uh and then the verifier decides which one of those is right. So, it is supposed to
result in faster speeds. Uh maybe in prefill, but definitely in token generation. I've had mixed
results with it. Um but that was before I got to this MTPLX. So, today we're going to take a look at
that. Um here's their website. There's a bunch of cool stuff. Uh but we'll jump over to how it
works. And this is what's different about it. Um so, when you install it, and we'll take a look at
that in a second, you can install these like ready-to-go models. And they're versions of models
you're probably already using. You got your Gemma 4, your Qwen 3.5 and 3.6, and all sorts of them.
Um And [clears throat] what they say here is So, this is a native MTP runtime, not a wrapper. And
what their claim is, and you know, you should read through their website, it's all very interesting,
good, cool stuff. Uh but what their claim is is that um this is even faster because not only do you
get the MLX and the MTP, they combine it into one model. They build it all in, and you don't need a
second model. That's at the end of the day, that is the gist of what they're getting at. So, what I
want to do today is um grab one of their models. Uh there's a process we can go through that'll tune
it for my machine, which is cool. Uh and then we're going to run that model in MTPLX and then we're
going to run the exact same model in uh OMLX, which I'm a big fan of and I it's kind of my daily
driver and we're just going to compare the results. Uh so, to get started with MTPLX, you can go to
their website, you can download uh the DMG and you can just drag it over to applications or you can
install it with Homebrew. Uh and at the end of all that uh and we'll go through the process in just
a second. At the end of all that, you end up with a server that looks a lot like OMLX or a llama. Uh
so, it's really easy to tap into the exact same APIs. So, once you download one of their models,
this really interesting process takes uh effect and that's this tuning for your Mac and you probably
can't hear it right now cuz I spent so much time blocking out background noise. Uh but, this guy is
running the out of my fans on my computer and the the the whole project comes with this fan related
module. Uh but, this is uh optimizing Gemma 4 right now. It's not that great, but whatever, it's
there. Uh now I'm optimizing Qwen 3.6, which is like my go-to. So, we're going to let that guy
optimize. We're going to see what kind of token speeds we get and the whole system is set up to uh
take advantage of the most optimal settings for your computer, which is really really dope. So, for
our little experiment, I'm going to jump over to OMLX and down here where I have directories for
various models, I'm going to add the very specific MTPLX model directory. I'm going to save those
settings and when I come back over here to models, I can see I've got these models that I've been
downloading uh from the MTPLX hugging face repository. I've jumped into our uh Pi configuration for
models and I updated the ID and model for this particular MTPLX model. And jump back to the
dashboard, put this up there, jump down here to this project I have called Vogon. What it is is not
important. I'm going to run Pi. I'm going to say review this project excluding node modules and
summarize. Now we've got uh a very detailed response uh and all of it at a glance. I'm I'm very
familiar with the project, of course. Looks great. So, let's try this with MTPLX. I'm going to jump
back into my Pi config for models and I'm just going to update this to MTPLX. The URL, the API is
all the same. We'll fire up MTPLX and there it is. Going to start that guy. We are on the exact same
model. Okay, we are going to jump back over to Pi. Now it's trying to switch uh to this Nvidia
model, uh but we are going to set it up for the exact same model. And you can see it is running on
MTPLX. So, we're going to give it the exact same prompt. Review this project excluding node modules
and summarize. I don't know if you can hear it, but the fan just fired up. And then it died off
pretty quick. So, it keeps track of our all-time max, and our highest so far is 54.2. So, our
summary is coming in. >> [clears throat and cough] >> And I will say this did not take
as long as the uh response from OMLX. So, here's the basic math. I got a 20 almost 23% increase in
token uh generation on the generation side. Um but I actually think the prefill side was uh
improved. Uh the response time was improved. And uh now I do have to mention like I'm running two
different inference engines on my machine at the same time. I'm running OBS. Uh I'm running a bunch
of things, so there's a lot of other factors to consider here, but at a glance, MTPLX is uh the
winner. I I can't deny it. So, I I I guess I'm going to be switching over to this and try it out as
a daily driver for a while and see how things go. Uh the other thing I would mention is um after
playing with it for a while, so OMLX uh I do have to keep a tight leash on context windows. I have
uh I don't think I'm seeing the same trouble with MTPLX right now. Uh this is super new. We'll see
how it all plays out, but at a glance, uh MTPLX just wins. It wins. I'm Joe Maddalone. I make short,
practical videos that respect your intelligence and your time. If that's your style, you'll like
this channel.
