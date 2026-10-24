# 修复：模拟器刚拉起就执行任务，nemu_ipc 与 uiautomator2 全部连接失败

## 现象

实例 E，23:00:03 Restart 任务到点，模拟器被拉起：

```
23:00:06.179 | Execute: MuMuManager.exe api -v 4 launch_player
23:00:16.946 | New window showing up: 2889834, focus back
23:00:16.955 | Waiting emulator online 10s/180s: 127.0.0.1:16512 (devices: ['127.0.0.1:16512'])
23:00:16.962 | Emulator online: AdbDevice(127.0.0.1:16512, device)
23:00:17.083 | Command ping: pong
23:00:17.691 | Found azurlane packages: ['com.bilibili.azurlane']
23:00:17.753 | Emulator start completed          ← launch 后仅 11 秒就判定「就绪」
23:00:17.872 | NemuIpcImpl init, instance_id=4
23:00:17.899 | Failed to call nemu_connect, result=0
23:00:17.931 | Emulator info incorrect
（第二条初始化路径连试 5 次，全部 result=0，23:00:21.973 结束）
23:00:24.992 | <<< APP RESTART >>>  App stop: com.bilibili.azurlane
23:00:26.585 | ERROR | AdbError -> ConnectionError: Remote end closed connection without response
               url = 'http://127.0.0.1:58441/shell'   ← atx-agent 的 forward 端口
               uiautomator_2.py:384 in app_stop_uiautomator2
```

adb 明明是通的，`pm list packages` 也查到了包，但 nemu_ipc 和 uiautomator2 全部连不上。

## 根因

### P1（主因）`emulator_start_watch()` 没有等系统 boot 完成

`module/device/platform/platform_windows.py:211-314` 的判据只有三条：
`adb devices` 里 status=device、`adb shell echo pong` 能回、`list_known_packages()` 非空。

**没有 `sys.boot_completed`。** 而 MuMu 的 adb 端口是 vbox NAT 转发，虚机进程一起来端口就通，
`pm list packages` 也只依赖 package manager（比 boot 完成早得多）。于是 launch 后 11 秒
就被判成「启动完成」，此时 Android 系统还在启动，nemu 渲染服务（nemu_ipc）和 atx-agent
都还没起来。

对照：MAA 子模块的 `submodule/AlasMaaBridge/module/device/emulator.py:196-200`
有 `boot_completed()`（`getprop sys.boot_completed == '1'`）并在 `wait_online()` 里作为判据。
主模块漏了这一条，两边不一致。

### P2 `u2` 是 cached_property，且全仓库从不清缓存

`module/device/connection_attr.py:341-357`：

```python
@cached_property
def u2(self) -> u2.Device:
    ...
    device = u2.connect_usb(self.serial)     # 内部会 adb forward 一个随机端口
    device.set_new_command_timeout(604800)
    return device
```

`u2.connect_usb()` 会建立 `adb forward tcp:<随机端口> -> tcp:7912`，日志里那个
`127.0.0.1:58441` 就是它。**模拟器被关掉后 forward 规则随之失效，模拟器重启后不会重建。**

而全仓库搜索 `del_cached_property` 的结果里，**没有任何一处清理 `'u2'`**：
`release_resource()`（connection.py:882-887）清了 `hermit_session` / `droidcast_session` /
`_minitouch_builder` / `_maatouch_builder` / `reverse_server`，唯独漏了 `u2`；
`device.py:298` 的 `emulator_check()` 清了 `'adb'`，也没清 `'u2'`。

于是 `app_stop_uiautomator2()` 拿到的还是重启前那个 `u2.Device`，继续往已经失效的
58441 端口发请求，必然 `RemoteDisconnected`。

（对照：`emulator_check()` 里对 nemu_ipc 是做了 `self.nemu_ipc_release()` 的——
见 device.py:297，说明作者记得清 nemu_ipc，只漏了 u2。）

### P3 nemu_ipc 重试窗口太短

`NemuIpcImpl.connect_with_retry()` 用 `RETRY_TRIES = 5`（`method/utils.py:55`），
实测从 23:00:17.872 到 23:00:21.973 只撑了约 4 秒就放弃。模拟器冷启动到渲染服务
可用通常要几十秒，4 秒不够。P1 修好后这条大概率不再触发，但值得顺手放宽。

## 修复要求

### R1（必须）`emulator_start_watch()` 加 boot 完成判据

`platform_windows.py` 的 `emulator_start_watch()`，在 `show_ping(pong)` 之后、
`list_known_packages()` 之前插入：

