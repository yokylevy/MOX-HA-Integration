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
    rooms:
      - id: Living          # page file name, letters and digits
        title: Living room
        icon: mdi-sofa
        devices:
          - light.living_room                         # title: the entity's friendly name
          - {entity: fan.office_fan, title: Fan}      # or a title of your own
          - {entity: switch.office_ac, title: AC, icon: mdi-air-conditioner}
          - {entity: cover.living_room_curtain, title: Curtain}
"""
import os
import re
import sys
import yaml

COLUMNS = 8
TILE_ICONS = {'light': 'mdi-lightbulb', 'fan': 'mdi-fan', 'switch': 'mdi-power',
              'input_boolean': 'mdi-power'}


def quote(text):
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"') + '"'


def device_spec(item):
    spec = {'entity': item} if isinstance(item, str) else dict(item)
    if not re.match(r'^\w+\.\w+$', spec.get('entity', '')):
        raise ValueError(f"bad device entry: {item}")
    spec['domain'] = spec['entity'].split('.')[0]
    return spec


def header(title, skin):
    return [f"title: {quote(title)}", "widget_dimensions: [120, 120]", "widget_margins: [8, 8]",
            f"columns: {COLUMNS}", f"skin: {skin}", ""]


def widget(name, lines):
    return [f"{name}:"] + [f"    {line}" for line in lines] + [""]


def tiles_layout(names, size='2x2', per_row=4):
    rows = []
    for i in range(0, len(names), per_row):
        rows.append("    - " + ", ".join(f"{n}({size})" for n in names[i:i + per_row]))
        if size.endswith('x2'):
            rows.append("    - empty: 1")
    return rows


def main_page(config):
    out = header(config.get('title', 'Home'), config.get('skin', 'mox_glass'))
    out += widget('page_title', ["widget_type: label", f"text: {quote(config.get('title', 'Home'))}"])
    out += widget('clock', ["widget_type: clock", "time_format: 24hr", 'date_format_country: "he-IL"'])
    names = []
    for room in config['rooms']:
        name = f"go_{room['id'].lower()}"
        names.append(name)
        out += widget(name, ["widget_type: navigate", f"title: {quote(room['title'])}",
                             f"dashboard: {room['id']}", f"icon_inactive: {room.get('icon', 'mdi-door')}"])
    out += ["layout:", "    - page_title(6x1), clock(2x2)", "    - spacer(6x1)"]
    out += tiles_layout(names)
    return "\n".join(out) + "\n"


def room_page(config, room):
    out = header(room['title'], config.get('skin', 'mox_glass'))
    out += widget('home', ["widget_type: navigate", 'title: ""', f"dashboard: {config.get('main', 'Main')}",
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
            icon = spec.get('icon', TILE_ICONS.get(spec['domain'], 'mdi-power'))
            out += widget(name, ["widget_type: switch", f"entity: {spec['entity']}",
                                 f"icon_on: {icon}", f"icon_off: {icon}"] + title)
            tiles.append(name)
    out += ["layout:", "    - home(1x1), page_title(7x1)"]
    out += tiles_layout(tiles)
    for base in curtains:
        out.append(f"    - {base}_name(3x1), {base}_up(1x1), {base}_stop(1x1), {base}_down(1x1)")
    return "\n".join(out) + "\n"


def build(config_path, out_dir):
    with open(config_path, encoding='utf-8') as f:
        config = yaml.safe_load(f)
    ids = [room['id'] for room in config['rooms']]
    for page_id in ids + [config.get('main', 'Main')]:
        if not re.match(r'^[A-Za-z0-9_]+$', page_id):
            raise ValueError(f"page id must be letters, digits or _: {page_id}")
    if len(set(ids)) != len(ids):
        raise ValueError("room ids must be unique")
    os.makedirs(out_dir, exist_ok=True)
    pages = {config.get('main', 'Main'): main_page(config)}
    pages.update({room['id']: room_page(config, room) for room in config['rooms']})
    for page_id, text in pages.items():
        with open(os.path.join(out_dir, f"{page_id}.dash"), 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    return sorted(pages)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print("wrote:", ", ".join(f"{p}.dash" for p in build(sys.argv[1], sys.argv[2])))
