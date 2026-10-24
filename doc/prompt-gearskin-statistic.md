# 工单：活动外观装备统计（EventGeneral.GearSkinStatistic）

> 交付对象：编码 Agent。本文自包含，无需其他上下文。
> 所有游戏内坐标均为 **1280x720 原图像素**，2026-10-25 在 MuMu12（127.0.0.1:16384）CN 服实测所得。
> 实测截图存于 `doc/gearskin-shots/`（全部为 1280x720 原图，未缩放）。

## 0. 硬约束（不可妥协）

- **禁止运行游戏、模拟器、ALAS 主程序、alas.py、任何 adb/uiautomator 命令。本工单的所有游戏侧事实已由人工实测验证，见第 1、2 节，不需要也不允许再实例化验证。**
- **禁止 `git commit` / `git push`**，改动留在工作区。
- 只允许只读查证（Read / Grep / Glob）+ 静态自查（py_compile、JSON 解析、键数对比）。
- 允许运行的唯一动态命令：`python -m module.config.config_updater`（配置生成器，见 R1）与 `python -m py_compile <files>`。
- 新增文件只能放在 `module/gear_skin/`，禁止写入 `module/maa/`、`submodule/`、`toolkit/`。
- 统计功能任何失败**不得中断 StorageScan（仓库扫描）主流程**（只 warning + 跳过，`task_delay` 仍按仓库扫描自身结果执行）。
- **不得改动 `module/shop_event/` 下任何文件的任何逻辑**（允许 import 复用）；不得改动 `module/storage/scanner.py` 的扫描算法、白名单、`task_delay` 语义与 `config.py:308-311` 的 StorageScan 调度特判。
- 不新增调度任务、不改 `menu.json` / `task.yaml`。

## 1. 需求与已验证事实

### 1.1 需求

1. 在 `活动 → 活动通用设置`（配置组 `EventGeneral`）新增开关 `GearSkinStatistic`（默认关）。
2. 开关开启后，**在 StorageScan（仓库扫描）任务运行时顺带执行**（与仓库扫描同一时间、同一任务，本质是数据统计，只是入口配置放在活动组）：进入 商店 → 补给道具 → 活动商店，打开当期**外观装备箱**的「信息」弹窗，记录箱内每种外观装备的名称与拥有数量，落盘到与仓库扫描相同的输出目录。
3. **非活动期间即使开关开启也不得运行**：三级守卫见 R5，任何一级不通过就记日志跳过。
4. `工具 → 数据统计` 页面新增区块展示最近一次统计（名称/数量/较上次变化）与历史。

### 1.2 实测游戏流程（已人工逐步验证，截图佐证）

| 步骤 | 操作 | 佐证截图 |
|---|---|---|
| 1 | 主界面 → 商店 | `01_shop.png`（`MAIN_GOTO_SHOP`，module/ui/assets.py:85，cn area (11,662,165,713)） |
| 2 | 商店左侧栏点「补给道具」→ 子导航「活动商店」 | `09_supply.png`、`10_event_shop.png`。子导航「活动商店」即现有 `NAV_EVENT`（module/shop/assets.py:11，cn area (52,537,127,558)），实测仍命中；`ShopUI.ui_goto_shop()`（module/shop/ui.py:119）可从任意页到达该页 |
| 3 | 活动商店第一页即见 6 件外观装备（直接出售）+ 2 个外观装备箱 | `10_event_shop.png`、`40_now.png` |
| 4 | 点「外观装备箱(xxx)」→ 弹「兑换」弹窗（含物品卡、查看详情按钮、兑换数量、取消/兑换） | `41_box1.png`（光暗夜想曲）、`30_box2_popup.png`（深海寻宝） |
| 5 | 点弹窗内「查看详情」→ 弹「信息 INFORMATION」弹窗："可能获得以下外观装备"，网格列出每个外观装备：图标 + 名称 + `拥有:N`，**需向下滚动才能看全**；**弹窗初始滚动位置不固定**（`22_box_info.png` 开在顶部，`42_box1_info.png` 开在底部） | `22_box_info.png`、`42~52_*.png`（箱1 全程）、`31~33_box2*.png`（箱2） |
| 6 | 关闭：信息弹窗点「确定」或右上角 X → 回到兑换弹窗 → 点「取消」回商店 | `53_ok.png`、`54_cancel.png` |

