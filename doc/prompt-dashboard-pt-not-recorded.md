# 修复：仪表盘「活动PT」长期不更新（停在 23 天前）

## 现象

WebUI 仪表盘「活动PT」的卡片一直是旧值，时间戳停在 `23天前`，其他卡片（石油 17分钟前、
物资 27秒前、钻石 3小时前）都正常刷新。卡片后面也没有 `/ 限制` 尾巴，而石油有 `/ 17000`。

## 证据

实例 E 的 `config/E.json`：

```json
"EventGeneral": { "PtLimit": 0, "TimeLimit": "2020-01-01 00:00:00" },
"Dashboard": { "Pt": { "Value": 18100, "Record": "2026-09-30 23:40:02" } }
```

今天是 10-24，`2026-09-30` 正好是 23~24 天前。**PtLimit 是 0**（默认值），
webui 里 `value_limit == ' / 0'` 时会把尾巴清空——两个现象互相印证。

日志里 18:25 明确在跑活动图：
```
18:25:01.118 | Campaign run: event="event_20261022_cn", name=t6
```
说明任务本身没问题，就是这条记录链路没走通。

## 根因

全仓只有一个地方写 `Dashboard.Pt`：`module/campaign/campaign_status.py:55-72` 的
`get_event_pt()`，末尾 `LogRes(self.config).Pt = pt`。

而 `get_event_pt()` **只有一个调用点**：`module/campaign/campaign_event.py:75`，
位于 `event_pt_limit_triggered()` 内。该函数开头（56-71 行）：

```python
def event_pt_limit_triggered(self):
    limit = int(re.sub(r'[,.\'"，。]', '', str(self.config.EventGeneral_PtLimit)))
    tasks = EVENTS + RAIDS + COALITIONS + GEMS_FARMINGS + HOSPITAL
    command = self.config.Scheduler_Command
    if limit <= 0 or command not in tasks:
        return False          # ← limit=0 时在这里就返回了，连 OCR 都不做
    ...
    pt = self.get_event_pt()  # ← 第 75 行，永远到不了
```

**PT 记录是「PT 上限检查」的副产品。用户不设 `EventGeneral.PtLimit` 时，
ALAS 从不读 PT，仪表盘自然永远不更新。** 上次有记录（09-30）说明那个活动期间
曾经设过限额，活动结束后限额被 `_disable_tasks()` 重置回 `DEFAULT_TIME/PtLimit`，
从此再没更新过。

影响面：所有 `PtLimit = 0` 的实例（当前 A~E 全部如此）。

## 修复要求

原则：**把「记录 PT」和「PT 限额检查」解耦**，记录不能依赖限额配置。

### R1 `CampaignStatus` 新增节流记录方法

`module/campaign/campaign_status.py`，紧跟 `get_event_pt()` 之后：

```python
# Dashboard.Pt 的刷新间隔。PT 只在活动图主界面右上角可见（OCR_EVENT_PT
# 区域 (1196, 109, 1280, 131)），OCR 有开销，普通刷图时每轮都读不划算。
EVENT_PT_LOG_INTERVAL = timedelta(minutes=30)


def update_event_pt(self, interval=None):
    """
    把活动 PT 同步到仪表盘，与 EventGeneral.PtLimit 无关。

    Pages:
        in: page_event or page_sp（右上角能看到 PT 的界面）

    Returns:
        int: 本次读到的 PT；被节流跳过时返回缓存值（可能为 0）
    """
    interval = interval or self.EVENT_PT_LOG_INTERVAL
    record = self.config.modified.get(
        'Dashboard.Pt.Record',
        deep_get(self.config.data, 'Dashboard.Pt.Record', default=None),
    )
    if isinstance(record, datetime) and record > datetime(2023, 1, 1):
        if datetime.now() - record < interval:
            return deep_get(self.config.data, 'Dashboard.Pt.Value', default=0)

    return self.get_event_pt()
```

需要新增 import：
```python
from datetime import datetime, timedelta
from module.config.deep import deep_get
```

