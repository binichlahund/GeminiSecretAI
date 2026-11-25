import unittest
from unittest.mock import Mock

from display_geometry import DisplayGeometry, indicator_bounds, selection_bounds


class DisplayGeometryTests(unittest.TestCase):
    def setUp(self):
        self.display = DisplayGeometry.__new__(DisplayGeometry)
        self.display.api = Mock()

    def test_virtual_desktop_includes_monitors_left_and_above(self):
        self.display.api.GetSystemMetrics.side_effect = {
            76: -2560, 77: -1440, 78: 6400, 79: 3600,
        }.__getitem__
        self.assertEqual(self.display.desktop_bounds(), (-2560, -1440, 3840, 2160))

    def test_overlay_uses_full_physical_dimensions_with_negative_origin(self):
        window = Mock()
        window.winfo_id.return_value = 123
        self.display.api.GetAncestor.return_value = 456
        self.display.place(window, (-1920, 0, 3840, 2160))
        args = self.display.api.SetWindowPos.call_args.args
        self.assertEqual(args[2:6], (-1920, 0, 5760, 2160))
        self.assertEqual(args[0], 456)

    def test_dot_uses_active_monitor_work_area_and_does_not_activate(self):
        bounds = indicator_bounds((-2560, -200, 0, 1200))
        self.assertEqual(bounds, (-34, 1166, -18, 1182))
        self.display.place(Mock(), bounds, no_activate=True)
        self.assertTrue(self.display.api.SetWindowPos.call_args.args[-1] & 0x0010)

    def test_reverse_drag_across_monitors_matches_capture_bbox(self):
        self.assertEqual(
            selection_bounds((3800, 2100), (-2500, -1400), (-2560, -1440, 3840, 2160)),
            (-2500, -1400, 3800, 2100),
        )

    def test_drag_at_display_edges_is_clamped(self):
        self.assertEqual(
            selection_bounds((-4000, -4000), (5000, 5000), (-2560, -1440, 3840, 2160)),
            (-2560, -1440, 3840, 2160),
        )

    def test_cursor_uses_windows_physical_coordinates(self):
        def fill_point(pointer):
            point = pointer._obj
            point.x, point.y = -2300, 1800
            return 1
        self.display.api.GetCursorPos.side_effect = fill_point
        self.assertEqual(self.display.cursor_position(), (-2300, 1800))


if __name__ == "__main__":
    unittest.main()
