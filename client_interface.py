import dearpygui.dearpygui as dpg
import requests
import threading
import time
import random
import string
import os
import subprocess
from pathlib import Path

# ────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────

cloud_base_URL = "pravahan.api"
API_BASE_URL = ""

READ_INTERVAL_SEC = 0.1
SERVER_RETRY_INTERVAL = 5
KEEP_NEWEST_IMAGES = 20

# API path templates
CHANNELS_LIST     = ":8000/channels"
CHANNEL_CREATE    = ":8000/createChannel?id=[id]&name=[name]&field1=[name1]&field2=[name2]&field3=[name3]&field4=[name4]&field5=[name5]"
CHANNEL_DELETE    = ":8000/deleteChannel?id=[id]"
FIELDS_WRITE      = ":8000/writeFields?id=[id]"
FIELDS_READ       = ":8000/readFields?id=[id]"
IMAGES_LIST       = ":8000/listImages?id=[id]&results=[results]"
IMAGES_GET        = ":8000/getImages?id=[id]&results=[results]"

# State
channels_data = {}
selected_channel_id = None
polling_active = False
server_online = True

field_value_texts = {}
field_label_texts = {}

MAX_FIELDS = 5

CLIENT_MEDIA_DIR = Path("client_media")


# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────

def random_id():
    return ''.join(random.choices(string.ascii_uppercase, k=6))


def api_url(template: str, **repl) -> str:
    path = template
    for k, v in repl.items():
        path = path.replace(f"[{k}]", str(v))
    return f"http://{API_BASE_URL}{path}"


def ensure_channel_dir(cid: str) -> Path:
    d = CLIENT_MEDIA_DIR / cid
    d.mkdir(parents=True, exist_ok=True)
    return d


def client_image_files(cid: str) -> list[str]:
    d = ensure_channel_dir(cid)
    return sorted(f.name for f in d.iterdir() if f.is_file())


def trim_oldest_files(directory: Path, keep: int = KEEP_NEWEST_IMAGES):
    if not directory.exists():
        return
    files = sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime)
    for f in files[:-keep]:
        try:
            f.unlink()
        except:
            pass


def open_in_explorer(path: Path | str):
    path = Path(path).resolve()
    if not path.exists():
        return
    if os.name == 'nt':
        subprocess.Popen(['explorer', str(path)])
    elif os.name == 'posix':
        cmd = 'open' if 'darwin' in os.uname().sysname.lower() else 'xdg-open'
        subprocess.Popen([cmd, str(path)])


def parse_multipart(resp):
    ct = resp.headers.get("Content-Type", "")
    if "boundary=" not in ct:
        return []
    boundary = "--" + ct.split("boundary=", 1)[1].split(";", 1)[0].strip()
    parts = resp.content.split(boundary.encode())
    result = []
    for part in parts[1:-1]:
        if len(part.strip()) < 40:
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_str = part[:header_end].decode(errors="ignore")
        body = part[header_end + 4 : -2]
        if 'filename="' not in headers_str:
            continue
        fname = headers_str.split('filename="', 1)[1].split('"', 1)[0].strip()
        if fname:
            result.append((fname, body))
    return result


# ────────────────────────────────────────────────
# Connection
# ────────────────────────────────────────────────

def connect_server():
    dpg.set_value("status_text", "Connecting...")
    dpg.configure_item("status_text", color=(180, 180, 200))

    try:
        r = requests.get(api_url(CHANNELS_LIST), timeout=5)
        r.raise_for_status()
    except:
        dpg.set_value("status_text", "Server unreachable")
        dpg.configure_item("status_text", color=(220, 70, 70))
        dpg.show_item("retry_btn")
        return

    dpg.set_value("status_text", "Connected ✓")
    dpg.configure_item("status_text", color=(100, 220, 120))
    dpg.hide_item("connect_screen")
    load_dashboard(r.json())


def retry_connect():
    connect_server()


