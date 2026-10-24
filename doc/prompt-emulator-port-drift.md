# 修复：模拟器启动成功但 adb 端口漂移，3 次 180s 超时后 RequestHumanTakeover

## 现象

实例 E（MuMuPlayer-12.0-4）18:25 按 close_emulator 策略正常关闭。18:31 Restart 任务到点：

```
18:31:20.354 | cannot connect to 127.0.0.1:16513: ... (10061)
18:31:20.406 | CRITICAL | Emulator is not running, trying to start it
18:31:20.412 | Execute: MuMuManager.exe api -v 4 shutdown_player
18:31:20.425 | Execute: MuMuManager.exe api -v 4 launch_player
18:31:20.441 | Current window: 1245810
18:31:22.952 | New window showing up: 5450388, focus back     ← 模拟器窗口确实起来了
18:34:21.301 | WARNING | Emulator start timeout                 ← 正好 180s
（重试 2 次，同样 180s 超时）
18:40:23.060 | ERROR | Failed to start emulator 3 times, stopped
18:40:23.066 | Request human takeover
```

9 分钟内模拟器被反复关了又开，每次窗口都出现，但 adb 始终连不上。

## 证据（已在本机核实，不用重复排查）

1. **真实端口是 16512，不是 16513**：
   ```
   adb connect 127.0.0.1:16513  → 10061 拒绝
   adb connect 127.0.0.1:16512  → connected
   netstat: 127.0.0.1:16512  LISTENING
   ```

2. **ALAS 现在枚举出来的就是 16512**（用运行目录的 python 实测
   `EmulatorManager().all_emulator_instances`）：
   ```
   127.0.0.1:16512 | MuMuPlayer-12.0-4
   ```

3. **这次故障运行用的却是 16513**。`vms/MuMuPlayer-12.0-4/logs/VBox.log.2`
   （对应 18:25:03 结束的那次运行）：
   ```
   NAT: Set redirect TCP 127.0.0.1:16513 -> 0.0.0.0:5555
   ```
   而 `VBox.log.1`（18:31 之后那次）里 `ADB_PORT` 已经是 `16512`。

4. **`.nemu` 会被 MuMu 重写**：`MuMuPlayer-12.0-4.nemu` 的 mtime 是 `18:37:35`
   —— 正好是第三次 `launch_player`（18:37:22）之后 13 秒，内容已是 `hostport="16512"`。

结论：**MuMu 每次启动都会重新分配并重写 ADB 端口**。09:47 那次因为 16512 被占用顺延到
16513，18:31 重启时又回到 16512。端口不是稳定的。

## 根因

`module/device/platform/platform_windows.py` 的 `emulator_start_watch()`（211-314 行）：

```python
serial = self.emulator_instance.serial        # 第 219 行：启动前的快照，16513
...
while 1:
    devices = self.list_device().select(serial=serial)   # 第 268 行
    if not devices:
        adb_connect()                          # connect 的也是 self.serial = 16513
        continue
```

**整个 180 秒等待只认启动前快照里的 serial，从不重新枚举实例。**
MuMu 启动完成时已经把 `.nemu` 改成 16512，ALAS 却还在死连 16513 → 必然超时。

超时后 `emulator_start()`（316-338 行）直接 `continue` → 再 `_emulator_stop` +
`_emulator_start`，把刚启动好的虚机关掉重来，端口再变一次，三次全废。

两个放大因素：
- `find_emulator_instance()`（`platform_base.py:136-168`）在 `select(serial=...).count == 0`
  时**直接返回 None**，没有按 name 兜底。所以 `del_cached_property(self, 'emulator_instance')`
  这种复用现有属性的写法在端口漂移后救不回来——必须显式按 name 重新匹配。
- 180 秒等待期零日志，日志上完全看不出在等哪个端口的什么状态。

## 修复要求

### R1（必须）`emulator_start_watch()` 内做端口重发现

新增一个按实例名重新取 serial 的方法（`PlatformWindows` 已继承 `EmulatorManager`，
可以直接用 `self.all_emulator_instances`）：

