# 新增功能：MAA 插件「魔改包启动辅助」（ALAS 层实现）

> 提示词使用说明：本文件全文可直接作为任务提示词交给编码代理/工程师。
> 文中坐标基于 1280x720 游戏画面；素材已随文档提供，**不要**让用户重新截图。

## 0. 环境与约束

- 代码位置：`submodule/AlasMaaBridge`（MAA 插件，本地目录不是 git submodule）。
- 运行实例：`D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript`，Python 为 `toolkit/python.exe`（**3.7.6**）。禁止 3.8+ 语法（walrus、dict `|`、f-string `=` 说明符）与新依赖。
- **不要动主模块** `alas.py` / `module/webui/*` / `module/device/*`，只新增插件内文件与配置。
- **不要动** `Maa/` 下的资源与二进制；本功能与 MaaCore 无关。
- 部署方式：源码仓库提交后，在实例目录 `git fetch dev && git reset --hard dev/master`。正式改动只做在源码仓库；如需在实例立刻试跑，把同一批文件复制过去即可（不要提交实例侧）。

## 1. 需求（用户原话）

> 因为使用的是魔改包，现在需要为 MAA 加一个启动辅助的功能，因为是属于 alas 的功能，不能写在 MAA 里，而是写在 alas 里。完整的流程应该是进入游戏后，点击正上方的图标，开启辅助，隐藏辅助。每次进入游戏都应该执行一次。

即：每次进入游戏（游戏已在主界面）后自动执行一次：

```
点击顶部灵动岛（太阳图标） → 打开 Priestess 面板
   → 若有免责声明：下滑到底 → 点「我已阅读并完全同意以上声明」
   → 密码页：填密码 → 点「进入辅助」
   → 功能面板：点右上角齿轮 → 进入设置
   → 点「隐藏灵动岛(点击原位置恢复)」 → 悬浮 UI 消失，完成
```

目的：让魔改辅助加载生效，并把悬浮窗藏起来，避免挡住 MAA 的点击。

## 2. 素材

`doc/assist-shots/` 下 7 张**已对齐到 1280x720** 的截图（按内容命名，原图 `doc/assist-shots/original/` 为未缩放的 ~1890x1050 窗口截图）：

| 文件 | 页面 |
|---|---|
| `main_clean.png` | 主界面，无面板 |
| `main_arrow.png` | 主界面 + 红色箭头，箭头指向顶部灵动岛里的**太阳图标** |
| `decl_top.png` | 声明页顶部（绿色标题「【最终免责与版权声明】」） |
| `decl_bottom.png` | 声明页底部，底部有「▶ 我已阅读并完全同意以上声明」按钮 |
| `password.png` | 密码页，「请输入密码: 」输入框 + 「▶ 进入辅助」按钮 |
| `panel.png` | 功能面板，「属性修改 / 全屏攻击 / 干员无敌 / 全图放置」，右上角齿轮 |
| `settings.png` | 设置页，含「▶ 隐藏灵动岛(点击原位置恢复)」 |

⚠️ 用户给的 7 张截图**文件名顺序与页面顺序不一致**（按时间戳排序无效），上表是逐张目视确认后的结果，直接采信。

## 3. 已完成的分析（请直接采信，不必重新测算；但模板需你自己验证相似度）

### 3.1 元素坐标（1280x720，格式 `(x1, y1, x2, y2)`）

| 用途 | 来源图 | 建议模板裁剪区 | 点击区域 |
|---|---|---|---|
| 太阳图标 sun | `main_arrow` | `(584,16,618,52)` | 灵动岛圆角条 `(572,14,706,52)` |
| 同意声明 agree | `decl_bottom` | `(428,565,700,627)` | `(422,561,760,631)` |
| 进入辅助 enter | `password` | `(435,238,575,300)` | `(422,234,760,305)` |
| 齿轮 gear | `panel` | `(839,26,884,66)` | 同模板区 |
| 隐藏灵动岛 hide | `settings` | `(425,427,700,479)` | `(419,425,760,481)` |
| 密码输入框 | `password` | 不需要模板 | `(435,124,755,190)` |
| 功能面板特征 panel | `panel` | `(430,163,620,196)`（「属性修改」文字行） | 仅作页面判据，不点击 |

