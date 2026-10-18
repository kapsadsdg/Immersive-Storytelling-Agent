"""The eight logical agents. Each wraps one kind of LLM call with its own prompt and schema.

Which of them run on a given turn is decided by the Scheduler (story_agent/scheduler.py).
"""

from .character_agent import CharacterAgent
from .chronicler import Chronicler
from .intent_interpreter import IntentInterpreter
from .narrative_critic import NarrativeCritic
from .narrator import Narrator
from .plot_planner import PlotPlanner
from .scene_director import SceneDirector
from .world_architect import WorldArchitect

__all__ = [
    "CharacterAgent", "Chronicler", "IntentInterpreter", "NarrativeCritic", "Narrator", "PlotPlanner",
    "SceneDirector", "WorldArchitect",
]
