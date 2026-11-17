"""Tests for tools/make_dashboards.py (needs pyyaml)."""
import os
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import make_dashboards  # noqa: E402


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.out)

    def page(self, name):
        with open(os.path.join(self.out, f"{name}.dash"), encoding='utf-8') as f:
            return yaml.safe_load(f)

    def test_example(self):
        pages = make_dashboards.build(os.path.join(ROOT, 'dashboards', 'rooms.example.yaml'), self.out)
        self.assertEqual(pages, ['Living', 'Living_m', 'Main', 'Main_m', 'Office', 'Office_m'])
        phone = self.page('Living_m')
        self.assertEqual(phone['columns'], 4)
        self.assertEqual(phone['home']['dashboard'], 'Main_m')
        self.assertEqual(self.page('Main_m')['go_office']['dashboard'], 'Office_m')
        self.assertIn('c2_up(1x1), c2_stop(2x1), c2_down(1x1)', phone['layout'])
        main = self.page('Main')
        self.assertEqual(main['go_office']['dashboard'], 'Office')
        self.assertEqual(main['skin'], 'mox_glass')
        living = self.page('Living')
        self.assertEqual(living['home']['dashboard'], 'Main')
        self.assertEqual(living['d1']['icon_on'], 'mdi-air-conditioner')
        self.assertEqual([living[f'c2_{k}']['widget_type'] for k in ('name', 'up', 'stop', 'down')],
                         ['cover', 'cover_up', 'cover_stop', 'cover_down'])
        self.assertIn('c2_name(3x1), c2_up(1x1), c2_stop(1x1), c2_down(1x1)', living['layout'])
        self.assertIn({'empty': 1}, living['layout'])          # after the 2x2 tiles
        self.assertNotIn('title', self.page('Office')['d0'])   # friendly name used

    def test_bad_room_id(self):
        path = os.path.join(self.out, 'rooms.yaml')
        with open(path, 'w', encoding='utf-8') as f:
            f.write("rooms:\n  - {id: 'bad id', title: x, devices: []}\n")
        with self.assertRaises(ValueError):
            make_dashboards.build(path, self.out)
