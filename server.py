from fastapi import FastAPI, HTTPException, Query, File, Form, UploadFile
from fastapi.responses import StreamingResponse
import json
import os
import time
import shutil
import csv
from typing import List, Dict, Any
from collections import deque
import threading
from datetime import datetime
import pytz

app = FastAPI()

# ────────────────────────────────────────────────
# Persistent storage configuration (Render + local fallback)
# ────────────────────────────────────────────────

# On Render with disk → use /data as the persistent root
# Locally → use a folder called "channel_data" in the current directory
PERSISTENT_ROOT = "/data" if os.path.exists("/data") else "channel_data"

# All persistent data lives directly under this root
DATA_DIR       = PERSISTENT_ROOT
MEDIA_SUBDIR   = "channel_media"
LOGS_SUBDIR    = "channel_logs"

dataFile = os.path.join(DATA_DIR, "channels2.json")
imgLimit = 20
ACTIVE_THRESHOLD_MS = 5000

# {channel_id: [name, list_of_field_dicts]}
channels: Dict[str, List[Any]] = {}

# {channel_id: deque of rows (list of values) - max 200}
log_buffer: Dict[str, deque] = {}

# Last time we flushed buffers
last_flush_time = time.time()



def ensure_directories():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, MEDIA_SUBDIR), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, LOGS_SUBDIR), exist_ok=True)



def get_media_path(cid: str) -> str:
    return os.path.join(DATA_DIR, MEDIA_SUBDIR, cid)



def get_log_path(cid: str) -> str:
    return os.path.join(DATA_DIR, LOGS_SUBDIR, f"{cid}.csv")



def loadChannels():
    if not os.path.exists(dataFile):
        return

    try:
        with open(dataFile, "r", encoding="utf-8") as f:
            raw = json.load(f)
            for cid, data in raw.items():
                channelName = data[0]
                fieldsRaw = data[1]
                fields = []
                for item in fieldsRaw:
                    fields.append({
                        "fieldName": item["fieldName"],
                        "value": None if item["value"] is None else float(item["value"])
                    })

                # Add new fields to older channels
                existing_names = [f["fieldName"] for f in fields]

                if "hops" not in existing_names:
                    fields.append({"fieldName": "hops", "value": None})

                if "signalstren" not in existing_names:
                    fields.append({"fieldName": "signalstren", "value": None})

                if "packetno" not in existing_names:
                    fields.append({"fieldName": "packetno", "value": None})

                channels[cid] = [channelName, fields]

                # Initialize buffer
                log_buffer[cid] = deque(maxlen=200)

        print(f"Loaded {len(channels)} channels from {dataFile}")
    except Exception as e:
        print(f"Error loading channels: {e}")



def saveChannels():
    try:
        serializable = {}
        for cid, data in channels.items():
            channelName = data[0]
            fieldsSer = [
                {"fieldName": f["fieldName"], "value": f["value"]}
                for f in data[1]
            ]
            serializable[cid] = [channelName, fieldsSer]
        with open(dataFile, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        print(f"Error saving channels: {e}")



def trimDirectory(folder_path, n):
    files = [
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f))
    ]

    if len(files) > n:
        files.sort(key=os.path.getmtime)
        for f in files[:-n]:
            os.remove(f)



def flush_logs():
    global last_flush_time

    now = time.time()

    if now - last_flush_time < 10:
        return

    for cid, buffer in list(log_buffer.items()):
        if not buffer:
            continue

        log_path = get_log_path(cid)
        file_exists = os.path.exists(log_path)

        field_names = [
            f["fieldName"]
            for f in channels[cid][1]
            if f["fieldName"] != "time_src"
        ]

        max_retries = 3

        for attempt in range(max_retries):
            try:
                with open(log_path, "a", newline="", encoding="utf-8") as csvfile:
                    writer = csv.writer(csvfile)

                    if not file_exists:
                        writer.writerow(field_names)

                    for row in buffer:
                        writer.writerow(row)

                buffer.clear()
                break

            except PermissionError:
                if attempt == max_retries - 1:
                    print(
                        f"Permission denied after {max_retries} attempts: {log_path}"
                    )
                time.sleep(0.5)

            except Exception as e:
                print(f"Unexpected error writing {log_path}: {e}")
                break

    last_flush_time = now