### 1.3 关键判定事实（避免重新踩坑）

1. **SkinBox 识别已有现成规则**：`module/shop_event/item.py:166-168` `correct_name_and_cost()` 把 `price == 2000 and total_count == 10` 的活动商店物品命名为 `SkinBox`。实测两个外观装备箱恰好都是 价格 2000、限购 10/10。→ 统计模块从活动商店物品扫描结果里取 `name == 'SkinBox'` 的物品即可，**不要自己重新识别箱子**。
2. **外观装备（装备类）的兑换弹窗不显示拥有数**（弹窗只有「查看详情」按钮）；堆叠类物品（如心智单元）的兑换弹窗才直接显示 `拥有:66323`（`28_box2.png`）。→ 外观装备的拥有数量**唯一来源**是外观装备箱的「信息」弹窗，这正是本功能要走箱子弹窗的原因。
3. **物品图标右下角的 "1" 角标 = 当前兑换数量，不是拥有数量**（`13_skin_detail2.png` vs `icon_owned.png`/`icon_notowned.png`）。
4. **船类外观（如铁角号 8000 价）的兑换弹窗是另一种形态**（显示"图鉴未解锁"，无查看详情，`11_skin_detail.png`）。SkinBox 永远是 2000/10 形态，不受影响，但点击后等待「查看详情」出现时要做超时保护。
5. **本仓库截图预览缩放比例不固定（640/720/931/962/1001/1053/1080 宽都出现过）**。本工单所有坐标都已换算为 1280x720 原图像素并以 PIL 1:1 裁剪复核过（`popup_full.png`、`info_rows_1x.png`、`btn_detail_zoom3x.png`、`btn_cancel_1x.png`）。实现时若需重新目测，必须用 toolkit Python + PIL 在原图上量，禁止用聊天预览图目测。
6. **箱1（外观装备箱(光暗夜想曲)）完整清单已实测验证（18 个槽位，5+5+5+3，四帧图标重叠串接无遗漏）**，作为验收基准：

   | 行 | 物品（拥有数） |
   |---|---|
   | 1 | 圣诞双羽子弹(2) 纯白昙花子弹(4) 猫纹宝石子弹(1) 激光飞矢鱼雷(3) 缝袋魔爪鱼雷(2) |
   | 2 | 羽柔无锋鱼雷(**0**) 祝福天使侏地鸮(1) 霉运窥眸枭鸮(**0**) 露丝蛛网炮弹(1) 星坪隙影·飞机(3) |
   | 3 | 星坪隙影·飞机(2) 星坪隙影·飞机(3) 祈愿魔窗·飞机(3) 祈愿魔窗·飞机(3) 祈愿魔窗·飞机(2) |
   | 4 | 蚀火吊灯·飞机(3) 蚀火吊灯·飞机(2) 蚀火吊灯·飞机(3) |

   由此得出三条 OCR 必须处理的规则：**存在 `拥有:0`**；**同名外观装备占多格**（星坪隙影·飞机 3 格 = 2+3+3，祈愿魔窗·飞机 3 格 = 3+3+2，蚀火吊灯·飞机 3 格 = 3+2+3）；槽位数（18）≠ 种类数（12）。箱2（深海寻宝，幽灵船系列约 13 项）结构相同，见 `31~33_box2*.png`。
7. 当前活动（兑换开放时间 2026.10.22-2026.11.11）活动商店页顶部横幅即 `SHOP_EVENT_DEADLINE`（module/shop_event/assets.py:10，cn area (217,161,751,186)），现有 `OCR_EVENT_SHOP_DEADLINE` + `EventShopUI._get_event_deadline()`（module/shop_event/ui.py:71-88）可解析出截止日期 → 非活动守卫直接复用，见 R5。

