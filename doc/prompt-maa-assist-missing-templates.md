# 修复任务：MAA 启动辅助被整体跳过（运行实例缺少模板文件）

> 提示词使用说明：本文件全文可直接作为任务提示词交给编码代理/工程师。
> 结论已定位完毕：**不是代码 bug，是部署不同步**。请按本文结论执行，不要重新排查根因。

## 0. 硬约束（用户反复强调，违反即不合格）

**这个功能不能写在 MAA 里。** 这是需求的一部分，不是偏好：

- 逻辑与资源**只能**放在 ALAS 侧：`submodule/AlasMaaBridge/`（MAA 插件目录，属于 ALAS 仓库）。
- **禁止**把任何文件放进 `Maa/`、`Maa/resource/`、`Maa/cache/`，**禁止**修改 MAA 的任务资源 `tasks.json`。
- **禁止**使用 MaaCore 实现本功能：不得新增 `AsstAppendTask` 任务链、不得新增 MAA 任务类型。
- 截图与点击走 adbutils（`screencap` + `input tap/swipe`）——当前实现符合，保持。

## 1. 现象（实例 alas2，2026-10-25 02:34 实测日志）

`MaaStartup` 走完 `StartUp`（游戏已在主界面）后：

```
02:34:19.308 │ [Message.AllTasksCompleted] StartUp
02:34:19.484 │ Delay task `MaaStartup` to 2026-10-25 04:00:00 (server_update='04:00')
WARNING  02:34:19.508 │ 辅助启动: 缺少模板
         D:\...\AzurLaneAutoScript\submodule\AlasMaaBridge\assets\assist\assist_sun.png
02:34:19.512 │ Scheduler: End task `MaaStartup`
```

`MaaAssist.run()`（`module/handler/assist.py:92-99`）一进来就逐个 `cv2.imread` 模板，
任一个为 None 就 `return False`，**整个功能被跳过**，后续 `MaaFight` 照常运行。

## 2. 根因（已确认）

- 源码仓库 `C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript`：
  `submodule/AlasMaaBridge/assets/assist/` 下 **6 个 png 存在且已纳入 git**
  （提交 `bdfeaefeb feat: 新增魔改客户端启动辅助功能`，`git ls-files` 可查）。
- 运行实例 `D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript`：
  同一路径 **目录不存在**。此前一次代码回退删掉了该目录，随后只同步了 `.py`，png 没跟过去。
- 模板质量本身没问题：对 7 张页面图实测匹配矩阵如下（本页 1.000，跨页均未达 0.85 阈值）：

| 模板 | 本页 | 跨页最高 |
|---|---|---|
| `assist_sun` | 1.000（主界面） | 面板页 0.975~0.997 |
| `assist_agree` | 1.000（声明底部） | 0.574 |
| `assist_enter` | 1.000（密码页） | 0.676 |
| `assist_gear` | 1.000（功能面板） | 设置页 0.931 |
| `assist_hide` | 1.000（设置页） | 0.612 |
| `assist_panel` | 1.000（功能面板） | 0.654 |

（太阳在面板页也 0.97+ 是预期行为，代码里 sun 判定排在最后且有 `opened` 门控，不要"修"它。）

## 3. 修复步骤

### R1（必须）：把 6 个 png 同步到运行实例

在实例目录执行其一：

```bash
# 首选：实例与源码仓库是同一套 git 历史，直接拉取
git fetch dev && git reset --hard dev/master
# 若实例 HEAD 已领先/有本地提交，退而求其次
cp -r "C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript/submodule/AlasMaaBridge/assets" \
      "D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/submodule/AlasMaaBridge/"
```

确认：`ls D:/.../AzurLaneAutoScript/submodule/AlasMaaBridge/assets/assist/` 应有 6 个 png。

### R2（可选，加固日志）：缺模板时一次报全

当前只报第一个缺失文件就返回。改为收集全部缺失文件名，一条 warning 列出，并追加一句
「请确认部署：`git fetch dev && git reset --hard dev/master`」。改完仍保持 `return False`。

### R3（可选，功能 gap，先报告再动手）

**密码流程未实现**：现在只点「进入辅助」，依赖辅助自己记住密码。若辅助没记住就会卡在密码页。
如需补齐：点击密码框 `(435,124,755,190)` → `input keyevent 123`（光标到末尾）→
`keyevent 67` × 30 删除旧值 → `input text <MaaAssist.Password>`；密码配置留空则跳过输入。
**这一项是否要做请先说明，不要默认实施。**

## 4. 约束

- 只做 R1（+ 可选的 R2/R3），**不要重构** `assist.py`，不要改阈值与坐标（已验证可用）。
- 不要动 `Maa/` 下任何文件（见第 0 节）。
- 运行实例 Python 为 `toolkit/python.exe`（**3.7.6**），禁止 3.8+ 语法与新依赖。
- 不要 `git commit` / `push`，改动留在工作区（R1 是文件同步，不产生新提交）。

## 5. 验收（附实际输出）

1. `ls` 实例 `assets/assist/` → 6 个 png（agree / enter / gear / hide / panel / sun）。
2. 在实例把 `MaaAssist.Enable` 打开，跑一次 `MaaStartup`，日志应出现
   `OPEN_ISLAND` →（可有 0~4 次 `声明页向下滚动`）→ `AGREE` → `ENTER_ASSIST` →
   `OPEN_SETTINGS` → `HIDE_ISLAND` → `灵动岛已隐藏，辅助设置完成`。
   不应再出现 `辅助启动: 缺少模板`。
3. 若某步 `score=` 在 0.7~0.85 徘徊（说明模板与真实截图有缩放差异），
   **优先**用模拟器原生 1280x720 截图重裁模板；素材在 `doc/assist-shots/`
   （7 张已对齐 1280x720，按页面命名：`main_clean` / `main_arrow` / `decl_top` /
   `decl_bottom` / `password` / `panel` / `settings`；`original/` 为未缩放原图）。
   裁剪坐标见 `module/handler/assist.py` 顶部 `TEMPLATES` 常量。
   不要一上来就把 `THRESHOLD = 0.85` 往下调。

## 6. 交付物

1. 实例侧 6 个 png 的 `ls` 输出
2. 一次 `MaaStartup` 的完整日志片段（含 `辅助启动` 相关行）
3. 5 行以内说明：根因（部署不同步）、做了什么、验证结果
4. 若做了 R2/R3，附对应 diff