```python
def refresh_serial_by_name(self):
    """
    按实例名重新枚举，返回模拟器当前真实的 adb 端口。

    MuMu 每次启动都会重写 .nemu 里的 ADB_PORT（实测同一天内 16513 / 16512 来回换），
    此时按 serial 已经找不到实例，只能按 name 匹配。
    """
    name = self.emulator_info.name
    if not name:
        return None
    for instance in self.all_emulator_instances:
        if instance.name == name:
            return instance.serial
    return None
```

在 `emulator_start_watch()` 的 `if not devices:` 分支里，**每 10 秒最多检查一次**
（`Timer(10)` 节流，避免每 0.5 秒扫一遍全部实例）：

```python
serial_timer = Timer(10).start()
...
if not devices:
    if serial_timer.reached():
        serial_timer.reset()
        new_serial = self.refresh_serial_by_name()
        if new_serial and new_serial != serial:
            logger.warning(f'Emulator serial changed: {serial} -> {new_serial}')
            serial = new_serial
            self.config.Emulator_Serial = self.serial = new_serial
            del_cached_property(self, 'emulator_instance')
            del_cached_property(self, 'adb')
    adb_connect()
    continue
```

注意：
- `serial` 是第 219 行的局部变量，必须同步改掉，后面 `select(serial=serial)` 才生效。
- `adb_connect()` 用的是 `self.serial`，所以必须同时更新 `self.serial`，
  否则重发现之后仍然 connect 旧端口。
- 重发现成功后不要再 `continue` 空转，下一轮 `list_device().select(serial=新端口)`
  就会命中，正常走后面的 ping / package 检查。

### R2（必须）加等待进度日志

`while 1` 里每 10 秒打一条，让「在等」和「卡死」在日志上可区分：

```python
logger.info(f'Waiting emulator online {int(elapsed)}s/180s: {serial} (devices: {[d.serial for d in devices] or "none"})')
```

### R3（建议）重试前先确认模拟器是否已起来

`emulator_start()` 的 `for _ in range(3)` 每次都无条件 `_emulator_stop` + `_emulator_start`。
端口重发现生效后一般不会走到重试；但如果仍然走到，无谓地把已启动的虚机关掉重来只会让端口
再变一次。建议在 `emulator_start_watch()` 返回 False 后，先尝试 `refresh_serial_by_name()`
拿到的 serial 能否 `adb connect` 成功，能就不要 shutdown，直接再 watch 一次。

这一条属于优化，R1 有效时可以不改，但不要改成「watch 失败就不重试」——保留 3 次上限。

### 不要动的地方

- 不要改 `emulator_check()`（`device.py:255-301`）的流程，它已经做了启动前的一次重枚举，
  问题出在「启动期间」，不是启动前。
- 不要改 `close_emulator` 策略和 `emulator_stop_and_wait()` 的判据。
- 不要为了绕开这个问题把 `Emulator_Serial` 写死成某个具体端口，或改成 `auto`。
- 不要改 `vbox_file_to_serial()` 的正则或 `iter_instances()` 的枚举逻辑，那里是对的。
- 不要动 MAA 插件（`submodule/AlasMaaBridge/`），本次是主模块问题。

## 验收

1. 把实例 E 的 serial 手动改成错误端口（例如 16513），或让 MuMu 因端口占用顺延一次，
   然后启动实例。日志应出现：
   ```
   Waiting emulator online 10s/180s: 127.0.0.1:16513 (devices: none)   ← 新增
   Emulator serial changed: 127.0.0.1:16513 -> 127.0.0.1:16512
   Emulator online: AdbDevice(...)
   Emulator start completed
   ```
2. **不得**再出现 `Emulator start timeout` 连续 3 次 → `Failed to start emulator 3 times`。
3. 端口没漂移时（正常情况）行为与现在完全一致，`Emulator start completed` 在 60 秒内完成，
   且不产生 `Emulator serial changed` 日志。
4. 配置里的 `Alas.Emulator.Serial` 被自动更新为新端口（检查 `config/E.json`）。

## 部署提醒

改动文件：`module/device/platform/platform_windows.py`（仅此一个文件）。

该目录在仓库与运行目录各有一份，**两边都要改**：
- 源码仓：`C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript/module/device/platform/`
- 运行副本：`D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/module/device/platform/`

注意运行目录里 `__pycache__/platform_windows.cpython-37.pyc` 会被直接复用，
改完必须重启实例（GUI「停止」→「开始」）让 Python 重新编译，否则跑的还是旧字节码。