## 2. 「信息」弹窗几何与 OCR 规则（1280x720 实测）

### 2.1 兑换弹窗（点 SkinBox 后出现）

| 元素 | 区域 (x1,y1,x2,y2) | 说明 |
|---|---|---|
| 查看详情按钮 | (391, 228, 485, 250) | 点击进入信息弹窗；用 OCR 区域文字「查看详情」确认出现，勿用固定延时盲点 |
| 物品卡标题（箱名，黄字） | (500, 100, 780, 130) | OCR 得箱子名称，如 `外观装备箱(光暗夜想曲)`；黄字 mean ≈ (232,200,90) |
| 取消按钮 | (402, 608, 565, 662) | 退出兑换弹窗，中心 ≈ (483, 635)，实测可点 |
| 兑换按钮 | ≈ (727, 608, 890, 662) | **禁止点击** |

### 2.2 「信息 INFORMATION」弹窗

| 元素 | 区域 | 说明 |
|---|---|---|
| 弹窗面板 | (338, 165, 945, 555) | 水平中心 640 |
| 标题栏「信息」 | (355, 170, 855, 215) | 可作为弹窗出现判据（OCR `信息`） |
| 右上角 X | (865, 170, 925, 217) | 关闭信息弹窗 |
| 副标题「可能获得以下外观装备」 | y 228-248 | 可选判据 |
| 列表视口 | (347, 252, 935, 470) | 网格滚动区域 |
| 滚动条 | x ≈ 912-922，y 252-470 | 黄色；位置可辅助判底，但不强依赖 |
| 确定按钮 | (555, 485, 730, 540) | 关闭信息弹窗，实测可点 |

网格（首屏、滚动置顶时）：

- 5 列，图标左缘 x = **382, 490, 598, 705, 812**（列距 ≈ 107.5，图标 ≈ 72px）。
- 第 1 行：稀有度粉横幅顶 y=258，名称文字带 **y 356-374**（白字 mean ≈ (222,222,219)），`拥有:N` 条带 **y 373-392**（金字 mean ≈ (202,180,65)，条带为半透明深色、颜色随背景变化，**不要用固定底色检测条带**）。
- 行距 = **150px**（第 2 行名称带 y 506-524、条带 y 523-542，超出视口需滚动）。

建议的常量承载（直接可用，勿只留表格）：

```python
INFO_LIST_AREA = (347, 252, 935, 470)
INFO_TITLE_AREA = (355, 170, 855, 215)
INFO_CONFIRM = (555, 485, 730, 540)
INFO_CLOSE = (865, 170, 925, 217)
EXCHANGE_DETAIL_BUTTON = (391, 228, 485, 250)
EXCHANGE_TITLE_AREA = (500, 100, 780, 130)
EXCHANGE_CANCEL = (402, 608, 565, 662)
INFO_SCROLL_FROM = (640, 440)
INFO_SCROLL_TO = (640, 280)
```

### 2.3 信息弹窗 OCR 算法（每屏）