注意：必须同时看 `config.modified`——`LogRes` 是往 `config.modified` 写的，
不会立刻进 `config.data`。只读 `config.data` 会导致节流失效（每轮都 OCR）。

### R2 `event_pt_limit_triggered()` 里无条件记录

`module/campaign/campaign_event.py:56-82`，把 `limit <= 0` 的短路改成「记账后返回」：

```python
def event_pt_limit_triggered(self):
    limit = int(re.sub(r'[,.\'"，。]', '', str(self.config.EventGeneral_PtLimit)))
    tasks = EVENTS + RAIDS + COALITIONS + GEMS_FARMINGS + HOSPITAL
    command = self.config.Scheduler_Command
    if command not in tasks:
        return False
    if command in GEMS_FARMINGS and self.stage_is_main(self.config.Campaign_Name):
        return False

    if limit <= 0:
        # 没设上限也要读一次，否则仪表盘的「活动PT」永远不会更新。
        # update_event_pt() 自带节流，不会每轮都 OCR。
        self.update_event_pt()
        return False

    # 设了上限时不做节流，必须读到最新的 PT 才能及时停
    pt = self.get_event_pt()
    logger.attr('Event_PT_limit', f'{pt}/{limit}')
    if pt >= limit:
        logger.hr(f'Reach event PT limit: {limit}')
        self._disable_tasks(tasks)
        return True

    return False
```

要点：
- `if limit <= 0 or command not in tasks` 被拆成两个判断，`tasks` 的短路保留，
  只让 `limit <= 0` 走到记录分支。
- `limit > 0` 的路径**不做节流**，否则「刷到上限自动停」最多会多刷 30 分钟。
- 调用点不用改：`run.py:117` 已经是 `if oil_check and self.campaign.event_pt_limit_triggered():`，
  `oil_check` 为 True 表示当前不在自动搜索菜单、即活动图主界面，PT 才可见。

### 不要动的地方

- 不要改 `get_event_pt()` 内部的 OCR 和 `LogRes` 写法。
- 不要改 `run.py:96-132` 的 `triggered_stop_condition()` 结构和 `oil_check` 的传参方式。
- 不要在「自动搜索菜单内」读取 PT——那时右上角被菜单盖住，必然 OCR 出垃圾。
- 不要为了这个问题把 `PtLimit` 改成某个大数写死进默认配置（见下方「临时绕过」）。
- 不要动 `coalition.py` / `raid.py` 里各自同名的 `get_event_pt()`，它们不写 Dashboard。

## 验收

1. 保持 `EventGeneral.PtLimit = 0`，跑一次 Event2（活动图）。
2. 日志中应出现 `Event_PT: <数字>`（来自 `logger.attr('Event_PT', pt)`），
   且 `config/E.json` 的 `Dashboard.Pt.Record` 更新为当前时间。
3. WebUI 仪表盘「活动PT」的时间戳变成「x分钟前」，数值与游戏内一致。
4. 同一任务内连续刷图时，30 分钟内不重复 OCR（日志里 `Event_PT` 不会每轮都出现）。
5. 设 `PtLimit = 1000`（低于当前 PT）再跑一次，应正常触发
   `Reach event PT limit` 并停任务——确认限额逻辑没有被破坏。

## 临时绕过（不修代码也能立刻恢复记录）

把该实例的「活动 PT 上限」（`EventGeneral.PtLimit`）设成一个远大于实际的值，
例如 `999999`。这样 `limit <= 0` 短路不再命中，`get_event_pt()` 会被正常调用并写仪表盘。
活动结束时 `_disable_tasks()` 会把它重置回 0，届时需要重新设置。

## 部署提醒

改动文件：
- `module/campaign/campaign_status.py`
- `module/campaign/campaign_event.py`

仓库与运行目录各有一份，**两边都要改**：
- 源码仓：`C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript/module/campaign/`
- 运行副本：`D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/module/campaign/`

运行目录下的 `__pycache__/*.cpython-37.pyc` 会被直接复用，改完必须重启实例
（GUI「停止」→「开始」）才会生效。
