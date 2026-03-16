'''
File name - server.py

Install fastapi -
pip install fastapi

cmd command to find IPv4 address -
ipconfig | find "IPv4"

cmd command to run server -
uvicorn test:app --host 0.0.0.0 --port 8000

API to list available channels -
http://192.168.31.68:8000/channels

API to create new channel -
http://192.168.31.68:8000/createChannel?id=ABCD&name=hello&field1=temperature&field2=humidity&field3=soil_moisture

API to delete existing channel -
http://192.168.31.68:8000/deleteChannel?id=ABCD

API to write channel fields -
http://192.168.31.68:8000/writeFields?id=ABCD&field1=31.66&field2=68.9227&field3=41   &field4=[val4]&field5=[val5]

API to read channel fields -
http://192.168.31.68:8000/readFields?id=ABCD


Interactive gui for api testing -
http://192.168.31.68:8000/docs
'''

from fastapi import FastAPI, HTTPException, Query
import json
import os
from typing import List, Dict, Any
import time
import shutil
from fastapi import File, Form, UploadFile
from fastapi.responses import StreamingResponse

app = FastAPI()

dataFile = "channels2.json"

imgLimit = 20

#Dictionary format
# {ch1_id : [ch1_name, [{f1_name : "name", f1_val : "val"}, {f2_name : "name", f2_val : "val"}, ..... ]]}
channels: Dict[str, List[Any]] = {}


def loadChannels():
    if os.path.exists(dataFile):
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
                    channels[cid] = [channelName, fields]
            print(f"Loaded {len(channels)} channels from {dataFile}")

        except Exception as e:
            print(f"Error loading {dataFile}: {e}. Starting empty.")


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
        print(f"Saved {len(channels)} channels to {dataFile}")
    except Exception as e:
        print(f"Error saving {dataFile}: {e}")


def trimDirectory(folder_path, n):
    files = [os.path.join(folder_path, f) for f in os.listdir(folder_path)]
    files = [f for f in files if os.path.isfile(f)]

    x = len(files)

    if x > n:
        files.sort(key=os.path.getmtime)  # oldest first
        for f in files[:x - n]:
            os.remove(f)


# Load data at startup
loadChannels()

try:
    os.mkdir("channel_media")
except FileExistsError:
    pass
except FileNotFoundError:
    pass


fname_dict = {}

for channel in os.listdir("channel_media"):
    dir_path = f"channel_media/{channel}"

    if os.path.isdir(dir_path):

        files = [f for f in os.listdir(dir_path)
                 if os.path.isfile(os.path.join(dir_path, f))]

        files.sort(key=lambda x: os.path.getmtime(os.path.join(dir_path, x)))

        fname_dict[channel] = files


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
):
    if not name.strip():
        raise HTTPException(400, "Channel name cannot be empty")
    if not id.strip():
        raise HTTPException(400, "Channel ID cannot be empty")
    if id in channels:
        raise HTTPException(409, "Channel ID already exists")

    defaultNames = ["field1", "field2", "field3", "field4", "field5", "time_src", "time_des"]
    provided = [field1, field2, field3, field4, field5, None, None]

    fields = []
    for i in range(7):
        customName = provided[i]
        fieldName = customName.strip() if customName and customName.strip() else defaultNames[i]
        fields.append({"fieldName": fieldName, "value": None})

    channels[id] = [name.strip(), fields]

    saveChannels()

    try:
        os.mkdir(f"channel_media/{id}")
    except FileNotFoundError:
        print("Parent directory not found!")

    return {
        "status": "channel created",
        "channelId": id,
        "channelName": name.strip(),
        "fields": [{"fieldName": f["fieldName"], "value": f["value"]} for f in fields]
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
):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    data = channels[id]
    fields = data[1]

    updates = [field1, field2, field3, field4, field5, time_src]
    for i, val in enumerate(updates):
        if val is not None and i < len(fields):
            fields[i]["value"] = val

    if time_src != None:
        fields[6]["value"] = time.time_ns()/1000000
    
    saveChannels()

    return {
        "status": "fields updated",
        "channelId": id,
        "channelName": data[0],
        "fields": [{"fieldName": f["fieldName"], "value": f["value"]} for f in fields]
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


@app.get("/deleteChannel")
async def deleteChannel(id: str):
    if id not in channels:
        raise HTTPException(404, "Channel not found")

    channelName = channels[id][0]
    del channels[id]

    saveChannels()

    try:
        shutil.rmtree(f"channel_media/{id}")
    except FileNotFoundError:
        print("Deletion failed, directory not found!")

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

    # path = f"channel_media/{id}"

    # imgList = sorted(
    #     [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))],
    #     key=lambda x: os.path.getctime(os.path.join(path, x)),
    #     reverse=True  # True for newest first
    # )

    if results == None:
        return {
            "channelID": {id},
            "img_list": fname_dict[id]
        }
    
    else:
        return {
            "channelID": {id},
            "img_list": fname_dict[id][-results:]
        }
    

@app.post("/uploadImage")
async def uploadImage(
        id: str = Query(None),
        file: UploadFile = File(...),
        filename: str = Form(...)
    ):

    with open(f"channel_media/{id}/{filename}", "wb") as f:
        shutil.copyfileobj(file.file, f)

    fname_dict[id].append(filename)
    # if len(fname_dict[id]) > imgLimit:
    #     fname_dict[id] = fname_dict[id][-imgLimit:] # trim list of imgs
    fname_dict[id] = fname_dict[id][-imgLimit:] # trim list of imgs

    trimDirectory(f"channel_media/{id}",imgLimit)

    return {
        "sent": filename
    }


@app.get("/getImages")
async def getImages(
        id: str = Query(...),
        results: int = Query(1)
    ):

    files = fname_dict[id]
    files = files[-results:]  # newest N files

    boundary = "myboundary"

    def generate():
        for filename in files:
            path = f"channel_media/{id}/{filename}"

            yield f"--{boundary}\r\n"
            yield f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
            yield "Content-Type: application/octet-stream\r\n\r\n"

            with open(path, "rb") as f:
                yield f.read()

            yield "\r\n"

        yield f"--{boundary}--\r\n"

    return StreamingResponse(
        generate(),
        media_type=f"multipart/form-data; boundary={boundary}"
    )