def connect_pressed():
    global API_BASE_URL
    API_BASE_URL = cloud_base_URL if dpg.get_value("cloud_toggle") else dpg.get_value("local_url").strip()
    connect_server()


# ────────────────────────────────────────────────
# Dashboard & list
# ────────────────────────────────────────────────

def load_dashboard(data):
    global channels_data
    channels_data = data.get("channels", {})
    CLIENT_MEDIA_DIR.mkdir(exist_ok=True)
    dpg.show_item("dashboard")
    refresh_channel_list()


def refresh_channel_list():
    dpg.delete_item("channel_list", children_only=True)

    if not channels_data:
        dpg.add_text("No channels yet", parent="channel_list", color=(140,150,170))
        return

    for cid, info in sorted(channels_data.items()):
        name = info.get("channelName", "Unnamed")
        with dpg.group(parent="channel_list"):
            btn = dpg.add_button(
                label=f"{name} ({cid})",
                width=-1,
                callback=select_channel,
                user_data=cid
            )
            with dpg.tooltip(btn):
                dpg.add_text(f"ID: {cid}")


# ────────────────────────────────────────────────
# Channel view + sync
# ────────────────────────────────────────────────

def select_channel(sender, app_data, channel_id):
    global selected_channel_id, polling_active, field_value_texts, field_label_texts

    field_value_texts.clear()
    field_label_texts.clear()
    selected_channel_id = str(channel_id)

    dpg.delete_item("right_panel", children_only=True)

    base = f"http://{API_BASE_URL}"
    read_url  = base + FIELDS_READ.replace("[id]", selected_channel_id)
    write_url = base + FIELDS_WRITE.replace("[id]", selected_channel_id)

    dpg.add_text("Read endpoint", color=(140,150,170), parent="right_panel")
    dpg.add_input_text(default_value=read_url, readonly=True, width=-1, parent="right_panel")

    dpg.add_spacer(height=4, parent="right_panel")
    dpg.add_text("Write endpoint", color=(140,150,170), parent="right_panel")
    dpg.add_input_text(default_value=write_url, readonly=True, width=-1, parent="right_panel")

    dpg.add_spacer(height=12, parent="right_panel")
    dpg.add_separator(parent="right_panel")

    dpg.add_group(tag="fields_area", parent="right_panel")

    global latency_text_tag
    dpg.add_spacer(height=16, parent="right_panel")
    latency_text_tag = dpg.add_text("Latency (ms): --", color=(180, 220, 120), parent="right_panel")

    dpg.add_spacer(height=12, parent="right_panel")

    with dpg.group(horizontal=True, parent="right_panel"):
        dpg.add_button(label="Close channel", callback=close_channel)
        dpg.add_spacer(width=12)
        dpg.add_button(label="Open channel images", callback=open_channel_images_folder)

    # Latency test - using plain requests.get() without timeout on write
    latency_list = []
    write_base = base + FIELDS_WRITE.replace("[id]", selected_channel_id)

    for _ in range(5):
        ts = time.time_ns() / 1_000_000
        try:
            requests.get(f"{write_base}&time_src={ts}", timeout=None)
        except:
            pass
        time.sleep(0.1)

        try:
            r = requests.get(read_url, timeout=4)
            r.raise_for_status()
            data = r.json()
            latency_list.append(float(data.get("time_des", 0)) - float(data.get("time_src", 0)))
        except:
            pass

    if latency_list:
        dpg.set_value(latency_text_tag, f"Latency (ms): {min(latency_list):.2f}")
    else:
        dpg.set_value(latency_text_tag, "Latency (ms): --")

    sync_initial_images(selected_channel_id)
    start_polling()


def open_channel_images_folder():
    if selected_channel_id:
        open_in_explorer(CLIENT_MEDIA_DIR / selected_channel_id)


