"""
Builds AppDaemon HADashboard pages from a rooms file: a main page with a tile per room,
and a page per room with its lights, fans, switches and curtains.

    python tools/make_dashboards.py rooms.yaml OUTPUT_FOLDER

Copy the .dash files from OUTPUT_FOLDER to the AppDaemon add-on's dashboards folder
(/addon_configs/<appdaemon>/dashboards) and open http://<home-assistant>:5050/<main page>.
The pages use the mox_glass skin and the cover_up/cover_stop/cover_down widgets from
dashboards/custom_css and dashboards/custom_widgets in this repository.

rooms.yaml (see dashboards/rooms.example.yaml):
    main: Main              # file name of the main page
    title: Our home
    skin: mox_glass
    main_devices:           # optional: house-wide switches on the main page, beside the clock
      - {entity: input_boolean.away_from_home, title: Away, icon: mdi-home-export-outline,
         confirm_on: "Nobody is home?", confirm_off: "Somebody is home?"}   # optional questions
    rooms:
      - id: Living          # page file name, letters and digits
        title: Living room
        icon: mdi-sofa
        devices:
          - light.living_room                         # title: the entity's friendly name
          - {entity: fan.office_fan, title: Fan}      # or a title of your own
          - {entity: switch.office_ac, title: AC, icon: mdi-air-conditioner, timers: 3,   # daily timers
             fan_swap: true}   # switch: fan on when the AC goes off (mox_timers fans)
          - {entity: cover.living_room_curtain, title: Curtain}
      - id: Guests
        title: Guest unit
        standalone: true    # its own page: no tile on the main page, no button back to it
        devices: [light.guest_parking]
"""
import os
import re
import sys
import yaml

# Two layouts of every page: the wide one, and a phone one whose file name ends in PHONE.
# The mox_glass skin opens the phone pages on narrow screens (see its head_includes).
PHONE = '_m'
LAYOUTS = {
    '': {'columns': 8, 'size': [120, 120], 'margins': [8, 8], 'tiles_per_row': 4},
    PHONE: {'columns': 4, 'size': [84, 84], 'margins': [6, 6], 'tiles_per_row': 2},
}
TILE_ICONS = {'light': 'mdi-lightbulb', 'fan': 'mdi-fan', 'switch': 'mdi-power',
              'input_boolean': 'mdi-power'}


def quote(text):
    """Text as a double-quoted YAML string (Hebrew and other text stay readable)."""
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"') + '"'


def device_spec(item):
    """
    A device entry (an entity id, or a mapping with 'entity' and options) as a dict with
    'entity' and 'domain'. Raises ValueError for a bad entity id.
    """
    spec = {'entity': item} if isinstance(item, str) else dict(item)
    if not re.match(r'^\w+\.\w+$', spec.get('entity', '')):
        raise ValueError(f"bad device entry: {item}")
    spec['domain'] = spec['entity'].split('.')[0]
    return spec


def header(title, skin, layout):
    """The page settings: title, tile size and margins, columns, rows and skin."""
    return [f"title: {quote(title)}", f"widget_dimensions: {layout['size']}",
            f"widget_margins: {layout['margins']}", f"columns: {layout['columns']}",
            "rows: 80",  # gridster's default is 15: tiles below it get squeezed into gaps
            f"skin: {skin}", ""]


def widget(name, lines):
    """A widget definition: its name and indented settings, as YAML lines."""
    return [f"{name}:"] + [f"    {line}" for line in lines] + [""]


def tiles_layout(names, per_row, size='2x2'):
    """
    Layout rows with up to `per_row` widgets of `size` each. After a row of 2-high tiles an
    'empty: 1' row is added, because the layout engine moves one row per layout line.
    """
    rows = []
    for i in range(0, len(names), per_row):
        rows.append("    - " + ", ".join(f"{n}({size})" for n in names[i:i + per_row]))
        if size.endswith('x2'):
            rows.append("    - empty: 1")
    return rows


