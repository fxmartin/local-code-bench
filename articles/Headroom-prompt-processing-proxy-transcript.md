# Cut Local LLM Prompt Processing 30% on a Mac with Headroom — Transcript

> **Source video:** [Cut Local LLM Prompt Processing 30% on a Mac with Headroom](https://www.youtube.com/watch?v=j6U_kKiMXgo)
> **Channel:** Joe Maddalone
> **Published:** 2026-06-23 · **Duration:** 9:06 (546 s)
> **Retrieved:** 2026-06-25 via `yt-dlp` English captions (auto-generated, lightly de-duplicated).
> **Headroom docs:** https://headroom-docs.vercel.app/docs

## Summary

Joe Maddalone tests **Headroom** — an **OpenAI-compatible proxy** that sits *between* the
coding agent and the local model and **compresses context** to cut the number of tokens
the inference engine has to prefill. It is **not an inference engine**; it forwards
requests to whatever OpenAI-compatible backend you point it at (here oMLX on `:8000`).

The motivation is explicitly this project's thesis: on a Mac, **prefill (prompt
processing) is the bottleneck**. Headroom doesn't make prefill *faster per token* — it
makes the model prefill *fewer tokens*, so the same task completes quicker.

Method: run `headroom proxy --port 8787` pointing at the OpenAI URL, repoint the Pi coding
agent's `models.json` at `:8787`. First pass uses `--no-optimize` to capture a **baseline**;
second pass enables optimization. Headroom's dashboard shows before/after token counts per
session. A **context router** picks among ~4 strategies ("summary crusher", "JSON crusher",
"smart crusher", excluding tool calls). A `--learn` flag (caching to tune per-machine) and
other flags were left off ("bare-bones").

Results (uncontrolled, single run):
- **Short coding task:** ~133K → ~106K tokens ≈ **20%** saved (~26.9K tokens).
- **Long coding task:** ~800K → ~568K tokens ≈ **30%** saved (~231K tokens).

Caveat the author states: context compression is lossy by nature — he ran it "bare-bones"
and didn't validate that correctness/agent behavior was unaffected.

## Description (from YouTube)

Tests Headroom — an OpenAI-compatible proxy — as a token optimizer for oMLX on Apple
Silicon. Result: nearly 30% fewer tokens processed on long coding sessions → faster
inference and less wasted compute. Headroom sits between agent and local model, compressing
context "without losing important information." Mentions Rust-based "token killer" as a
popular alternative.

**Timestamps**
- 0:00 Why token optimization matters
- 1:29 Install and Run Headroom
- 2:23 Connect Our Agent
- 3:26 Baseline without optimization
- 4:18 Baseline results
- 4:50 Enabling optimization
- 5:20 How Headroom works
- 6:15 20% token savings
- 7:06 Long-context test
- 7:56 Nearly 30% reduction
- 8:30 Conclusion

## Transcript

