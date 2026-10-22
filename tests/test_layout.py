"""Hit testing: a tap has to land on exactly what is drawn there. These areas are shared with the renderer."""
import unittest

from helpers import Base, m, needs_fonts


def row_y(i):
    return m.SET_Y0 + i * m.SET_ROW_PITCH + 18


@needs_fonts
class Tabs(Base):
    def test_tab_areas_are_contiguous_in_every_language(self):
        for lang in m.STRINGS:
            m.set_language(lang)
            for names in (m.ST_TAB_NAMES, m.SET_TAB_NAMES):
                areas = list(m.tab_ranges(names).values())
                self.assertEqual(areas[0][0], 12, lang)
                self.assertTrue(all(a[1] == b[0] for a, b in zip(areas, areas[1:])), lang)

    def test_a_tap_in_the_middle_of_a_tab_hits_that_tab(self):
        for lang in m.STRINGS:
            m.set_language(lang)
            for name, (x0, x1) in m.tab_ranges(m.ST_TAB_NAMES).items():
                self.assertEqual(m.stations_hit((x0 + x1) // 2, 26), ("tab", name), (lang, name))
            for name, (x0, x1) in m.tab_ranges(m.SET_TAB_NAMES).items():
                self.assertEqual(m.settings_hit((x0 + x1) // 2, 26, 3), ("tab", name), (lang, name))


@needs_fonts
class StationScreen(Base):
    def test_header_buttons(self):
        for name, (x0, x1) in m.ST_HEADER.items():
            self.assertEqual(m.stations_hit((x0 + x1) // 2, 26), (name,))

    def test_tiles(self):
        seen = set()
        for r in range(m.ST_ROWS):
            for c in range(m.ST_COLS):
                x = m.ST_X0 + c * (m.ST_TILE_W + m.ST_GAP) + 10
                y = m.ST_Y0 + r * (m.ST_TILE_H + m.ST_GAP) + 10
                seen.add(m.stations_hit(x, y))
        self.assertEqual(seen, {("tile", i) for i in range(m.ST_COLS * m.ST_ROWS)})


@needs_fonts
class SettingsScreen(Base):
    def test_rows_and_their_parts(self):
        self.assertEqual(m.settings_hit(450, 26, 3), ("close",))
        self.assertEqual(m.settings_hit(100, row_y(0), 3), ("row", 0, "row"))
        self.assertEqual(m.settings_hit(300, row_y(4), 5), ("row", 4, "minus"))
        self.assertEqual(m.settings_hit(430, row_y(4), 5), ("row", 4, "plus"))
        self.assertEqual(m.settings_hit(372, row_y(4), 5), ("row", 4, "row"))       # between the - and + buttons

    def test_only_the_rows_of_the_tab_can_be_hit(self):
        self.assertIsNone(m.settings_hit(100, row_y(3), 3))                           # General has three rows
        self.assertEqual(m.settings_hit(100, row_y(4), 5), ("row", 4, "row"))        # Display has five
        self.assertIsNone(m.settings_hit(100, m.SET_Y0 + m.SET_ROW_H + 1, 5))        # the gap between rows
        self.assertIsNone(m.settings_hit(5, row_y(1), 5))                             # left of the rows


class LanguagePicker(Base):
    def test_ten_cells_and_the_close_button(self):
        self.assertEqual(m.picker_hit(450, 26), "close")
        self.assertIsNone(m.picker_hit(100, 26))
        cells = {m.picker_hit(x, y) for x in (100, 350) for y in range(m.SET_Y0 + 5, m.SET_Y0 + 5 * 50, 50)}
        self.assertEqual(cells, set(range(len(m.LANGUAGE_CODES))))

    def test_gaps_are_not_hits(self):
        self.assertIsNone(m.picker_hit(240, 80))                                      # between the two columns
        self.assertIsNone(m.picker_hit(100, m.SET_Y0 + 46))                           # between two rows
        self.assertIsNone(m.picker_hit(100, m.SET_Y0 + 5 * 50 + 5))                   # below the last row


class RoomsAndMenus(Base):
    def test_rooms_rows(self):
        for i in range(m.ROOM_ROWS):
            self.assertEqual(m.rooms_row_at(200, m.ROOM_Y0 + i * m.ROOM_PITCH + 10), i)
        self.assertIsNone(m.rooms_row_at(200, m.ROOM_Y0 + m.ROOM_ROW_H + 2))          # gap
        self.assertIsNone(m.rooms_row_at(5, m.ROOM_Y0 + 10))                          # outside the list

    def test_confirmation_buttons(self):
        for name, (x0, y0, x1, y1) in m.ROOM_CONFIRM_BUTTONS.items():
            self.assertEqual(m.confirm_button_at((x0 + x1) // 2, (y0 + y1) // 2), name)
        self.assertIsNone(m.confirm_button_at(240, 200))                              # between yes and no

    def test_menu_rows(self):
        self.assertEqual(list(m.POWER_ROWS), ["settings", "restart", "shutdown", "cancel"])
        for name, (x0, y0, x1, y1) in m.POWER_ROWS.items():
            self.assertEqual(m.power_button_at((x0 + x1) // 2, (y0 + y1) // 2), name)
        self.assertIsNone(m.power_button_at(240, 123))                                # between two rows

    def test_menu_rows_stay_inside_the_panel(self):
        px0, py0, px1, py1 = m.POWER_PANEL
        for name, (x0, y0, x1, y1) in m.POWER_ROWS.items():
            self.assertTrue(px0 <= x0 and x1 <= px1 and py0 <= y0 and y1 <= py1, name)

    def test_player_buttons(self):
        self.assertEqual(m.button_at(240, 270), "play")
        self.assertEqual(m.button_at(40, 270), "stations")
        self.assertEqual(m.button_at(440, 270), "volume")
        self.assertIsNone(m.button_at(240, 100))                                      # above the button row


if __name__ == "__main__":
    unittest.main()
