# 工单 v2：仓库扫描数量污染 + 外观装备统计未生效（增量修复）

> 交付对象：编码 Agent。本文自包含。
> 上版工单 `doc/prompt-storage-scan-fix.md` 的部分修复**已落地并有验证数据**，本工单只处理仍失效/新发现的 4 项，**不要重做已完成项**。
> 所有像素坐标均为 1280x720 原图像素。2026-10-25 05:20~05:35 于 MuMu12（127.0.0.1:16384）CN 服、实例 A 实测。

## 0. 硬约束

- **禁止启动 ALAS 主程序/GUI、禁止 `git commit`/`git push`**。改动留在工作区。
- 允许的动态验证只有两种：`python -m py_compile <files>`、运行 `dev_tools/storage_scan_replay.py`（离线回放，不连设备）。
- 改动范围：`module/storage/scanner.py`、`module/gear_skin/gear_skin.py`、`dev_tools/storage_scan_replay.py`。**禁止改动** `module/shop_event/**`（含 `shop_event.py` 的标准进入流程，只可 import 复用）、`module/shop/**`、`module/storage/{storage,ui,assets}.py`、`module/config/**`、`assets/**`。
- `save_records()` 的 records.json 结构不变；`gear_skin.json` 结构不变。

### 0.1 已落地项（本次不要再动，若被改动请还原）

| 项 | 落地形态 | 证据 |
|---|---|---|
| R1 II 像素判定 | `_has_ii_suffix()`（scanner.py:329-332）、`_resolve_prefix_name()`（:334-346） | 实例 A 本次扫描 `心智单元II 15929` 正确、无 `(金)` 键 |
| R2 页内不覆盖 | `scan_current_view` 的 `found` 改为列表（:546-568） | 同上 |
| R3 覆盖需 ≥2 票 | `_confirmed_names()`（:672-687） | 同上 |
| R6 离线回放 harness | `dev_tools/storage_scan_replay.py` | 文件存在 |
| display_name / QUALITY_SUFFIX | 保持现状（研发图按品质映射 SSR型/UR型是**正确的游戏内命名**） | `舰炮研发图SSR型 902`（金框）/ `舰炮研发图UR型 40`（紫框）游戏实拍并存 |

### 0.2 本次要修的 4 项

| 编号 | 问题 | 严重度 |
|---|---|---|
| Q1 | 外观装备统计开关读不到（跨任务组）→ 功能永不执行 | 致命（功能完全无输出） |
| Q2 | 外观装备统计导航守卫判否 → 即使开关为真也跳过 | 致命 |
| Q3 | 数量读取的模板匹配实现有误，**比原 OCR 更差**（7 格错 5 格） | 高（引入回归） |
| Q4 | 白名单缺「机密实验计划」→ 整条漏报（真值 960） | 中 |

---

## 1. 问题分析（全部实测）

### 1.1 Q1：开关跨任务组读取失败（已定量实证）

实例 A 的 `config/A.json` 里开关确实是开的：

```json
"EventGeneral": {"EventGeneral": {"PtLimit": 0, "TimeLimit": "2020-01-01 00:00:00", "GearSkinStatistic": true}}
```

但 `StorageScan` 任务在 `module/config/argument/task.yaml:377-378` 只绑定了 `Scheduler`：

```yaml
    StorageScan:
      - Scheduler
```

`MaterialScanner.run()`（scanner.py:843）用 `self.config.EventGeneral_GearSkinStatistic` 读这个**不属于当前绑定组**的键，AZL 返回默认值。实测（运行目录 `toolkit/python.exe`，只读，不连设备）：

```
getattr(c, 'EventGeneral_GearSkinStatistic')                                  -> False      # 错
c.cross_get(keys='EventGeneral.EventGeneral.GearSkinStatistic', default=False) -> True       # 对
```

运行日志佐证：`log/2026-10-25_A.txt` 全文检索 `gear`/`GearSkin`/`外观装备` **零命中**（仅出现仓库里的「外观装备箱」物品名），说明 `if self.config.EventGeneral_GearSkinStatistic:` 分支从未进入，连 `GearSkinStatistic failed` 的警告都没机会打印。

### 1.2 Q2：导航守卫用 appear(NAV_EVENT) 判存在性，恒为 False