def switch_widget(name, spec):
    """
    Widget lines for a light, fan or switch tile, or a confirm_switch when the device has
    confirm_on / confirm_off texts.
    """
    title = [f"title: {quote(spec['title'])}"] if spec.get('title') else []
    icon = spec.get('icon', TILE_ICONS.get(spec['domain'], 'mdi-power'))
    lines = ["widget_type: switch", f"entity: {spec['entity']}", f"icon_on: {icon}", f"icon_off: {icon}"]
    if spec.get('confirm_on') or spec.get('confirm_off'):  # ask before switching
        lines[0] = "widget_type: confirm_switch"
        lines += [f"confirm_on: {quote(spec.get('confirm_on', ''))}",
                  f"confirm_off: {quote(spec.get('confirm_off', ''))}"]
    return widget(name, lines + title)


def timer_section(config, spec, base, variant):
    """
        Timer rows for a device with 'timers: N' (run by the mox_timers app): a label, then for
        each timer its enable switch and its on and off times. Returns (layout rows, widget lines).
    """
    name = spec['entity'].split('.', 1)[1]
    texts = {'label': 'Timers', 'timer': 'Timer', 'on': 'On', 'off': 'Off', 'fan_swap': 'Fan when AC is off'}
    texts.update(config.get('timer_texts') or {})
    widgets = widget(f"{base}_label", ["widget_type: label",
                                       f"text: {quote(texts['label'] + ' - ' + spec.get('title', name))}",
                                       'text_style: "color: #ffffff; font-size: 140%; font-weight: 600"'])
    rows = [f"    - {base}_label({LAYOUTS[variant]['columns']}x1)"]
    for n in range(1, int(spec['timers']) + 1):
        widgets += widget(f"{base}_{n}", ["widget_type: switch", f"entity: input_boolean.timer_{name}_{n}",
                                          "icon_on: mdi-timer-outline", "icon_off: mdi-timer-off-outline",
                                          f"title: {quote(texts['timer'] + ' ' + str(n))}"])
        for kind in ('on', 'off'):
            widgets += widget(f"{base}_{n}_{kind}", ["widget_type: input_datetime",
                                                     f"entity: input_datetime.timer_{name}_{n}_{kind}",
                                                     f"title: {quote(texts[kind])}"])
        if variant == PHONE:
            rows += [f"    - {base}_{n}(4x1)", f"    - {base}_{n}_on(2x1), {base}_{n}_off(2x1)"]
        else:
            rows.append(f"    - {base}_{n}(2x1), {base}_{n}_on(3x1), {base}_{n}_off(3x1)")
    if spec.get('fan_swap'):  # mox_timers' fan swap switch for this device
        widgets += widget(f"{base}_fan", ["widget_type: switch", f"entity: input_boolean.fan_swap_{name}",
                                          "icon_on: mdi-fan-auto", "icon_off: mdi-fan-auto",
                                          f"title: {quote(texts['fan_swap'])}"])
        rows.append(f"    - {base}_fan(4x1)")
    return rows, widgets


def main_page(config, variant=''):
    """The main page in one layout: title, clock, house-wide buttons (main_devices) and a tile per room."""
    layout = LAYOUTS[variant]
    out = header(config.get('title', 'Home'), config.get('skin', 'mox_glass'), layout)
    out += widget('page_title', ["widget_type: label", f"text: {quote(config.get('title', 'Home'))}"])
    out += widget('clock', ["widget_type: clock", "time_format: 24hr", 'date_format_country: "he-IL"'])
    buttons = []  # house-wide switches (main_devices), beside the clock
    for n, spec in enumerate(device_spec(d) for d in config.get('main_devices') or []):
        if spec['domain'] == 'cover':
            raise ValueError(f"main_devices can't hold covers: {spec['entity']}")
        out += switch_widget(f"m{n}", spec)
        buttons.append(f"m{n}")
    names = []
    for room in config['rooms']:
        if room.get('standalone'):  # a page of its own, not linked from the main page
            continue
        name = f"go_{room['id'].lower()}"
        names.append(name)
        out += widget(name, ["widget_type: navigate", f"title: {quote(room['title'])}",
                             f"dashboard: {room['id']}{variant}", f"icon_inactive: {room.get('icon', 'mdi-door')}"])
    if variant == PHONE:
        out += ["layout:", "    - page_title(4x1)", "    - clock(4x1)"]
        out += tiles_layout(buttons, 2, size='2x1')
    else:
        beside_clock, rest = buttons[:3], buttons[3:]
        cells = [f"{b}(2x1)" for b in beside_clock] + (["spacer(%dx1)" % (6 - 2 * len(beside_clock))]
                                                       if len(beside_clock) < 3 else [])
        out += ["layout:", "    - page_title(6x1), clock(2x2)", "    - " + ", ".join(cells)]
        out += tiles_layout(rest, 4, size='2x1')
    out += tiles_layout(names, layout['tiles_per_row'])
    return "\n".join(out) + "\n"