1. **先回顶**：弹窗初始滚动位置不固定（见 1.3.5），进入后先连续执行 2~3 次"向上滑动"（`INFO_SCROLL_TO 方向`：从 (640, 280) 滑到 (640, 440)），画面不再变化即到顶；也可对照滚动条位置确认，但不强依赖。
2. 对 `INFO_LIST_AREA` 整块做一次 `Ocr(lang='cnocr')` 全区域 OCR（返回带 box 的结果）。
3. 用正则 `拥有[:：]?\s*(\d+)` 挑出所有「拥有」条目（数量）。
4. 每个拥有条目：在其**上方 80px 内、水平重叠 ≥50%** 的文本框中取最近者作为该外观装备名称；找不到配对时丢弃该条目并 `logger.warning`（禁止用索引顺序硬配对，多列布局下顺序不可靠）。
5. **每帧产出"槽位对"列表 `[(name, amount), ...]`（同一帧内同名出现几次就记几次，不做帧内去重）**；跨帧累积后对每个 `(name, amount)` 组合取**各帧中出现次数的最大值**作为该组合的槽位数（不同帧看到的行数不同，行完整可见时计数才最大）。
6. **按名称合并**：最终每名称数量 = 该名称所有 `(amount × 槽位数)` 之和（同名多格是不同皮肤共享显示名，实测星坪隙影·飞机 = 2+3+3 = 8）。`拥有:0` 的装备也要记录（代表未获得，种类数 kinds 只数 amount>0 的名称）。
7. 向上滑动 `INFO_SCROLL_FROM → INFO_SCROLL_TO`，再次 OCR；**连续 2 次没有新增 (name, amount) 组合**或滑动达 12 次即认为到底。禁止依赖滚动条颜色判底。
8. 名称 OCR 是 13px 小字，若识别率差，先对该区域 2x 放大再 OCR（`cv2.resize` 或 `PIL.Image.resize`，LANCZOS）。
9. **验收基准**：按 1.3.6 的清单，箱1 合并后应为 12 个名称、18 个槽位、总拥有数 38、kinds=10。实现后用静态推演（把算法套在 `42~52_*.png` 截图的预期 OCR 结果上走一遍伪代码）自证能得出该结果，写入交付说明。

## 3. 实现步骤

### R1 配置项（必须）

1. `module/config/argument/argument.yaml` 的 `EventGeneral` 组（现约 :291-293）追加一行：`GearSkinStatistic: false`。
2. 在仓库根运行 `python -m module.config.config_updater`（会 chdir 到仓库根；会重写 `module/config/argument/args.json`、`module/config/config_generated.py`、`config/template.json`、四个 `module/config/i18n/*.json`）。**跑完必须逐文件 diff**：除新增 `GearSkinStatistic` 相关键外不得有其他变化；若有，回滚该文件。
   - 若生成器在本环境无法运行（缺依赖等），改为手工同步四处：`args.json` 的 `EventGeneral` 加 `{"GearSkinStatistic": {"type": "checkbox", "value": false}}`；`config/template.json` 的 `EventGeneral` 加 `"GearSkinStatistic": false`；`config_generated.py:161-163` 区加 `EventGeneral_GearSkinStatistic = False`；四个 i18n 手工加键。并在交付说明里注明用了手工方式。
3. 四语言 i18n（在 `EventGeneral.TimeLimit.*` 之后追加）：
   - zh-CN：`name = 外观装备统计`，`help = 开启后，随仓库扫描(StorageScan)任务一并执行：进入活动商店打开外观装备箱详情，统计当期各外观装备的拥有数量，并与仓库扫描记录同一时间落盘到 工具-数据统计 页面\n非活动期间自动跳过；需要活动商店内存在外观装备箱（价格 2000、限购 10 的物品）`
   - zh-TW：`外觀裝備統計`（help 繁体同义）
   - en-US：`Gear Skin Statistics`（help 同义英文）
   - ja-JP：`外観装備統計`（help 同义日文）
4. i18n 文案中的字面花括号必须写成 `{{`/`}}`（`module/webui/lang.py:37` 的 `t()` 走 `str.format`，历史事故见 config i18n `MaaOperProgress.Plans.help`）。

### R2 统计模块（必须）

新文件 `module/gear_skin/__init__.py`、`module/gear_skin/gear_skin.py`、`module/gear_skin/assets.py`（本工单的弹窗几何常量放这里）。