绕过开关直接实例化 `GearSkinScanner(cfg).run(...)` 实测（05:28），日志：

```
Click (1133, 188) @ ACADEMY_GOTO_MUNITIONS
No event shop nav, skip gear skin statistics
==== RESULT ==== None
```

当时画面**确实是**「补给道具 → 补给商店」，左侧子导航清晰可见「活动商店」，且 `NAV_EVENT` 资产坐标 (52,537,127,558) 精确覆盖「活动商店」四字。问题出在颜色判定：

| 按钮 | 实测区域均值 | 定义 color | 逐通道差 | 判定 |
|---|---|---|---|---|
| `NAV_GENERAL`（补给商店，**选中态**）| (126,178,207) | (126,178,207) | (0,0,0) | True |
| `NAV_EVENT`（活动商店，**未选中态**）| (124,159,175) | (127,179,208) | (3,20,**33**) | **False** |

ALAS 的 Button 走区域比色，逐通道阈值 30；B 通道差 33 刚好超限。即：`NAV_EVENT.color` 记的是**选中态**颜色，未选中态必然判否 —— 所以「用它判断活动商店是否存在」从设计上就不成立。

对比 `module/shop_event/shop_event.py:248-266`（EventShop 任务的正确做法）：先用 `appear(NAV_GENERAL)` 确认在补给道具页，再用 `NO_NAV_EVENT_CHECK` 判断**活动商店是否存在**，最后 `ui_click(NAV_EVENT, check_button=NAV_EVENT, appear_button=NAV_GENERAL)` 进入（此时点击后变选中态，`check_button` 才匹配）。

### 1.3 Q3：已落地的模板匹配是负收益（实测 7 格错 5 格）

`read_amount()`（scanner.py:212-240）优先走 `_match_amount()` + `module/storage/amount_digits.json`（05:25 生成，10 个数字仅 1~2 个实例）。对冻结截图 `doc/storage-shots/35_f6.png` 第 3 行（`icon_top=399`）离线实测：

| 格子 | 真值（游戏内肉眼核对） | `read_amount` 输出 | 结果 |
|---|---|---|---|
| 舰炮研发图 | 902 | **90** | 末位丢失 |
| 鱼雷研发图 | 968 | 968 | OK |
| 防空炮研发图 | 933 | 933 | OK |
| 舰载机研发图 | 832 | **83** | 末位丢失 |
| 机密实验计划 | 960 | **969** | 末位错认 |
| 心智魔方 | 6595 | **659** | 末位丢失 |
| 喵箱SSR型 | 501 | **50** | 末位丢失 |

**5/7 错，且全部错在末位/末位字符**，属右缘锚定与模板错标问题（模板实例过少，`960` 被读成 `969` 说明 0/9 模板本身错标）。当前正确率反而低于未启用模板时的原 OCR（同期原 OCR 错 3/7）。**必须移除该路径，不要在它上面继续调参。**

### 1.4 仓库扫描数量污染：机理与已验证的修复

实例 A 两次扫描仅隔 2 分钟，同一格结果不同（`log/storage_scan/A/records.json`）：

| 时间 | 舰炮研发图SSR型 | 机密实验计划 |
|---|---|---|
| 05:16:08 | 902（正确） | 16（非该项，见 Q4） |
| 05:18:28 | **2902**（错） | 读成 **2960** 后被白名单丢弃 |

**抖动实验**（对 35_f6.png 同一页，把行锚点 `y1c` 从 -4 到 +4 平移，其余不变）：

```
cannon (真值902): -4:902 -3:902 -2:2902 -1:2902 0:902 +1:902 +2:902 +3:902 +4:902
aa     (真值933): -4:933 -3:933 -2:2933 -1:933  0:933 +1:933 +2:933 +3:933 +4:933
secret (真值960): -4:960 -3:960 -2:2960 -1:2960 0:2960 +1:960 +2:960 +3:960 +4:960
torp/plane/cube/box: 全部 9 个偏移均正确
```

结论：**污染不是随机 OCR，而是行锚点 ±2px 抖动把卡片的白底图案推进裁剪窗**，被当成一个前导字符（多为 `2`，故 902→2902、960→2960、883→2883）。

**窗口调参已证伪**（同一页 9 个偏移全跑，统计每种窗口的错误格数）：

