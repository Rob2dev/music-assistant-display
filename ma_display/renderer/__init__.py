"""Draws every screen.

Renderer is put together from a base (background, glass panels, overlay) and one mixin per group of screens."""
from .base import RendererBase
from .plain import PlainScreens
from .player import PlayerScreen
from .popups import PopupScreens
from .settings_screens import SettingsScreens
from .stations import StationScreens


class Renderer(RendererBase, PlayerScreen, StationScreens, PopupScreens, SettingsScreens, PlainScreens):
    pass


__all__ = ["Renderer"]
