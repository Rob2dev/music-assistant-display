"""Now-playing screen for a Music Assistant player (sendspin-cli) on a Raspberry Pi.

Draws straight onto a 480x320 SPI display (/dev/fb0, RGB565) and reads the XPT2046
touch panel through evdev. No token needed for the player screen:

- Controls and basic state come from the control socket of sendspin-cli (the audio player).
- Title/artist/album/progress/cover come from our own Sendspin client with the metadata,
  artwork, controller and visualizer roles. Music Assistant sees it as a DISPLAY player.
  If the player is playing but the display sits in a different group, the display sends
  'switch' until the titles match.
- Picking radio stations needs a Music Assistant API token (see README).

Usage:
  ma_display.py                 normal mode (systemd service)
  ma_display.py --demo          show a demo state on the display
  ma_display.py --demo --png X  write a demo frame to a PNG (no display needed)
  ma_display.py --calibrate     calibrate the touch panel (stop the service first, as root)
"""

# Everything defined in the submodules is re-exported here, so `import ma_display as m` keeps working for the
# tests and tools (m.Renderer, m.tr, m.SETTING_DEFS, ...).
from .config import *  # noqa: F401,F403
from .gfx import *  # noqa: F401,F403
from .strings import *  # noqa: F401,F403
from .i18n import *  # noqa: F401,F403
from .settings import *  # noqa: F401,F403
from .hardware import *  # noqa: F401,F403
from .player_socket import *  # noqa: F401,F403
from .sendspin import *  # noqa: F401,F403
from .music_assistant import *  # noqa: F401,F403
from .layout import *  # noqa: F401,F403
from .widgets import *  # noqa: F401,F403
from .renderer import *  # noqa: F401,F403
from .demo import *  # noqa: F401,F403
from .app import *  # noqa: F401,F403
from .calibrate import *  # noqa: F401,F403