```python
# Check system boot completed
# MuMu 的 adb 是 vbox NAT 转发，虚机进程一起来端口就通，`pm list packages`
# 也只依赖 package manager，两者都比 boot 完成早得多。不看 sys.boot_completed
# 的话，launch 后 10 秒就会被判成「就绪」，随后 nemu_ipc / atx-agent 全连不上。
try:
    boot = self.adb_shell(['getprop', 'sys.boot_completed']).strip()
except Exception:
    boot = ''
if boot != '1':
    continue
```

要点：
- 未 boot 完成时 `getprop` 返回的是**空字符串**，不是 `'0'`，所以只能判 `== '1'`。
- 与 MAA 子模块 `MaaEmulator.boot_completed()` 的写法保持一致。
- 不要替换原有的三条判据，它们是必要条件，boot_completed 是追加。
- 加一条 `@run_once` 的日志（例如 `Waiting system boot completed`），否则这段时间又会是静默的。

### R2（必须）模拟器重启 / adb 断开时清理 `u2` 缓存

在 `module/device/connection.py` 的 `release_resource()`（882-887 行）里补上：

```python
def release_resource(self):
    del_cached_property(self, 'hermit_session')
    del_cached_property(self, 'droidcast_session')
    del_cached_property(self, '_minitouch_builder')
    del_cached_property(self, '_maatouch_builder')
    del_cached_property(self, 'reverse_server')
    del_cached_property(self, 'u2')        # ← 新增
```

`release_resource()` 被 `adb_disconnect()` 和 `adb_restart()` 调用，而 `adb_reconnect()`
必然经过其中之一，所以这一处能覆盖所有「adb 连接被重建」的场景——那正是 forward 规则失效、
`u2` 缓存必然失效的时刻。

需要在 `connection.py` 顶部确认已导入 `del_cached_property`（第 14 行已有）。

补充要求：在 `device.py` 的 `emulator_check()` 成功路径（296-300 行）里，
把 `del_cached_property(self, 'u2')` 紧跟 `del_cached_property(self, 'adb')` 写在一起，
与已有的 `self.nemu_ipc_release()` 并列。两处都加是刻意的：
`emulator_check()` 是「设备实例被重建」的唯一入口，语义最明确。

### R3（建议）放宽 nemu_ipc 的重试窗口

`nemu_ipc.py` 的 `NemuIpc.__init__` / `connect_with_retry()`：把首次连接失败后的
重试总窗口从当前的约 4 秒放宽到 60 秒以上（用 `Timer` 控总时长，而不是只靠
`RETRY_TRIES` 次数），并在日志里带上已等待时长。

理由：模拟器冷启动到 `nemu_connect` 可用确实需要几十秒，当前重试策略对「还没起来」
和「真的坏了」不做区分，一律快速放弃。

### 不要动的地方

- 不要删 `emulator_start_watch()` 里原有的 ping / packages 判据。
- 不要为了「让 nemu_ipc 早点连上」去改 `EmulatorInfo_path` 或 dll 路径探测逻辑，
  路径是对的（`instance_id=4`、`nx_device/12.0/shell/sdk` 都解析正确）。
- 不要动 `WAIT_ONLINE_TIMEOUT` / 180 秒超时。
- 不要改 `emulator_start()` 的 3 次重试结构。
- 不要动 MAA 子模块。

## 验收

1. 关掉实例 E 的模拟器 → 等任务到点自动拉起。日志里 `launch_player` 到
   `Emulator start completed` 的间隔应从现在的 11 秒变成 40~90 秒（等到 boot 完成）。
2. 之后不得再出现 `Failed to call nemu_connect, result=0` 和 `Emulator info incorrect`。
3. `App stop: com.bilibili.azurlane` 不再抛 `RemoteDisconnected`；
   日志里 `u2.Device` 的 atx_agent_url 应是**重新分配**的端口（不再是重启前的 58441）。
4. 正常运行时（模拟器一直在跑）不得因为清 `u2` 缓存产生额外延迟——
   `u2.Device` 只在 adb 重连后才重新初始化。

## 部署提醒

改动文件：
- `module/device/platform/platform_windows.py`
- `module/device/connection.py`
- `module/device/device.py`
- （可选）`module/device/method/nemu_ipc.py`

仓库与运行目录各有一份，**两边都要改**：
- 源码仓：`C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript/module/`
- 运行副本：`D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/module/`

运行目录下的 `__pycache__/*.cpython-37.pyc` 会被直接复用，改完必须重启实例
（GUI「停止」→「开始」）才会生效。