| 裁窗 (y 起止, x 起止) | 错误格数 |
|---|---|
| 现状 y+96..+124, x+55..+123 | 3/7 |
| y+99..+122, x+55..+126 | 7/7 |
| y+101..+123, x+55..+126 | 7/7 |
| y+97..+124, x+52..+127 | 3/7 |
| y+99..+123, x+60..+127 | 7/7（且切掉数字） |

任何固定窗口都躲不开（图案会随偏移变形）。**唯一稳定且已验证的方案 = 多偏移取样 + 后缀/前缀关系判定**：

```
候选 = 对 dy ∈ {-3..+3} 各取一次 AmountOcr 结果（只保留正整数）
若只有一个候选 → 直接采用
若存在「短值是长值的后缀」（如 902 ⊂ 2902）→ 前导污染 → 取短值
若存在「短值是长值的前缀」（如 883 ⊂ 2883）→ 尾部截断 → 取长值
否则 → 取众数
```

实测（35_f6 + 16_p6 两页全格）：

| 格子 | 现状 dy=0 | 修复后 | 
|---|---|---|
| 机密实验计划 | 2960 | **960** ✔ |
| 氟橡胶 | 15925 | **5925** ✔ |
| 舰炮/鱼雷/防空/舰载/喵箱/心智魔方 | 全对 | 全对（无回归） ✔ |

即：**7/7 全对**，且修好了原 OCR 单独搞不定的 `15925→5925`。

### 1.5 Q4：白名单缺「机密实验计划」

游戏内实拍 `doc/storage-shots/33_f4.png` / `35_f6.png` 确认两者**并存且是不同物品**：

- 「绝密实验计划」= **16**（红章图案，第 1 页可见）
- 「机密实验计划」= **960**（第 3 行第 5 格，与 SSR 研发图同行）

`WHITELIST`（scanner.py:19-26）只有 `'绝密实验计划'`，因此 `机密实验计划` 每次都被 `match_whitelist` 丢弃，输出恒缺一条（本次 21 条记录里没有它；`2960` 也没进 `page found`）。

---

## 2. 问题点清单

| 编号 | 根因 | 位置 | 处置 |
|---|---|---|---|
| Q1 | 跨任务组读开关，`getattr` 恒 False | `module/storage/scanner.py:843` | 改 `cross_get` |
| Q2 | `appear(NAV_EVENT)` 判存在性，未选中态色差 33>30 恒 False | `module/gear_skin/gear_skin.py:116-129` | 复用 EventShop 标准流程 |
| Q3 | 模板匹配右缘/模板错标，末位系统性丢失 | `scanner.py:212-240`、`amount_digits.json` | 移除该路径，改多偏移+后缀规则 |
| Q4 | `_merge_amounts` 只把短值票并给长值，前导污染时反而巩固错值 | `scanner.py:630-652` | 按前/后缀方向分派 |
| Q5 | 白名单缺 `机密实验计划` | `scanner.py:19-26` | 补入（18 项） |

非问题（明确不改）：`shop_event/**` 标准流程、`detect_name_bars`/`cell_bar_top` 主体、滑动参数、`save_records`/`task_delay`、`display_name` 的研发图品质映射、webui 展示层。

---

## 3. 实现步骤

### R1（Q1）开关改跨组读取 —— 1 行

`module/storage/scanner.py:843`：

```python
# 旧：StorageScan 任务不绑定 EventGeneral 组，getattr 恒返回默认 False
# if self.config.EventGeneral_GearSkinStatistic:
if self.config.cross_get(keys='EventGeneral.EventGeneral.GearSkinStatistic', default=False):
```

不要改 `task.yaml` 给 StorageScan 绑 EventGeneral（会污染任务配置面板），`cross_get` 已是 AZL 标准跨组读法。

### R2（Q2）导航守卫复用 EventShop 标准流程

重写 `module/gear_skin/gear_skin.py` 的 `enter_event_shop()` 前半段（守卫一），保留其后的截止时间 OCR 逻辑（守卫二）：