def sync_initial_images(cid: str):
    local_files = set(client_image_files(cid))
    dir_path = ensure_channel_dir(cid)

    try:
        r = requests.get(api_url(IMAGES_LIST, id=cid, results=20), timeout=8)
        r.raise_for_status()
        server_files = r.json().get("img_list", [])
    except:
        return

    to_fetch = []
    for fname in reversed(server_files):
        if fname not in local_files:
            to_fetch.append(fname)
        else:
            break

    if not to_fetch:
        trim_oldest_files(dir_path)
        return

    try:
        resp = requests.get(api_url(IMAGES_GET, id=cid, results=len(to_fetch)), timeout=12, stream=True)
        resp.raise_for_status()
        for name, content in parse_multipart(resp):
            try:
                (dir_path / name).write_bytes(content)
            except:
                pass
        trim_oldest_files(dir_path)
    except:
        pass


# ────────────────────────────────────────────────
# Polling loop
# ────────────────────────────────────────────────

def start_polling():
    global polling_active
    polling_active = True
    threading.Thread(target=poll_loop, daemon=True).start()


def poll_loop():
    global polling_active, server_online
    last_image = None
    base = f"http://{API_BASE_URL}"

    while polling_active and selected_channel_id:
        try:
            r = requests.get(base + FIELDS_READ.replace("[id]", selected_channel_id), timeout=4)
            r.raise_for_status()
            update_fields(r.json())
            server_online = True
        except:
            server_online = False
            time.sleep(SERVER_RETRY_INTERVAL)
            continue

        # New image check
        try:
            r_img = requests.get(api_url(IMAGES_LIST, id=selected_channel_id, results=1), timeout=4)
            r_img.raise_for_status()
            latest = r_img.json().get("img_list", [None])[0]
            if latest and latest != last_image:
                resp = requests.get(api_url(IMAGES_GET, id=selected_channel_id, results=1), timeout=8, stream=True)
                resp.raise_for_status()
                for name, content in parse_multipart(resp):
                    target = CLIENT_MEDIA_DIR / selected_channel_id / name
                    try:
                        target.write_bytes(content)
                        last_image = name
                        trim_oldest_files(target.parent)
                    except:
                        pass
                # only one expected
        except:
            pass

        time.sleep(READ_INTERVAL_SEC)


# ────────────────────────────────────────────────
# Field display
# ────────────────────────────────────────────────

def update_fields(data):
    global field_value_texts, field_label_texts

    exclude = {"channelId", "channelName"}
    fields = [(k, v) for k, v in data.items() if k not in exclude][:MAX_FIELDS]

    if not field_value_texts:
        dpg.delete_item("fields_area", children_only=True)

        with dpg.table(
            header_row=False,
            borders_outerH=False, borders_outerV=False,
            borders_innerH=False, borders_innerV=False,
            policy=dpg.mvTable_SizingStretchProp,
            parent="fields_area"
        ) as table:

            for _ in range(3):
                dpg.add_table_column(width_stretch=True)

            with dpg.table_row(parent=table):
                for i in range(min(3, len(fields))):
                    name, _ = fields[i]
                    with dpg.table_cell():
                        with dpg.group(horizontal=False):
                            vtag = f"val_{i}"
                            ltag = f"lbl_{i}"
                            dpg.add_text("--", tag=vtag, color=(180, 220, 255))
                            dpg.add_text(name, tag=ltag, color=(140, 150, 170))
                            field_value_texts[i] = vtag
                            field_label_texts[i] = ltag

            with dpg.table_row(parent=table):
                for i in range(3, min(5, len(fields))):
                    name, _ = fields[i]
                    with dpg.table_cell():
                        with dpg.group(horizontal=False):
                            vtag = f"val_{i}"
                            ltag = f"lbl_{i}"
                            dpg.add_text("--", tag=vtag, color=(180, 220, 255))
                            dpg.add_text(name, tag=ltag, color=(140, 150, 170))
                            field_value_texts[i] = vtag
                            field_label_texts[i] = ltag

                while len(dpg.get_item_children(dpg.get_item_children(table)[-1])) < 3:
                    with dpg.table_cell():
                        pass

    for i, (name, val) in enumerate(fields):
        vtag = field_value_texts.get(i)
        ltag = field_label_texts.get(i)
        if vtag and dpg.does_item_exist(vtag):
            dpg.set_value(vtag, "--" if val is None else f"{float(val):.2f}")
        if ltag and dpg.does_item_exist(ltag):
            dpg.set_value(ltag, name)

    for i in range(len(fields), MAX_FIELDS):
        vtag = field_value_texts.get(i)
        ltag = field_label_texts.get(i)
        if vtag and dpg.does_item_exist(vtag):
            dpg.set_value(vtag, "--")
        if ltag and dpg.does_item_exist(ltag):
            dpg.set_value(ltag, "")