def background_flush():
    while True:
        time.sleep(10)
        flush_logs()



# ────────────────────────────────────────────────
# Startup
# ────────────────────────────────────────────────

ensure_directories()
loadChannels()

# Start background CSV writer
threading.Thread(target=background_flush, daemon=True).start()

fname_dict = {}

media_subdir_path = os.path.join(DATA_DIR, MEDIA_SUBDIR)

if os.path.exists(media_subdir_path):
    for channel in os.listdir(media_subdir_path):
        dir_path = os.path.join(media_subdir_path, channel)

        if os.path.isdir(dir_path):
            files = [
                f
                for f in os.listdir(dir_path)
                if os.path.isfile(os.path.join(dir_path, f))
            ]

            files.sort(
                key=lambda x: os.path.getmtime(
                    os.path.join(dir_path, x)
                )
            )

            fname_dict[channel] = files



# ────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────

@app.get("/createChannel")
async def createChannel(
    id: str,
    name: str,
    field1: str | None = None,
    field2: str | None = None,
    field3: str | None = None,
    field4: str | None = None,
    field5: str | None = None,
    time_src: str | None = None,
    time_des: str | None = None,
    hops: str | None = None,
    signalstren: str | None = None,
    packetno: str | None = None,
):
    if not name.strip():
        raise HTTPException(400, "Channel name cannot be empty")

    if not id.strip():
        raise HTTPException(400, "Channel ID cannot be empty")

    if id in channels:
        raise HTTPException(409, "Channel ID already exists")

    defaultNames = [
        "field1",
        "field2",
        "field3",
        "field4",
        "field5",
        "time_src",
        "time_des",
        "hops",
        "signalstren",
        "packetno"
    ]

    provided = [
        field1,
        field2,
        field3,
        field4,
        field5,
        None,
        None,
        hops,
        signalstren,
        packetno
    ]

    fields = []

    for i in range(10):
        customName = provided[i]

        fieldName = (
            customName.strip()
            if customName and customName.strip()
            else defaultNames[i]
        )

        fields.append({
            "fieldName": fieldName,
            "value": None
        })

    channels[id] = [name.strip(), fields]
    log_buffer[id] = deque(maxlen=200)

    saveChannels()

    try:
        os.mkdir(get_media_path(id))
    except FileExistsError:
        pass

    # Create empty CSV with headers
    log_path = get_log_path(id)

    field_names = [
        f["fieldName"]
        for f in fields
        if f["fieldName"] != "time_src"
    ]

    if not os.path.exists(log_path):
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(field_names)

    return {
        "status": "channel created",
        "channelId": id,
        "channelName": name.strip(),
        "fields": [
            {
                "fieldName": f["fieldName"],
                "value": f["value"]
            }
            for f in fields
        ]
    }



