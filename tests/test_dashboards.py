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
    """tools/make_dashboards.py: generated pages."""
    def setUp(self):
        """A temporary output folder."""
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        """Removes the output folder."""
        shutil.rmtree(self.out)

    def page(self, name):
        """A generated page, read back as YAML."""
        with open(os.path.join(self.out, f"{name}.dash"), encoding='utf-8') as f:
            return yaml.safe_load(f)

    def test_example(self):
        """Example."""
        pages = make_dashboards.build(os.path.join(ROOT, 'dashboards', 'rooms.example.yaml'), self.out)
        self.assertEqual(pages, ['Guests', 'Guests_m', 'Living', 'Living_m', 'Main', 'Main_m',
                                 'Office', 'Office_m'])
        phone = self.page('Living_m')
        self.assertEqual(phone['columns'], 4)
        self.assertEqual(phone['rows'], 80)  # long pages: gridster's default is 15
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
        self.assertEqual(main['m0']['entity'], 'input_boolean.away_from_home')
        self.assertEqual(main['m0']['widget_type'], 'confirm_switch')
        self.assertEqual(main['m0']['confirm_on'], 'Mark the house as empty?')
        self.assertEqual(living['d0']['widget_type'], 'switch')
        self.assertEqual(living['t1_2']['entity'], 'input_boolean.timer_central_ac_2')
        self.assertEqual(living['t1_3_off']['entity'], 'input_datetime.timer_central_ac_3_off')
        self.assertIn('t1_1(2x1), t1_1_on(3x1), t1_1_off(3x1)', living['layout'])
        self.assertIn('t1_1_on(2x1), t1_1_off(2x1)', self.page('Living_m')['layout'])
        self.assertEqual(self.page('Office')['t2_fan']['entity'], 'input_boolean.fan_swap_office_ac')
        self.assertNotIn('t1_fan', living)
        self.assertIn('m0(2x1), spacer(4x1)', main['layout'])
        self.assertIn('m0(2x1)', self.page('Main_m')['layout'])

    def test_standalone(self):
        """A standalone page has no home button and no tile on the main page."""
        make_dashboards.build(os.path.join(ROOT, 'dashboards', 'rooms.example.yaml'), self.out)
        for variant in ('', '_m'):
            guests = self.page('Guests' + variant)
            self.assertNotIn('home', guests)
            self.assertNotIn('home', guests['layout'][0])
            self.assertEqual(guests['d0']['entity'], 'light.guest_parking')
            self.assertNotIn('go_guests', self.page('Main' + variant))

    def test_bad_room_id(self):
        """Bad room id."""
        path = os.path.join(self.out, 'rooms.yaml')
        with open(path, 'w', encoding='utf-8') as f:
            f.write("rooms:\n  - {id: 'bad id', title: x, devices: []}\n")
        with self.assertRaises(ValueError):
            make_dashboards.build(path, self.out)
