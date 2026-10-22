"""Where things are on the 480x320 screen: coordinates and the tap areas that match them."""

from .gfx import font
from .i18n import tr
from .settings import LANGUAGE_CODES


# Bottom row: playback buttons centred, a small speaker icon on the right that opens the
# volume popup. Values: (x0, x1) of the tap area.
BTN_Y0, BTN_H = 232, 88


ROW_CY = BTN_Y0 + BTN_H // 2 - 4


MAIN_BUTTONS = {"stations": (0, 80), "prev": (100, 190), "play": (190, 290), "next": (290, 380),
                "volume": (400, 480)}


# Volume popup: panel and buttons as (x0, y0, x1, y1).
POPUP = (56, 44, 424, 268)


POPUP_BUTTONS = {"vol-": (80, 160, 180, 244), "mute": (190, 160, 290, 244), "vol+": (300, 160, 400, 244)}


POPUP_SECS = 4


# Floating glass bar behind the buttons
GLASS_BAR = (12, 238, 468, 308)


# Volume bar in the popup (x0, x1, y of the bar) and the larger tap area around it.
POPUP_BAR = (80, 400, 114)


POPUP_BAR_HIT = (64, 92, 416, 146)


# Power menu: long-press the clock (top-right corner of the player screen) to open it.
POWER_ZONE = (400, 0, 480, 40)


POWER_PANEL = (56, 44, 424, 268)


POWER_ROWS = {"settings": (80, 80, 400, 120), "restart": (80, 126, 400, 166), "shutdown": (80, 172, 400, 212),
              "cancel": (80, 218, 400, 258)}


POWER_SECS = 20  # the menu closes by itself after this long


def popup_bar_value(x, y):
    """Volume (0-100) if the bar was tapped, else None."""
    hx0, hy0, hx1, hy1 = POPUP_BAR_HIT
    if hx0 <= x < hx1 and hy0 <= y < hy1:
        bx0, bx1, _ = POPUP_BAR
        return round(min(1.0, max(0.0, (x - bx0) / (bx1 - bx0))) * 100)
    return None


def button_at(x, y):
    if y >= BTN_Y0 - 8:
        for name, (x0, x1) in MAIN_BUTTONS.items():
            if x0 <= x < x1:
                return name
    return None


# Station screen: 3x2 tiles, a header row with tabs, paging and close.
ST_COLS, ST_ROWS = 3, 2


ST_TILE_W, ST_TILE_H, ST_GAP = 144, 118, 8


ST_X0, ST_Y0 = 16, 56


ST_TAB_NAMES = ("fav", "all", "rooms")


ST_HEADER = {"left": (336, 368), "right": (404, 436), "close": (436, 480)}


STATIONS_SECS = 30


# Rooms tab: the players that can be in this player's group, one row each (a switch on the right).
ROOM_ROWS, ROOM_Y0, ROOM_ROW_H, ROOM_PITCH = 4, 100, 44, 48


ROOM_CONFIRM = (56, 62, 424, 258)


ROOM_CONFIRM_BUTTONS = {"yes": (80, 176, 236, 232), "no": (244, 176, 400, 232)}


# Settings screen: two tabs, then six rows (five settings and a reset action) or the system info.
SET_TAB_NAMES = ("general", "display", "info")


SET_Y0, SET_ROW_H, SET_ROW_PITCH = 56, 40, 43


SETTINGS_SECS = 60  # the screen closes by itself after this long without a tap


def tab_ranges(names):
    """Tab areas laid out by the width of their translated labels (12 px padding on both sides), so a long
    label in any language neither overlaps its neighbour nor leaves a tap area that does not match the text."""
    f, out, x = font(16, True), {}, 12
    for name in names:
        width = int(f.getlength(tr("tab_" + name))) + 24
        out[name] = (x, x + width)
        x += width
    return out