@app.get("/writeFields")
async def writeFields(
    id: str,
    field1: str | None = None,
    field2: str | None = None,
    field3: str | None = None,
    field4: str | None = None,
    field5: str | None = None,
    time_src: str | None = None,
    hops: str | None = None,
    signalstren: str | None = None,
    packetno: str | None = None,
):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    data = channels[id]
    fields = data[1]

    # hops defaults to 0 if omitted
    if hops is None:
        hops = "0"

    incoming = [
        field1,
        field2,
        field3,
        field4,
        field5,
        time_src,
        None,
        hops,
        signalstren,
        packetno
    ]

    for i, incoming_val in enumerate(incoming):
        if incoming_val is not None and i < len(fields):
            stripped = incoming_val.strip()

            if stripped == "":
                fields[i]["value"] = None
            else:
                try:
                    fields[i]["value"] = float(stripped)
                except ValueError:
                    fields[i]["value"] = stripped

    # packetno handling
    if packetno is not None:
        current_packetno = fields[9]["value"]

        if current_packetno is not None:
            try:
                current_packetno = float(current_packetno)

                previous_packetno = None

                # Check the most recent buffered record first
                if log_buffer[id]:
                    previous_row = log_buffer[id][-1]

                    if len(previous_row) > 7:
                        if previous_row[7] != "":
                            previous_packetno = float(previous_row[7])

                # If there is no buffered record, check the CSV
                if previous_packetno is None:
                    log_path = get_log_path(id)

                    if os.path.exists(log_path):
                        with open(
                            log_path,
                            "r",
                            encoding="utf-8"
                        ) as csvfile:
                            rows = list(csv.reader(csvfile))

                        if len(rows) > 1:
                            header = rows[0]

                            if "packetno" in header:
                                packet_index = header.index("packetno")

                                for previous_row in reversed(rows[1:]):
                                    if (
                                        len(previous_row) > packet_index
                                        and previous_row[packet_index] != ""
                                    ):
                                        previous_packetno = float(
                                            previous_row[packet_index]
                                        )
                                        break

                # If received packet number is smaller than
                # the most recent packet number, add the
                # previous packet number to it.
                if (
                    previous_packetno is not None
                    and current_packetno < previous_packetno
                ):
                    fields[9]["value"] = (
                        current_packetno + previous_packetno
                    )

            except (ValueError, TypeError):
                pass

    current_millis = time.time_ns() / 1000000
    fields[6]["value"] = current_millis

    saveChannels()

    row = []
    has_real_data = False

    # field1 - field5
    for i in range(5):
        val = fields[i]["value"]

        if val is None:
            row.append("")
        elif isinstance(val, (int, float)):
            row.append(f"{val:g}")
        else:
            row.append(str(val))

        if val is not None:
            has_real_data = True

    # hops
    val = fields[7]["value"]

    if val is None:
        row.append("")
    elif isinstance(val, (int, float)):
        row.append(f"{val:g}")
    else:
        row.append(str(val))

    # signalstren
    val = fields[8]["value"]

    if val is None:
        row.append("")
    elif isinstance(val, (int, float)):
        row.append(f"{val:g}")
    else:
        row.append(str(val))

    # packetno
    val = fields[9]["value"]

    if val is None:
        row.append("")
    elif isinstance(val, (int, float)):
        row.append(f"{val:g}")
    else:
        row.append(str(val))

    ist_tz = pytz.timezone("Asia/Kolkata")
    ist_now = datetime.fromtimestamp(
        current_millis / 1000,
        tz=ist_tz
    )

    time_des_str = ist_now.strftime("%Y-%m-%d-%H-%M-%S")

    row.append(time_des_str)

    if has_real_data:
        log_buffer[id].append(row)

    return {
        "status": "fields updated",
        "channelId": id,
        "channelName": data[0],
        "fields": [
            {
                "fieldName": f["fieldName"],
                "value": f["value"]
            }
            for f in fields
        ]
    }



@app.get("/readFields")
async def readFields(id: str):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    data = channels[id]
    fields = data[1]

    result = {
        "channelId": id,
        "channelName": data[0],
    }

    for f in fields:
        result[f["fieldName"]] = f["value"]

    return result



@app.get("/checkActive")
async def checkActive(id: str):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    latest_time_des = None

    # Check most recent buffered record first
    if log_buffer[id]:
        latest_row = log_buffer[id][-1]

        if len(latest_row) > 8:
            latest_time_des = latest_row[8]

    # If there is no buffered record, check CSV
    if latest_time_des is None:
        log_path = get_log_path(id)

        if not os.path.exists(log_path):
            return 0

        flush_logs()

        try:
            with open(log_path, "r", encoding="utf-8") as csvfile:
                rows = list(csv.reader(csvfile))

            if len(rows) <= 1:
                return 0

            latest_row = rows[-1]

            if len(latest_row) == 0:
                return 0

            latest_time_des = latest_row[-1]

        except Exception:
            return 0

    if not latest_time_des:
        return 0

    try:
        ist_tz = pytz.timezone("Asia/Kolkata")

        latest_time = datetime.strptime(
            latest_time_des,
            "%Y-%m-%d-%H-%M-%S"
        )

        latest_time = ist_tz.localize(latest_time)

        current_time = datetime.now(ist_tz)

        difference_ms = (
            current_time - latest_time
        ).total_seconds() * 1000

        if difference_ms < ACTIVE_THRESHOLD_MS:
            return 1

        return 0

    except Exception:
        return 0



