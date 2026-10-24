# -*- coding: utf-8 -*-
"""Storage scan 实测助手：tap / swipe / screenshot，MuMu adb，截图为 1280x720 原图。"""
import subprocess
import sys
import time

ADB = r"D:/Program Files/Netease/MuMu/nx_main/adb.exe"
SERIAL = "127.0.0.1:16384"
OUT = r"C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript/doc/storage-shots"


def shot(name):
    p = subprocess.run([ADB, "-s", SERIAL, "exec-out", "screencap", "-p"],
                       capture_output=True)
    path = f"{OUT}/{name}.png"
    with open(path, "wb") as f:
        f.write(p.stdout)
    print("saved", path)
    return path


def tap(x, y, wait=1.5):
    subprocess.run([ADB, "-s", SERIAL, "shell", "input", "tap", str(x), str(y)],
                   capture_output=True)
    time.sleep(wait)


def swipe(x1, y1, x2, y2, ms=850, wait=1.5):
    subprocess.run([ADB, "-s", SERIAL, "shell", "input", "swipe",
                    str(x1), str(y1), str(x2), str(y2), str(ms)], capture_output=True)
    time.sleep(wait)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "shot":
        shot(sys.argv[2])
    elif cmd == "tap":
        tap(int(sys.argv[2]), int(sys.argv[3]))
        shot(sys.argv[4])
    elif cmd == "swipe":
        swipe(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
        shot(sys.argv[6])