```python
from module.ui.page import page_shop, page_munitions
from module.shop.assets import SHOP_GOTO_MUNITIONS, NAV_GENERAL, NAV_EVENT
from module.shop_event.assets import NO_NAV_EVENT_CHECK

    def enter_event_shop(self):
        self.ui_goto_shop()
        # 与 EventShop 任务一致：先确认在补给道具页
        timeout = Timer(2, count=4)
        for _ in self.loop():
            if self.appear(page_munitions.check_button, threshold=20):
                break
            if timeout.reached():
                self.device.click(SHOP_GOTO_MUNITIONS)
                timeout.reset()
        if not self.appear(NAV_GENERAL, offset=(5, 5)):
            logger.info('Not in munitions page, skip gear skin statistics')
            return False
        # 活动商店是否存在只能靠 NO_NAV_EVENT_CHECK 判断；
        # NAV_EVENT 的 color 是选中态颜色（B 通道差 33 > 阈值 30），
        # 未选中态 appear 恒 False，不能用于存在性判断
        if self.appear(NO_NAV_EVENT_CHECK, offset=(5, 5)):
            logger.info('No event shop currently, skip gear skin statistics')
            return False
        self.ui_click(NAV_EVENT, check_button=NAV_EVENT, appear_button=NAV_GENERAL)
        # ... 原守卫二（OCR SHOP_EVENT_DEADLINE 截止时间）保持不变 ...
```

要点：`ui_click` 的 `check_button=NAV_EVENT` 在点击后是**选中态**才能匹配，这正是 NAV_EVENT 定义色的用途；不能用它做点击前的存在性判断。

### R3（Q3+Q4）移除模板匹配，改多偏移取样 + 后缀/前缀判定

1. **删除** `read_amount()` 里的模板分支、`_match_amount()`、`digit_templates()`、`self._templates` 初始化，并删除资产 `module/storage/amount_digits.json`（未跟踪文件，直接删）。
2. 新的 `read_amount()`：

```python
    AMOUNT_DY = (-3, -2, -1, 0, 1, 2, 3)

    def read_amount(self, image, x, icon_top):
        """
        多偏移取样后按前/后缀关系消歧。

        行锚点 ±2px 抖动会把卡面白底图案推进数量裁剪窗，被读成前导
        多余数字（实测 902->2902、960->2960、933->2933），任何固定
        裁剪窗都躲不开（9 个偏移全跑，5 种窗全失败）。而污染恒为
        "前导字符 + 真值"，故对多个偏移的读数取后缀关系判定。
        """
        area = self.AMOUNT_AREA
        candidates = []
        for dy in self.AMOUNT_DY:
            top = icon_top + dy
            crop = image[top + area[1]:top + area[3], x + area[0]:x + area[2]]
            if crop.shape[0] != area[3] - area[1]:
                continue
            try:
                value = int(self.ocr_amount.ocr([crop], direct_ocr=True)[0])
            except (ValueError, TypeError):
                continue
            if value > 0:
                candidates.append(value)
        return self._resolve_amount(candidates)

    @staticmethod
    def _resolve_amount(candidates):
        if not candidates:
            return 0
        uniq = sorted(set(candidates), key=lambda v: len(str(v)))
        if len(uniq) == 1:
            return uniq[0]
        # 前导污染：短值是长值的后缀 -> 短值是真值
        for short in uniq:
            for long in uniq:
                if len(str(long)) > len(str(short)) and str(long).endswith(str(short)):
                    return short
        # 尾部截断：短值是长值的前缀 -> 长值是真值
        for long in uniq:
            for short in uniq:
                if len(str(long)) > len(str(short)) and str(long).startswith(str(short)):
                    return long
        return Counter(candidates).most_common(1)[0][0]
```

3. **修正跨页合并方向** `_merge_amounts()`（scanner.py:630-652）：现实现只有"短值票并给长值"，把前导污染值越并越牢。改为按关系分派：

```python
    @staticmethod
    def _merge_amounts(amounts):
        merged = dict(amounts)
        for short in sorted(list(merged), key=lambda v: len(str(v))):
            if short <= 0 or short not in merged:
                continue
            for long in list(merged):
                if long == short or long not in merged or len(str(long)) <= len(str(short)):
                    continue
                if str(long).endswith(str(short)):      # 前导污染 -> 短值是真值
                    merged[short] += merged.pop(long)
                    break
                if str(long).startswith(str(short)):    # 尾部截断 -> 长值是真值
                    merged[long] += merged.pop(short)
                    break
        return merged
```

4. 需要 `from collections import Counter`。

### R4（Q5）白名单补一项