def stations_hit(x, y):
    """What was tapped on the station screen: ('tab', name) / ('left',) / ('tile', index) / ..."""
    if y < ST_Y0 - 4:
        for name, (x0, x1) in tab_ranges(ST_TAB_NAMES).items():
            if x0 <= x < x1:
                return ("tab", name)
        for name, (x0, x1) in ST_HEADER.items():
            if x0 <= x < x1:
                return (name,)
        return None
    c = (x - ST_X0) // (ST_TILE_W + ST_GAP)
    r = (y - ST_Y0) // (ST_TILE_H + ST_GAP)
    if 0 <= c < ST_COLS and 0 <= r < ST_ROWS:
        return ("tile", int(r * ST_COLS + c))
    return None


def rooms_row_at(x, y):
    """Row (0..ROOM_ROWS-1) of the rooms list at this point, or None."""
    if 16 <= x < 464 and ROOM_Y0 <= y < ROOM_Y0 + ROOM_ROWS * ROOM_PITCH and (y - ROOM_Y0) % ROOM_PITCH < ROOM_ROW_H:
        return (y - ROOM_Y0) // ROOM_PITCH
    return None


def confirm_button_at(x, y):
    for name, (x0, y0, x1, y1) in ROOM_CONFIRM_BUTTONS.items():
        if x0 <= x < x1 and y0 <= y < y1:
            return name
    return None


def settings_hit(x, y, n_rows):
    """What was tapped on the settings screen: ('tab', name) / ('close',) / ('row', index, part)."""
    if y < SET_Y0 - 4:
        for name, (x0, x1) in tab_ranges(SET_TAB_NAMES).items():
            if x0 <= x < x1:
                return ("tab", name)
        return ("close",) if x >= 428 else None
    i, within = divmod(y - SET_Y0, SET_ROW_PITCH)
    if not (0 <= i < n_rows and within < SET_ROW_H and 16 <= x < 464):
        return None
    part = "minus" if 288 <= x < 328 else "plus" if 416 <= x < 456 else "row"
    return ("row", i, part)


def picker_hit(x, y):
    """The language picker: a 2-column grid. Returns an index into LANGUAGE_CODES, 'close' or None."""
    if y < SET_Y0 - 4:
        return "close" if x >= 428 else None
    row, within = divmod(y - SET_Y0, 50)
    col = 0 if 16 <= x < 236 else 1 if 244 <= x < 464 else None
    if col is None or within >= 44 or row > 4 or row * 2 + col >= len(LANGUAGE_CODES):
        return None
    return row * 2 + col


def popup_button_at(x, y):
    for name, (x0, y0, x1, y1) in POPUP_BUTTONS.items():
        if x0 <= x < x1 and y0 <= y < y1:
            return name
    return None


def power_button_at(x, y):
    for name, (x0, y0, x1, y1) in POWER_ROWS.items():
        if x0 <= x < x1 and y0 <= y < y1:
            return name
    return None


__all__ = [
    "BTN_Y0", "BTN_H", "ROW_CY", "MAIN_BUTTONS", "POPUP", "POPUP_BUTTONS", "POPUP_SECS", "GLASS_BAR",
    "POPUP_BAR", "POPUP_BAR_HIT", "POWER_ZONE", "POWER_PANEL", "POWER_ROWS", "POWER_SECS", "popup_bar_value",
    "button_at", "ST_COLS", "ST_ROWS", "ST_TILE_W", "ST_TILE_H", "ST_GAP", "ST_X0", "ST_Y0", "ST_TAB_NAMES",
    "ST_HEADER", "STATIONS_SECS", "ROOM_ROWS", "ROOM_Y0", "ROOM_ROW_H", "ROOM_PITCH", "ROOM_CONFIRM",
    "ROOM_CONFIRM_BUTTONS", "SET_TAB_NAMES", "SET_Y0", "SET_ROW_H", "SET_ROW_PITCH", "SETTINGS_SECS",
    "tab_ranges", "stations_hit", "rooms_row_at", "confirm_button_at", "settings_hit", "picker_hit",
    "popup_button_at", "power_button_at",
]