### 3.2 实测匹配矩阵（cv2 灰度 `TM_CCOEFF_NORMED`，同源图裁剪）

| 模板 | 本页 | 跨页最高 |
|---|---|---|
| `assist_agree` | 1.000 | 0.574 |
| `assist_enter` | 1.000 | 0.676 |
| `assist_hide` | 1.000 | 0.611 |
| `assist_panel` | 1.000 | 0.653 |
| `assist_gear` | 1.000 | **settings 0.931、声明顶 0.856、密码页 0.834** |
| `assist_sun` | 主界面 1.000/0.989 | **面板页 0.975~0.997** |

### 3.3 三个必须处理的坑（这是本需求最难的部分）

1. **所有面板页右上角都有齿轮**，齿轮模板在设置页相似度 0.931、声明页 0.856、密码页 0.834 都能命中。
   ⇒ 点齿轮必须加**页面门控**：只有当 `assist_panel`（「属性修改」行，跨页仅 0.653）匹配时才允许点齿轮，否则会在声明页/密码页误点。
2. **太阳图标在面板打开时依然可见**（灵动岛悬浮在最上层，0.975~0.997）。
   ⇒ 不能用「太阳是否可见」判断主界面，也不能用它判断流程完成。完成判据应为：点击 hide 后，所有按钮模板均不匹配。
3. **声明页很长**，「我已阅读」按钮在页面底部，刚打开时不可见。
   ⇒ 面板已打开但无任何已知按钮时，向上滑动（`device.swipe((600,520),(600,300))`），最多 4 次，之后放弃并 warning。

### 3.4 挂载点

`submodule/AlasMaaBridge/maa.py` 的 `maa_startup()`：在 `AssistantHandler(...).startup()` 返回之后调用新流程（`startup()` 里的 `StartUp` 会阻塞到完成，返回时游戏已在主界面）。

- 环形依赖/重入：不需要额外锁，每次 `MaaStartup` 任务都会走这里。
- **任何异常只 `logger.warning` 并继续**，绝不能抛 `RequestHumanTakeover` 或中断 MAA 任务。
- 默认关闭（`Enable=false`），用户自己开。

### 3.5 密码输入（adb）

点击密码框 → `input keyevent 123`（光标移到末尾）→ `keyevent 67` × 30 删除旧值 → `input text <密码>`。
这样无论辅助是否记住旧密码都安全（不会追加）。密码配置留空 = 只清空不输入（信任辅助记住的密码）。adb 调用：`self.device.adb.shell(['input', 'text', pwd])`。

### 3.6 ALAS 设备 API 注意事项

- `device.screenshot()` 返回 **BGR** `np.ndarray`（1280x720）。
- `device.click(button)` **要求 Button 实例**（内部访问 `button.button` 与 `str(button)`）。传 tuple 会炸。写一个最小替身：
  ```python
  class ClickBox(object):
      def __init__(self, area, name):
          self.button = area      # (x1,y1,x2,y2)，内部 random_rectangle_point 取随机点
          self.name = name
      def __str__(self):
          return self.name
  ```
- `device.swipe(p1, p2)` 可以直接传 tuple。
- 首次使用前需要 `device.config = self.config`（MAA 插件的 config 就是 ALAS config 对象，已复用）。

## 4. 实施要求

### 4.1 需要新增/修改的文件

1. 新增 `submodule/AlasMaaBridge/module/handler/assist.py`：状态机主体（模板加载 + 匹配 + 点击 + 密码输入）。
2. 新增 `submodule/AlasMaaBridge/assets/assist/assist_{sun,agree,enter,gear,hide,panel}.png`：按 3.1 的坐标从 `doc/assist-shots/` 裁剪。
   资源路径用 `os.path.dirname(__file__)` 拼相对路径，不要依赖 ALAS 的 `assets/<lang>/` 机制。