@app.get("/deleteChannel")
async def deleteChannel(id: str):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    channelName = channels[id][0]

    del channels[id]
    log_buffer.pop(id, None)

    saveChannels()

    try:
        shutil.rmtree(get_media_path(id))
    except FileNotFoundError:
        pass

    try:
        os.remove(get_log_path(id))
    except FileNotFoundError:
        pass

    return {
        "status": "channel deleted",
        "channelId": id,
        "channelName": channelName
    }



@app.get("/channels")
async def listChannels():
    result = {}

    for cid, data in channels.items():
        fieldsInfo = {}

        for f in data[1]:
            fieldsInfo[f["fieldName"]] = f["value"]

        result[cid] = {
            "channelName": data[0],
            "fields": fieldsInfo
        }

    return {"channels": result}



@app.get("/listImages")
async def listImages(
    id: str,
    results: int = Query(None)
):
    if id not in channels:
        raise HTTPException(404, "Channel not found!")

    media_dir = get_media_path(id)

    if not os.path.exists(media_dir):
        return {
            "channelID": id,
            "img_list": []
        }

    if id not in fname_dict:
        fname_dict[id] = []

    if results is None:
        return {
            "channelID": id,
            "img_list": fname_dict[id]
        }

    else:
        return {
            "channelID": id,
            "img_list": fname_dict[id][-results:]
        }



@app.post("/uploadImage")
async def uploadImage(
    id: str = Query(...),
    file: UploadFile = File(...),
    filename: str = Form(...)
):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    media_dir = get_media_path(id)
    os.makedirs(media_dir, exist_ok=True)

    file_path = os.path.join(media_dir, filename)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    if id not in fname_dict:
        fname_dict[id] = []

    fname_dict[id].append(filename)
    fname_dict[id] = fname_dict[id][-imgLimit:]

    trimDirectory(media_dir, imgLimit)

    return {"sent": filename}



@app.get("/getImages")
async def getImages(
    id: str = Query(...),
    results: int = Query(1)
):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    if id not in fname_dict:
        fname_dict[id] = []

    files = fname_dict[id][-results:]

    boundary = "myboundary"

    def generate():
        for filename in files:
            path = os.path.join(
                get_media_path(id),
                filename
            )

            yield f"--{boundary}\r\n"
            yield (
                f'Content-Disposition: form-data; '
                f'name="files"; filename="{filename}"\r\n'
            )
            yield "Content-Type: application/octet-stream\r\n\r\n"

            with open(path, "rb") as f:
                yield f.read()

            yield "\r\n"

        yield f"--{boundary}--\r\n"

    return StreamingResponse(
        generate(),
        media_type=f"multipart/form-data; boundary={boundary}"
    )



@app.get("/fetchData")
async def fetchData(
    id: str = Query(...),
    results: int = Query(None)
):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    log_path = get_log_path(id)

    if not os.path.exists(log_path):
        raise HTTPException(
            404,
            "No data log found for this channel"
        )

    flush_logs()

    def generate_csv():
        with open(
            log_path,
            "r",
            encoding="utf-8"
        ) as f:
            lines = f.readlines()

        if results is not None and results > 0:
            if len(lines) > 1:
                yield lines[0]

                for line in lines[-results:]:
                    yield line
            else:
                yield lines[0] if lines else ""

        else:
            for line in lines:
                yield line

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                f"attachment; filename={id}_data.csv"
        }
    )



if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )