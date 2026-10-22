"""The application: merges state, handles taps and runs the main loop.

App is put together from one mixin per concern; they all work on the same attributes (set in AppBase)."""
from .actions import Actions
from .base import AppBase
from .frames import Frames
from .loop import MainLoop
from .state import PlayerState, _blank, same_track
from .taps import Taps


class App(AppBase, PlayerState, Actions, Frames, Taps, MainLoop):
    pass


__all__ = ["_blank", "same_track", "App"]