# ────────────────────────────────────────────────
# Channel CRUD & close
# ────────────────────────────────────────────────

def open_create_popup():
    dpg.show_item("create_popup")


def create_channel_submit():
    cid = random_id()
    url = api_url(CHANNEL_CREATE,
                  id=cid,
                  name=dpg.get_value("create_name") or "untitled",
                  name1=dpg.get_value("create_f1") or "field1",
                  name2=dpg.get_value("create_f2") or "field2",
                  name3=dpg.get_value("create_f3") or "field3",
                  name4=dpg.get_value("create_f4") or "field4",
                  name5=dpg.get_value("create_f5") or "field5")

    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
    except:
        dpg.set_value("create_status", "Server unreachable")
        dpg.configure_item("create_status", color=(220,70,70))
        return

    dpg.set_value("create_status", "Channel created")
    dpg.configure_item("create_status", color=(100,220,120))
    time.sleep(1.2)
    dpg.hide_item("create_popup")
    refresh_pressed()


def open_delete_popup():
    dpg.show_item("delete_popup")


def delete_channel_submit():
    cid = dpg.get_value("delete_id").strip()
    if not cid:
        dpg.set_value("delete_status", "Enter an ID")
        return

    try:
        r = requests.get(api_url(CHANNEL_DELETE, id=cid), timeout=5)
        r.raise_for_status()
    except:
        dpg.set_value("delete_status", "Server unreachable")
        dpg.configure_item("delete_status", color=(220,70,70))
        return

    dpg.set_value("delete_status", "Channel deleted")
    dpg.configure_item("delete_status", color=(100,220,120))
    time.sleep(1.2)
    dpg.hide_item("delete_popup")
    refresh_pressed()


def refresh_pressed():
    global channels_data
    try:
        r = requests.get(api_url(CHANNELS_LIST), timeout=5)
        r.raise_for_status()
        channels_data = r.json().get("channels", {})
        if selected_channel_id and selected_channel_id not in channels_data:
            close_channel()
        refresh_channel_list()
    except:
        pass


def close_channel():
    global polling_active, selected_channel_id, field_value_texts, field_label_texts
    polling_active = False
    selected_channel_id = None
    field_value_texts.clear()
    field_label_texts.clear()

    dpg.delete_item("right_panel", children_only=True)

    with dpg.group(parent="right_panel", horizontal=True):
        dpg.add_spacer(width=100)
        with dpg.group(horizontal=False):
            dpg.add_spacer(height=160)
            dpg.add_text("Select a channel to view its data", color=(160,170,190))
            dpg.add_spacer(height=160)


# ────────────────────────────────────────────────
# GUI (unchanged)
# ────────────────────────────────────────────────

dpg.create_context()

with dpg.theme() as global_theme:
    with dpg.theme_component(dpg.mvAll):
        dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (15, 15, 25, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (18, 18, 28, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (28, 32, 48, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (40, 48, 70, 255))
        dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (50, 60, 90, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Button, (70, 110, 230, 220))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (90, 130, 255, 255))
        dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (60, 100, 220, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Text, (225, 225, 240, 255))
        dpg.add_theme_color(dpg.mvThemeCol_Separator, (60, 65, 90, 180))
        dpg.add_theme_color(dpg.mvThemeCol_Border, (55, 60, 85, 140))
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
        dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 9)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 6)
        dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 6)
        dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 5)
        dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 7)
        dpg.add_theme_style(dpg.mvStyleVar_ItemInnerSpacing, 4, 4)

