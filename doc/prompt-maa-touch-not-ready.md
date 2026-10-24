# 修复：MAA 插件拉起模拟器后 MaaCore 报 TouchModeNotAvailable

## 现象

实例 alas2（serial `127.0.0.1:16608`，MuMuPlayer-15.0-7）。00:30 按 close_emulator 策略关闭，
00:38:08 MaaFight 到点，模拟器被拉起：

```
00:38:11.155 | Execute: MuMuManager.exe api -v 7 launch_player
00:38:26.183 | 等待 adb 上线 15s/180s: 127.0.0.1:16608 (当前设备: ...没有 16608)
00:38:36.197 | 等待 adb 上线 25s/180s: 127.0.0.1:16608
00:38:42.444 | Emulator online: 127.0.0.1:16608            ← boot_completed 已通过
00:38:42.543 | 游戏进程未找到：com.hypergryph.arknightt（client_type=Official）
00:38:42.546 | CRITICAL | 游戏未运行，先执行开始唤醒
00:38:42.628 | Scheduler: Start task `MaaStartup`
00:38:42.809 | [Message.ConnectionInfo] 127.0.0.1:16608 UuidGot
00:38:42.975 | [Message.ConnectionInfo] 127.0.0.1:16608 Connected
00:38:46.210 | [Message.ConnectionInfo] 127.0.0.1:16608 TouchModeNotAvailable   ← 3.2 秒后
00:38:46.212 | CRITICAL | Request human takeover
```

## 与上一轮是不是同一个问题

**同一类，但不是同一个 bug，修复点也不同。**

上一轮（主模块实例 E，23:00）的根因是 `emulator_start_watch()` 缺 `sys.boot_completed`
判据，launch 后 **11 秒**就被判成就绪，于是 nemu_ipc 和 atx-agent 全连不上。

这一轮 MAA 插件的 `MaaEmulator.wait_online()`（`submodule/AlasMaaBridge/module/device/emulator.py:196-200`）
**本来就检查了 `boot_completed()`**——证据就是这次 adb 上线用了 **31 秒**（00:38:11 → 00:38:42），
明显是在等 boot，判据生效了。

问题在于：`sys.boot_completed == 1` 之后，Android 的 **input 子系统 / MaaTouch 服务仍需要
若干秒才可用**。MaaCore 在 adb 连上后约 3 秒就完成触控模式探测，赶在这之前了，于是
`TouchModeNotAvailable`，随后的 `StartUp` 任务 `TaskChainError` → `RequestHumanTakeover`。

所以这次要补的不是 boot 判据，而是** boot 完成之后的就绪等待 + 对触控失败的容错**。

## 修复要求

### R1（必须）刚拉起的模拟器，放行前等触控可用

`submodule/AlasMaaBridge/maa.py` 的 `MaaDevice.emulator_check()`（186-256 行）：
`maa_reconnect()` 之后、`emulator_started = True` 之前，只在**本次刚拉起**时（即
`emulator_started` 将要置真的那条路径）加一段就绪确认。

实现要点：
- 判据用 input 服务，而不是再睡固定秒数。可用 `adb shell dumpsys input`（返回非空即
  input 服务已注册），或 `adb shell service list` 里能查到 `input`。
  轮询间隔 5 秒，最长 60 秒。
- 超时**不要** RHT：触控探活失败有可能是别的原因，交给后续任务既有的
  `game_alive()` / `maa_diagnose_game()` 兜底，不要让模拟器启动路径把整个实例停掉。
- 只在 `self.emulator_started` 为真时做（模拟器本来就在跑时不需要），避免拖慢正常路径。
- 原有 `wait_online()` 里的 `boot_completed()` 判据保留，不要删。

`MaaEmulator` 已有 `shell(serial, command, timeout)` 静态方法，直接复用即可，不要新造 adb 通道。

### R2（必须）TouchModeNotAvailable 之后先重连一次，不要直接 RHT

`submodule/AlasMaaBridge/module/handler/handler.py`：

- `connect()`（578-589 行）在 `asst.connect()` 返回 True 后就认为成功了，但触控可用性是
  异步回调的（`TouchModeNotAvailable` 是 `Message.ConnectionInfo` 的 what 字段）。
- MaaCore 报 `TouchModeNotAvailable` 后，随后的任务会 `TaskChainError`，
  而 `maa_start()` 对 `TaskChainError` 一律 `raise RequestHumanTakeover`（474-475 行）。

要求：
- 在回调里识别 `what == 'TouchModeNotAvailable'` 并记一个标志位（例如
  `self.touch_mode_unavailable = True`）。
- `maa_start()` 遇到 `TaskChainError` 时，如果**本轮刚拉起过模拟器**且该标志为真，
  则先 `asst` 断开、等 10 秒重连，最多重试 2 次；仍失败才 RHT。
- 重试前打一条明确日志（例如「触控模式不可用，等待后重连 (1/2)」），
  让用户能区分「瞬态未就绪」和「真的坏了」。

### R3（提示，不要顺手改）包名不匹配的隐患仍在

日志里 `游戏进程未找到：com.hypergryph.arknightt（client_type=Official）`：
设备上装的是 `com.hypergryph.arknightt`（结尾多一个 t），而 `Official` 映射到
`com.hypergryph.arknights`。本次因为模拟器刚重启、游戏确实没开，判据结果「未找到」是对的，
唤醒流程照常走；但如果包名一直不匹配，**唤醒成功之后 `game_alive()` 仍会判「未运行」**，
最终 `maa_diagnose_game()` + RHT。

需要在 MAA 设置里把自定义包名填成实际的 `com.hypergryph.arknightt`。
这一条属于用户配置，不要写进代码里硬编码。

### 不要动的地方

- 不要删 `wait_online()` 的 `boot_completed()` 判据，它是这次能用 31 秒等到 boot 的原因。
- 不要改 `WAIT_ONLINE_TIMEOUT`（180 秒）。
- 不要改 `emulator_check()` 在 `get_next_task()` 里的两个既有调用点，
  也不要把它挪进 `while 1` 循环体（会重演 662cfb54e 修过的自激死循环）。
- 不要动主模块（`module/device/`），本次是 MAA 插件。

## 验收

1. 关掉实例 alas2 的模拟器 → 等任务到点自动拉起。日志应出现
   `Emulator online` →（新增的 input 就绪等待）→ `模拟器已重启`，
   随后 `MaaStartup` 不再出现 `TouchModeNotAvailable`。
2. 若仍出现 `TouchModeNotAvailable`，日志里应能看到重连重试（「等待后重连 (1/2)」），
   而不是立刻 `Request human takeover`。
3. 模拟器本就在线时，不得因为新增的就绪检查产生额外等待。

## 部署提醒

改动文件：
- `submodule/AlasMaaBridge/maa.py`
- `submodule/AlasMaaBridge/module/handler/handler.py`

仓库与运行目录各有一份，**两边都要改**：
- 源码仓：`C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript/submodule/AlasMaaBridge/`
- 运行副本：`D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/submodule/AlasMaaBridge/`

改完必须重启实例（GUI「停止」→「开始」）让 `.pyc` 重新编译。