3. 修改 `maa.py`：`maa_startup()` 末尾调用新流程（见 3.4）。
4. 修改 `module/config/argument/argument.yaml`：新增 `MaaAssist` 组（`Enable: false`、`Password: ""` + `valuetype: str`），放在 `MaaUpdate` 组之后。
5. 修改 `module/config/argument/task.yaml`：`Maa.tasks.Maa` 列表里在 `MaaUpdate` 后插入 `- MaaAssist`。
6. 运行 `submodule/AlasMaaBridge/module/config/config_updater.py` 重新生成 `args.json` / `menu.json` / `config_generated.py` / `config/template.maa.json`。
7. 四语言 `module/config/i18n/*.json` 补 `MaaAssist`（`_info.name` 组名、`_info.help` 说明、`Enable.name`、`Password.name` + `Password.help`）。
   ⚠️ 生成器 `generate_i18n` 用 `deep_get(old, default=key)`，旧值优先，手改不会被覆盖；但**新 key 必须先写进文件**，否则界面显示英文 key 名。

### 4.2 状态机建议（可直接采用）

```
每轮：screenshot → 按优先级 hide > agree > enter > gear(需 assist_panel 门控) 匹配
  → 命中则点击（ENTER_ASSIST 前先走密码流程）并 continue
  → 否则若 sun 匹配：首次点击打开面板；已打开过则上滑（最多 4 次）
  → 否则：hide 点过 → 完成；没点过 → 「未检测到灵动岛」跳过
超时 150s / 最多 40 轮兜底；命中 hide 后复核一次截图，无按钮即视为隐藏成功。
```

### 4.3 禁止

- 不要把这段逻辑塞进 MaaCore（不要新增 MAA 任务链、不要动 `Maa/resource`）。
- 不要在 `t()` / i18n 文案里出现未转义的花括号（`{` `}` 必须写成 `{{` `}}`，否则 webui `str.format()` 会 `KeyError`，项目已踩过这个坑）。
- 不要改 `module/webui/lang.py`、主模块 i18n 的 `{0}` 占位符。
- 不要 `git commit` / `push`，改动留在工作区。

## 5. 验收（逐条附实际输出）

1. **模板验证**：对 7 张截图做匹配矩阵，结果应与 3.2 表格基本一致（本页 ≥0.95，跨页按表）。
2. **离线状态机测试**（mock device：按点击推进页面，密码框点击不推进）：
   - 完整流程：`main_clean → decl_top → decl_bottom → password → panel → settings → main_clean`
     → 期望动作序列 `OPEN_ISLAND, SWIPE, AGREE, PASSWORD_BOX, ENTER, OPEN_SETTINGS, HIDE`
   - 声明已同意过：`main_clean → password → panel → settings → main_clean` → `OPEN_ISLAND, PASSWORD_BOX, ENTER, OPEN_SETTINGS, HIDE`
   - 面板已打开：`panel → settings → main_clean` → `OPEN_SETTINGS, HIDE`
   - 面板停在设置页：`settings → main_clean` → `HIDE`
   - 无灵动岛（全黑图） → 无任何动作，直接跳过
   - 密码命令序列：`keyevent 123` + `keyevent 67`×30 + `input text <密码>`
3. **导入检查**：`PYTHONPATH=. toolkit/python.exe -c "import submodule.AlasMaaBridge.maa"` 无报错；`GeneratedConfig.MaaAssist_Enable` 默认为 `False`。
4. **真机**：开启 `MaaAssist.Enable` 后跑一次 `MaaStartup`，日志应出现
   `点击灵动岛打开面板` → （`声明页向下滚动` 可出现 0~4 次）→ `AGREE` → `输入密码` → `ENTER_ASSIST` → `OPEN_SETTINGS` → `HIDE_ISLAND` → `灵动岛已隐藏，辅助设置完成`。
   若某步 `score=` 在 0.7~0.8 徘徊说明模板与真实截图有缩放差异，此时**优先**用模拟器原生 1280x720 截图重裁模板，其次才考虑下调阈值（不要一上来就把阈值降到 0.7 以下）。

## 6. 交付物

1. `module/handler/assist.py` + `assets/assist/*.png`
2. `maa.py`、`argument.yaml`、`task.yaml` 的 diff
3. 重新生成的 `args.json` / `config_generated.py` / `menu.json` / `config/template.maa.json` 与四语言 i18n
4. `git status` — 注意 `submodule/AlasMaaBridge/assets/` 是新增目录，确认 6 个 png 全部进入版本控制
5. 5 行以内说明：实现方式、挂载点、验证结果、真机日志片段