def room_page(config, room, variant=''):
    """
    A room page in one layout: home button and title, device tiles, a row per curtain
    (name + up/stop/down) and the timer rows. A 'standalone' room has no home button.
    """
    layout = LAYOUTS[variant]
    standalone = bool(room.get('standalone'))
    out = header(room['title'], config.get('skin', 'mox_glass'), layout)
    if not standalone:
        out += widget('home', ["widget_type: navigate", f"title: {quote(config.get('home_title', 'Home'))}",
                               f"dashboard: {config.get('main', 'Main')}{variant}",
                               "icon_inactive: mdi-home"])
    out += widget('page_title', ["widget_type: label", f"text: {quote(room['title'])}"])
    tiles, curtains = [], []
    for n, spec in enumerate(device_spec(d) for d in room.get('devices', [])):
        title = [f"title: {quote(spec['title'])}"] if spec.get('title') else []
        if spec['domain'] == 'cover':
            base = f"c{n}"
            out += widget(f"{base}_name", ["widget_type: cover", f"entity: {spec['entity']}"] + title)
            for kind in ('up', 'stop', 'down'):
                out += widget(f"{base}_{kind}", [f"widget_type: cover_{kind}", f"entity: {spec['entity']}",
                                                 'title: ""'])
            curtains.append(base)
        else:
            name = f"d{n}"
            out += switch_widget(name, spec)
            tiles.append(name)
    timer_rows = []
    for n, spec in enumerate(device_spec(d) for d in room.get('devices', [])):
        if spec['domain'] == 'cover' or not spec.get('timers'):
            continue
        out_rows, widgets = timer_section(config, spec, f"t{n}", variant)
        out += widgets
        timer_rows += out_rows
    out += ["layout:", f"    - page_title({layout['columns']}x1)" if standalone
            else f"    - home(1x1), page_title({layout['columns'] - 1}x1)"]
    out += tiles_layout(tiles, layout['tiles_per_row'])
    for base in curtains:
        if variant == PHONE:  # name on its own row, then the buttons under it
            out.append(f"    - {base}_name(4x1)")
            out.append(f"    - {base}_up(1x1), {base}_stop(2x1), {base}_down(1x1)")
        else:
            out.append(f"    - {base}_name(3x1), {base}_up(1x1), {base}_stop(1x1), {base}_down(1x1)")
    out += timer_rows
    return "\n".join(out) + "\n"


def build(config_path, out_dir):
    """
    Reads the rooms file, checks the page ids and writes every page in both layouts to out_dir.
    Returns the page names.
    """
    with open(config_path, encoding='utf-8') as f:
        config = yaml.safe_load(f)
    main = config.get('main', 'Main')
    ids = [room['id'] for room in config['rooms']]
    for page_id in ids + [main]:
        if not re.match(r'^[A-Za-z0-9_]+$', page_id) or page_id.endswith(PHONE):
            raise ValueError(f"page id must be letters, digits or _ and not end in {PHONE}: {page_id}")
    if len(set(ids + [main])) != len(ids) + 1:
        raise ValueError("room ids must be unique and differ from the main page")
    os.makedirs(out_dir, exist_ok=True)
    pages = {}
    for variant in LAYOUTS:
        pages[main + variant] = main_page(config, variant)
        pages.update({room['id'] + variant: room_page(config, room, variant) for room in config['rooms']})
    for page_id, text in pages.items():
        with open(os.path.join(out_dir, f"{page_id}.dash"), 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    return sorted(pages)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print("wrote:", ", ".join(f"{p}.dash" for p in build(sys.argv[1], sys.argv[2])))
