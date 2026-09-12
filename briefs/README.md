# How to use these briefs

One brief per agent. Paste, in this order, into a fresh agent session:

1. the whole of `AGENTS.md`
2. the whole of `swarm/config.py`
3. that agent's brief, and nothing else

Do not paste the design document. Do not paste another agent's brief.

Order of work: run **B in the foreground yourself** with the strongest model and
read every line as it lands — it is the only module where a plausible-looking
wrong answer costs you the weekend. Run A, C, D, E in parallel in the background.

Anything still marked `TODO(design)` in `config.py` must be filled in before the
agent that needs it starts. An agent that hits a `TODO` is instructed to stop.