- 类 `GearSkinScanner`，建议 `class GearSkinScanner(EventShopClerk, ShopUI)`（`module/shop_event/clerk.py:26` 的 `EventShopClerk` 提供 `event_shop_get_items` / `EVENT_SHOP_SCROLL` / `handle_obstruct`；`module/shop/ui.py` 的 `ShopUI` 提供 `ui_goto_shop`；二者同源于 `UI`，MRO 无冲突。若采用其他组合方式，交付说明里写清并保证不改变被复用类的行为）。
- 入口 `run(self, scan_time: str)`，参数为仓库扫描记录的时间戳字符串（见 R3），流程：
  1. **守卫一（R5）→ 守卫二（R5）**，任一不通过即返回。
  2. `ui_goto_shop()` 到 page_munitions → 点 `NAV_EVENT` 进活动商店（`Timer(3, count=6)` 等待出现；活动商店页加载确认可复用 `event_shop_load_ensure()` 的判据思路，但超时**不得 raise**，改为 warning + 跳过）。
  3. 遍历商店 tab（参照 `module/shop_event/shop_event.py` run() 的 tab 循环与 `event_shop_tab_count_and_navbar`；当前活动仅 1 tab，但实现必须兼容多 tab）：每 tab 用 `event_shop_get_items()`（沿 `scan_all()` 的滚动去重思路，见 clerk.py:99-125）拿全部物品，取 `name == 'SkinBox'` 的物品；为空则该 tab 跳过。
  4. 对每个 SkinBox：`EVENT_SHOP_SCROLL.set(item.scroll_pos, main=self)` → `event_shop_get_items()` 重新定位同名同价物品 → 点击其 button → 等待「查看详情」出现（OCR `EXCHANGE_DETAIL_BUTTON` 区域含"查看详情"，`Timer(3, count=6)`，超时 warning + 存截图 `log/storage_scan/err/` + 跳过该箱）→ 点击查看详情 → 等待信息弹窗（OCR `INFO_TITLE_AREA` 含"信息"）→ **先回顶**（2.3.1）→ 按 2.3 采集 → OCR 箱名（`EXCHANGE_TITLE_AREA` 需在兑换弹窗阶段先 OCR 并缓存，信息弹窗会盖住它）→ `INFO_CONFIRM` 或 `INFO_CLOSE` 关闭 → 等 `EXCHANGE_CANCEL` → 点击取消 → `handle_obstruct` 清场。
  5. 每箱产出 `{"box": <箱名>, "skins": [{"name": ..., "amount": ...}, ...]}`。
- 全流程 try/except Exception：失败 `logger.warning` 并返回已收集部分，**绝不 raise**。
- 点击物品前后禁止触碰 `SHOP_BUY_CONFIRM` / `兑换` 按钮；任何等待分支里若发现 `SHOP_BUY_CONFIRM_AMOUNT`/`SHOP_BUY_CONFIRM` 出现（说明误入了购买流），立即 warning 并点 `SHOP_CLICK_SAFE_AREA` + `EXCHANGE_CANCEL` 退出。
- 记录落盘（同文件或 `module/gear_skin/records.py`）：写 **`log/storage_scan/<config_name>/gear_skin.json`**（与仓库扫描 records.json 同目录，`module/storage/scanner.py:16` 的 `OUTPUT_FOLDER = 'log/storage_scan'`），结构：

```python
# {
#   "records": [
#     {
#       "time": "2026-10-25 04:30:00",   # 与当次仓库扫描记录完全相同的时间串（R3 传入）
#       "boxes": [
#         {"box": "外观装备箱(光暗夜想曲)",
#          "skins": [{"name": "圣诞双羽子弹", "amount": 2}, ...]},
#         ...
#       ],
#       "slot_count": 18,  # 槽位数（同名多格分别计数）
#       "total": 38,       # 所有 skins amount 之和
#       "kinds": 10        # amount > 0 的名称种类数
#     }, ...
#   ]
# }
```

  - 读改写需 try/except（文件损坏时备份为 `.bak` 后重建，参照 `module/statistics/statistics.py:105-135` 的防御式读法）；写入用 `deploy.atomic.atomic_write`。
  - 保留最近 90 条记录，超出裁剪。

### R3 挂载点（必须）

`module/storage/scanner.py` 的 `MaterialScanner`：

1. `save_records()`（scanner.py:581）加可选参数 `time: str = None`，为 None 时保持现状 `datetime.now().isoformat(timespec='seconds')`，行为不变。
2. `run()`（scanner.py:613）改为：`scan_time = datetime.now().isoformat(timespec='seconds')` → `self.save_records(time=scan_time)` → 其后追加：