`scanner.py:19-26` 的 `WHITELIST` 在 `'绝密实验计划'` 后加 `'机密实验计划'`（共 18 项）。已确认：`normalize()` 的 `戈→划` 归一生效，`机密实验计戈` 能精确匹配；两者无公共前缀，不会互相抢匹配；`display_name()` 无特判，输出名即 `机密实验计划`。
注意白名单从 17 变 18 后，`covered N/18` 的早停阈值自动跟随 `len(WHITELIST)`，无需另改。

### R5 回放 harness 补断言

在 `dev_tools/storage_scan_replay.py` 增加：

1. **污染专项**（本次核心）：`35_f6.png` 第 2 行 氟橡胶格 → 断言 `5925`；`16_p6.png`(x=776) 与 `35_f6.png`(x=776) 的机密实验计划格 → 断言 `960`；`902 / 968 / 933 / 832 / 6595 / 501` 七格全对。
2. **抖动鲁棒性**：对上述格，`icon_top` 取 `-3..+3` 共 7 个偏移，断言 `read_amount` 返回值**恒等于真值**（这是回归防线，模板匹配方案在此断言下会大批失败）。
3. **白名单**：断言 `len(WHITELIST) == 18` 且 `match_whitelist('机密实验计戈') == '机密实验计划'`。
4. **合并方向**：`_merge_amounts({902: 3, 2902: 1})` → `{902: 4}`；`_merge_amounts({883: 2, 2883: 1})` → `{2883: 3}`。
5. **导航守卫**：离线无法验证（需设备），改为静态断言 —— 源码中 `gear_skin.py` 的 `enter_event_shop` 不得出现 `appear(NAV_EVENT` 作为存在性判断（可用简单的 `assert 'appear(NAV_EVENT' not in src`）。

---

## 4. 验证边界

- 允许：Read/Grep/Glob；`python -m py_compile`；`python dev_tools/storage_scan_replay.py`。
- 禁止：启动 ALAS/GUI/adb/模拟器；运行 alas.py；修改 `doc/storage-shots/`、`doc/gearskin-shots/`；`git commit`。

## 5. 人工验证清单（用户执行）

1. 下次 StorageScan 日志应出现 `Gear skin: N event shop tab(s)`，且 `log/storage_scan/A/gear_skin.json` 生成，`boxes[].skins[]` 与游戏内「外观装备箱 → 查看详情」清单一致。
2. `records.json` 最新条目：`舰炮研发图SSR型 = 902`（不得为 2902）、新增 `机密实验计划 = 960`、`氟橡胶` 类长数字不再出现 `1xxxx`。
3. 连续两天的 `舰炮研发图SSR型` 不应在 902/2902 之间摆动；`试作型突破部件` 稳定 1204 区间。
4. 非活动期（活动结束 2026-11-04 之后）：日志应打印 `No event shop currently, skip gear skin statistics`，且不生成空记录。

## 6. 静态验收

1. `py_compile` 通过：`module/storage/scanner.py`、`module/gear_skin/gear_skin.py`、`dev_tools/storage_scan_replay.py`。
2. `python dev_tools/storage_scan_replay.py` 退出码 0（第 R5 节 5 类断言全绿）。
3. `grep -n "digit_templates\|_match_amount\|amount_digits" module/storage/scanner.py` 无残留；文件 `module/storage/amount_digits.json` 已删除。
4. `grep -n "appear(NAV_EVENT" module/gear_skin/gear_skin.py` 无命中。
5. `grep -n "cross_get" module/storage/scanner.py` 命中 Q1 修复点；`grep -n "EventGeneral_GearSkinStatistic" module/storage/scanner.py` 无命中。
6. `git diff` 未触及 `module/shop_event/`、`module/shop/`、`module/storage/{storage,ui,assets}.py`、`module/config/`、`assets/`。
7. 交付说明含：`_resolve_amount` 与 `_merge_amounts` 的方向判定依据、harness 输出摘要、遗留风险（若某格 7 个偏移全被污染则无解，只能靠跨页票数）。

## 7. 交付物

1. `git diff`（未提交）+ 已删除资产说明。
2. `dev_tools/storage_scan_replay.py` 最终版本。
3. 逐条对应第 6 节的静态自查报告。
4. ≤5 行交付说明。