So, as someone who predominantly runs all of my AI stuff locally, I don't ever really think that
much about token optimization. But, we are all very aware of the fact that the entire industry is
kind of being taken by surprise right now as context windows get bigger and everything is gigantic
and automation and these things are long-running processes. Everyone's kind of got sticker shock by
the per token pricing or whatever it is, the amount of tokens we're using. And that has given rise
to a lot of really interesting token optimization software. Rust token killer is very popular. And
then this one is called headroom, and that's what we're going to take a look at here. And the reason
I'm interested in this is not about saving money, which maybe it would in electricity, but one of
the bottlenecks on a Mac running LLMs, we all know is prefill. So, that's your prompt processing.
That's processing the input that's going in. So, what we can do is we can take advantage of the fact
that all of this token optimization stuff is available to us and use that to not necessarily improve
the speed of our prefill, but to reduce the amount of context that the inference engine has to
process. So, this is headroom. We're going to install it. But, what we're specifically going to look
at is this proxy server. Okay, so this is my terminal. This is the installation of headroom AI
proxy. And I'll run that and everything's installed. Then we're going to load that up by saying
headroom proxy a port, which is going to be 87 87 87. That's where headroom is going to live. Then
we're going to pass in our OpenAI API URL. So, this proxy acts as a middleman. So, everything that
goes to 8787 is going to get forwarded on to 8000. Now, before I start this, I'm actually going to
add one more flag, and that's going to be no optimize. Uh so, what I'm doing on this first run is
I'm saying proxy all the information, all the data, all the tokens through this port, um but don't
do any optimization. Just let it run through, and this is going to give us our baseline. Uh and then
we'll add the optimization, and we'll see what the differences are. So, with that up and running,
I'm jumping into my PyCodingAgent's uh models.json, and where I have uh this MTPLX uh server
running, I'm going to change that to 8787. Now, just to be clear, I'm actually running OMLX at at uh
8000, uh so in case that makes a difference. Uh so, now everything that it tries to request from
this model is going to go through that port. So, I'm going to save that, and what I'm going to do is
I'm going to ask PyCodingAgent to uh this is an index file on a project I have with a bunch of types
defined in this TypeScript file. Uh I'm going to tell it to uh extract all those to their own file,
um and then re-import them into this file, and then ensure that uh tests and linting are still
working. Uh and that should technically create a lot of tool outputs and file reads and things of
that nature that are going to really uh increase the size of the uh data that it needs to prefill.
So, I'm going to drop in my prompt right here. It's exactly what I said, uh extract the types to
their own file, re-import them to this file, and ensure that the linting and tests still pass. We're
going to run that guy. I'm going to jump over here to 8787 dashboard, and here we can get a real-
time view, or sort of real-time view, of the data that's coming in and uh how it's being optimized.
Now, in our case, there's going to be no optimizations, so this before and after are always going to
be the same, and we're just going to let this process uh continue until it is done. And we'll see it
reading a lot of files, running linting, making mistakes, all that good stuff. Uh so, this context
uh should should grow pretty significantly. >> [music] >> Cool, so that's done. Let me
reload this and see where we landed. 153.6 thousand uh tokens uh across 13 requests. Now, what I'm
going to do is come in here. I'm going to undo its work. I'm going to delete that types. And I'm
going to clear out any changes. So, now we have a clean repository with nothing to commit. We are
going to stop headroom. We're going to restart it without the new optimize flag. So, now we're
actually going to do optimization. Load that up. It's running. We'll jump back over here. Run pi.
Get our dashboard up, and it's all clean. This is on a per session basis. There is a historical tab,
but all I'm concerned about right now is the per session. So, I'm going to drop in the exact same
prompt. Uh and uh we'll see what we get. So, we're starting to see small optimizations. If we come
down here to um the actual commands that are being sent through, I'm going to zoom this out a bit.
And we can see that in the beginning, it's not really doing much optimization, but it will start to
do more and more. The way it ends up working is it has like this context router that says, "Oh, what
what kind of data is coming in, and what are the best ways for me to optimize?" And I think it's got
four different ones. One's called like summary crusher, JSON crusher. This one is excluding tool
calls. Um oh yeah, so smart crusher is the one there. So this one did both of those. Uh it'll show
you exactly which optimizations it's using and I'm using this pretty bare-bones. This is not uh
fully optimized. I'm not enabling all the different flags and really tweaking it. Um but what we can
see So our before and after right now is 133 versus 106. And according to this, that's 20% savings.
And our guy is done. So we shaved off 26.9 thousand uh tokens, I guess. Uh and that's pretty
significant. That definitely makes our uh prefill faster. Not It doesn't actually improve the
prefill speed. It just the prefill has to do less. Now this was a pretty short-running process. What
I'm going to do is undo those changes once again. I'm going to start a new session with Headroom.
I'm going to load up Pi. And I'm going to drop in a much longer uh prompt. And you know, it's
actually getting cut off. So I'll just show you what the prompt is that I pasted in. It's the exact
same thing that we had before, but then I wanted to go through and extract this function into its
own file, ensure that the linting and tests will pass, then do the same thing with the next function
and the next function. So this build user prompts, extract fields, and this function that is
actually called some models just won't listen. Um I'm not going to make you sit through all of this.
Uh but this should be a very long-running process that takes a while and has a lot of context. Uh so
I will uh run this and then we'll come back and see what type of savings Headroom says we got. Cool.
So, our task is done and if we look at this, we had almost 800,000 dropped down to 568. We saved
231, which is almost 30%. Now, there's a whole bunch of tweaking I have not done here. Very
specifically, this learn flag, which will uh use some caching techniques to learn how to best
optimize on your system, so you you can definitely get more out of this. So, again, I'm just doing a
a really bare-bones look at this thing. But, prefill being the bottleneck with uh running local AI
on your Mac, uh is 100% without a doubt improved because we're simply sending less to be prefilled.
Uh so, so even though we don't care about the cost of tokens, uh prefilling less tokens is going to
improve our performance. I'm Joe Maddalone. I make short, practical videos that respect your
intelligence and your time. If that's your style, you'll like this channel.
