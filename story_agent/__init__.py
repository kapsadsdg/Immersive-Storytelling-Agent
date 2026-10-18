"""Immersive storytelling agent.

A theme goes in; the World Architect builds a fixed world and cast, and the
Plot Planner shapes the chapters. Each player turn, a deterministic Scheduler
calls only the agents the turn needs from eight: World Architect, Intent
Interpreter, Plot Planner, Character Agent, Scene Director, Narrator,
Chronicler and Narrative Critic. Five deterministic modules (Scheduler, Policy
Guard, Pacing Controller, State Manager, Context Builder) keep the loop on track.
"""

__version__ = "0.2.0"