```python
if self.config.EventGeneral_GearSkinStatistic:
    try:
        from module.gear_skin.gear_skin import GearSkinScanner
        result = GearSkinScanner(self.config, self.device).run(scan_time=scan_time)
        if result:
            logger.hr('Gear skin statistics', level=2)
            logger.info(f'Gear skin statistic saved, see {OUTPUT_FOLDER}/{self.config.config_name}/gear_skin.json')
    except Exception as e:
        logger.warning(f'GearSkinStatistic failed, storage scan result unaffected: {e}')
```

3. `task_delay` 语义完全不变：统计失败不影响 `task_delay(success=True)` 已有路径（统计代码放在成功路径内、且自身吞异常）；仓库扫描失败路径（scanner.py:631-634）不执行统计。
4. 语义说明（写入交付说明）：统计与仓库扫描同一任务、同一时间戳，记录的是扫描时刻的拥有状态。

### R4 数据统计页面（必须）

`module/webui/app.py` `alas_statistics()`（:576-686）：

- 仿照 `alas_storage_scan()`（:688-792）新增渲染 `alas_gear_skin()`：读 **`log/storage_scan/<config_name>/gear_skin.json`**，在 storage scan 区块之后调用。
- 最新一条：按箱分组的表格 `名称 | 拥有 | 较上次`（变化 = 与上一条记录同箱同名 amount 差，`+N` 绿 / `-N` 红 / `-` 无变化；颜色参照 alas_storage_scan 的 `#4caf50`/`#ef5350`，沿用现状）。表格标题含统计时间与 `total`/`kinds`。
- 历史折叠块（`put_collapse`，默认收起，最近 7 条，每条一行摘要：时间 + total + kinds + 各箱 total）。
- 无文件/无数据时整块不渲染（不要显示"暂无数据"，与 storage scan 一致）。
- i18n 新键（四语言，`Gui.Statistics` 组）：`GearSkin`（外观装备统计）、`GearSkinLatest`（最新统计）、`GearSkinBox`（箱）、`GearSkinAmount`（拥有）、`GearSkinChange`（较上次）、`GearSkinKinds`（已获得种类）、`GearSkinTotal`（拥有总数）、`GearSkinHistory`（统计历史）。注意花括号转义规则同 R1.4。

### R5 非活动守卫（必须，三级任一不通过即跳过）

1. **守卫一（导航层）**：`ui_goto_shop()` 后在 `Timer(3, count=6)` 内 `self.appear(NAV_EVENT)` 不成立 → `logger.info('No event shop nav, skip gear skin statistics')` → 返回。
2. **守卫二（时间层）**：进入活动商店页后，复用 `EventShopUI._get_event_deadline()`（OCR `SHOP_EVENT_DEADLINE`）取截止日期；**OCR 失败（返回 None）或 `deadline <= server_now`（`datetime.now() - server_time_offset()`，`module/config/utils.py`）都视为非活动期间** → info 日志 → 返回。注意不要复用 `is_event_ended`（它绑定了 `EVENT_SHOP_IGNORE_DEADLINE` 语义且 "-7 天" 的判定与统计无关）。
3. **守卫三（内容层）**：某 tab 扫描后无 `SkinBox` 物品 → 该 tab 跳过；全部 tab 均无 → info 日志 → 返回（不写记录文件，避免产生空记录）。
4. 无法实测"活动结束后 NAV_EVENT 是否消失/活动商店页是否存在"，故三级守卫必须全部实现、相互兜底；守卫分支一律 info/warning 日志，禁止 raise。

### R6 明确不改的东西（非问题清单）