dpg.bind_theme(global_theme)

with dpg.window(tag="connect_screen", width=520, height=380, pos=[340, 200],
                no_resize=True, no_collapse=True):
    dpg.add_spacer(height=24)
    dpg.add_text("Pravahan", color=(90, 160, 255))
    dpg.add_spacer(height=32)
    dpg.add_checkbox(label="Use Cloud Server (pravahan.api)", tag="cloud_toggle", default_value=True)
    dpg.add_spacer(height=16)
    dpg.add_text("Local server (host:port)", color=(160,170,190))
    dpg.add_input_text(tag="local_url", hint="e.g. 192.168.1.77:8000", width=320)
    dpg.add_spacer(height=24)
    dpg.add_button(label="Connect", width=180, height=36, callback=connect_pressed)
    dpg.add_spacer(height=12)
    dpg.add_text("", tag="status_text")
    dpg.add_button(label="Retry", tag="retry_btn", width=180, height=32, show=False, callback=retry_connect)


with dpg.window(tag="dashboard", show=False,
                no_title_bar=True,
                no_close=True,
                pos=[0, 0]):

    with dpg.group(horizontal=True):

        with dpg.child_window(width=440, border=False):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Create", callback=open_create_popup)
                dpg.add_button(label="Delete", callback=open_delete_popup)
                dpg.add_button(label="Refresh", callback=refresh_pressed)

            dpg.add_spacer(height=14)
            dpg.add_separator()
            dpg.add_spacer(height=12)

            dpg.add_text("Channels", color=(140,150,170))
            dpg.add_spacer(height=6)

            with dpg.child_window(tag="channel_list",
                                  autosize_x=True,
                                  autosize_y=True,
                                  border=False):
                pass

        with dpg.child_window(tag="right_panel",
                              border=False,
                              autosize_x=True,
                              autosize_y=True):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=100)
                with dpg.group(horizontal=False):
                    dpg.add_spacer(height=160)
                    dpg.add_text("Select a channel to view its data", color=(160,170,190))
                    dpg.add_spacer(height=160)


with dpg.window(label="Create Channel", modal=True, show=False, tag="create_popup", width=360, height=420,
                pos=[420, 180], no_resize=True):

    dpg.add_input_text(label="Channel Name", default_value="My Sensor", tag="create_name", width=260)
    dpg.add_spacer(height=12)
    dpg.add_input_text(label="Field 1 Name", default_value="field1", tag="create_f1", width=260)
    dpg.add_input_text(label="Field 2 Name", default_value="field2", tag="create_f2", width=260)
    dpg.add_input_text(label="Field 3 Name", default_value="field3", tag="create_f3", width=260)
    dpg.add_input_text(label="Field 4 Name", default_value="field4", tag="create_f4", width=260)
    dpg.add_input_text(label="Field 5 Name", default_value="field5", tag="create_f5", width=260)

    dpg.add_spacer(height=20)
    dpg.add_button(label="Create Channel", callback=create_channel_submit)
    dpg.add_spacer(height=8)
    dpg.add_text("", tag="create_status")


with dpg.window(label="Delete Channel", modal=True, show=False, tag="delete_popup", width=320, height=180,
                pos=[440, 260], no_resize=True):

    dpg.add_text("Channel ID to delete:")
    dpg.add_input_text(tag="delete_id", hint="6-letter uppercase ID", width=220)
    dpg.add_spacer(height=16)
    dpg.add_button(label="Delete", callback=delete_channel_submit)
    dpg.add_spacer(height=8)
    dpg.add_text("", tag="delete_status")


# ────────────────────────────────────────────────
# Start
# ────────────────────────────────────────────────

dpg.create_viewport(title="Pravahan Channel Manager", width=1200, height=780)
dpg.setup_dearpygui()
dpg.show_viewport()
dpg.set_primary_window("dashboard", True)

dpg.start_dearpygui()
dpg.destroy_context()