- `module/shop_event/` 全目录（item.py 的 SkinBox 映射、selector.py、clerk.py、ui.py、shop_event.py）**零改动**，仅 import。即使未来某活动外观装备箱价格不同，也只允许在交付说明中提示。
- `module/storage/scanner.py` 的扫描算法（`scan_material`/`_scan_pass`/`get_records`）、WHITELIST、`OUTPUT_FOLDER` 值、`task_delay` 语义全部不动（仅 R3 列出的两处最小改动）。
- `DailyIncome`（module/statistics/statistics.py）与本功能无关，不并入（外观装备不是可累加的货币资源，正增量算法不适用）。
- 不新增独立调度任务：触发载体就是 StorageScan 任务（用户明确要求与仓库扫描同一时间）。不往 `menu.json`/`task.yaml`/`SCHEDULER_PRIORITY` 加任何新任务。
- `doc/gearskin-shots/` 下所有截图与 `explore.py` 是实测素材，保留，不清理不引用进运行时。

## 4. 验证边界

4.1 **允许**：Read/Grep/Glob 任意文件；`python -m py_compile`；`python -c "import json; ..."` 校验 JSON；对 i18n 四文件做键集合 diff；运行 `python -m module.config.config_updater` 并 diff 产物。
4.2 **禁止**：启动 ALAS/GUI；连接 adb/模拟器；运行 alas.py / gui.py / 任何 pytest 用例；安装依赖；修改 `doc/gearskin-shots/`。
4.3 需要真机确认的，全部移入第 5 节人工验证清单，交付说明中列明。

## 5. 人工验证清单（由用户/人工执行）

1. GUI 配置页：活动 → 活动通用设置 出现「外观装备统计」开关，四语言文案正常（重启 Alas 后检查，i18n 是启动时加载）。
2. 开启开关，手动触发 StorageScan 任务：仓库扫描正常完成后，日志出现 Gear skin 相关 hr/信息日志；活动商店内自动打开两个箱子的信息弹窗并滚动，全程无购买行为（无 SHOP_BUY_CONFIRM 点击记录）。
3. 统计完成后 `log/storage_scan/<实例名>/gear_skin.json` 生成，且 `time` 与同目录 records.json 最新一条的 `time` **完全一致**。
4. 数值核对（对照 1.3.6 基准）：箱1 = 12 名称 / 18 槽位 / total=38 / kinds=10；箱2 按截图核对逐项名称与数量。
5. 工具 → 数据统计页面出现「外观装备统计」区块，表格数值与 gear_skin.json 一致；触发第二次统计后"较上次"列出现变化值。
6. 关闭开关后 StorageScan 行为与改动前一致（不进商店）。
7. 非活动期间（或活动无外观装备箱）运行：三级守卫任一触发，只 info/warning 跳过，仓库扫描正常完成。

## 6. 验收标准（静态可核对）

1. `python -m py_compile` 通过：`module/gear_skin/*.py`、`module/storage/scanner.py`、`module/webui/app.py`、`module/config/config_generated.py`。
2. `args.json`/`template.json`/`config_generated.py` 均含 `GearSkinStatistic`（bool，默认 false）；四个 i18n 的键数在改动前后各自一致（新增键四语言同步，无缺键），且新 help 文案无未转义 `{`/`}`。
3. `git diff` 不包含对 `module/shop_event/` 任何文件、`module/storage/assets.py`、`module/config/config.py` 的改动；不包含新增任务/菜单项。
4. `GearSkinScanner.run` 最外层 try/except 覆盖，任何异常不逃逸到 `MaterialScanner.run`。
5. 代码中出现的所有游戏坐标与第 2 节常量表一致（或集中定义于 `module/gear_skin/assets.py`）。
6. 交付说明含：2.3.9 的算法静态推演结果（须得出箱1 = 12名称/18槽位/total 38/kinds 10）、配置生成器是否成功、人工验证清单的执行指引。

## 7. 交付物

1. `git diff`（工作区，未提交）。
2. 静态自查报告：逐条对应第 6 节验收标准的核对结果。
3. ≤5 行交付说明（挂载方式、生成器情况、遗留风险）。
4. 如素材缺失（如无法确认 i18n 现有键位），在说明中请示，不要猜。
