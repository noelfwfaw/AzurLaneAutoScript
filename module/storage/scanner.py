import json
import os
import re
import time
from datetime import datetime

import numpy as np

from module.base.timer import Timer
from module.handler.login import LoginHandler
from module.logger import logger
from module.ocr.ocr import Ocr
from module.statistics.item import AmountOcr
from module.storage.ui import StorageUI

OUTPUT_FOLDER = 'log/storage_scan'

# 用户关注物品白名单（按 OCR 常见混淆微调匹配）
WHITELIST = [
    '特装型突破', '试作型突破',
    '舰炮研发图', '鱼雷研发图', '防空炮研发图', '舰载机研发图', '装备研发图',
    '绝密实验计划', '特装原型',
    '心智单元', '心智单元II',
    '通用部件T4', '主炮部件T4', '鱼雷部件T4', '防空炮部件T4', '舰载机部件T4',
    '喵箱SSR型',
]

# 同名不同品质物品并存时，记录名加品质后缀（仅在该名字出现多种品质时）
QUALITY_SUFFIX = {'gold': '(金)', 'purple': '(紫)'}


def display_name(name, quality, suffix=False):
    """
    输出名按游戏稀有度命名：
    - 特装型/试作型突破 -> 加"部件"后缀
    - 装备研发图 -> 装备研发图UR型
    - 四类研发图按品质：紫UR型、金SSR型
    - suffix=True（同名多数量分组）时其余物品加品质兜底后缀
    """
    if name == '装备研发图':
        return '装备研发图UR型'
    if name in ('特装型突破', '试作型突破'):
        return name + '部件'
    if name.endswith('研发图'):
        if quality == 'purple':
            return name + 'UR型'
        if quality == 'gold':
            return name + 'SSR型'
        return name
    if suffix:
        return name + QUALITY_SUFFIX.get(quality, '')
    return name

# 部件类物品（同档五种）：名条 OCR 常吞掉/互读档位数字（T1↔T4），
# 档位靠品质裁决——T1 灰框、T2 蓝/T3 紫框、T4 金框
PART_HEADS = ('通用部件', '主炮部件', '鱼雷部件', '防空炮部件', '舰载机部件')


def normalize(text):
    """去除 OCR 噪声：空白、标点、易混字符归一。"""
    text = text.strip()
    text = re.sub(r'[.\s|、,，:：·]+$', '', text)
    text = text.replace('‖', 'II').replace('|I', 'II').replace('||', 'II')
    text = text.replace('圈', '图').replace('戈', '划').replace('邰', '部')
    return text


def match_whitelist(name, quality=None):
    """
    OCR 名字与白名单模糊匹配：先精确匹配，再前缀容错（OCR 截断）。

    部件类物品（通用/主炮/鱼雷/防空炮/舰载机部件）有 T1~T5 多档，
    名条 OCR 常吞掉或互读档位数字（实测 T1 行会被读成 T4），
    必须用品质裁决档位：
    - 仅金框格子可能匹配 T4（T4 金框；T1 灰框、T2 蓝/T3 紫框）
    - 名条明确读到 T4 以外的档位数字（T1/T2/T3/T5…）→ 拒绝
    - 数字被吞（"…部件T"/"…部件Ta"）且金框 → 按 T4 处理

    Args:
        name (str): OCR 原始结果
        quality (str): 该格品质 'gold'/'purple'/'other'，可选

    Returns:
        str: 匹配到的白名单全名，未匹配返回 None
    """
    name = normalize(name)
    if len(name) < 3:
        return None
    # 部件档位规则优先：金框裁决（T1 行误读成"T4"也不能绕过品质检查）
    for head in PART_HEADS:
        if not name.startswith(head):
            continue
        full = head + 'T4'
        if full not in [normalize(w) for w in WHITELIST]:
            continue
        # 名条里明确出现的档位数字（i/l/| 等形似 4 的误读除外）
        tail = name[len(head):].replace('T', '', 1)
        digits = re.sub(r'[iIl|]', '4', tail)
        has_other_tier = any(ch.isdigit() and ch != '4' for ch in digits)
        if quality == 'gold' and not has_other_tier:
            return full
        return None
    # 精确匹配优先（避免"心智单元"被"心智单元II"抢匹配）
    for w in WHITELIST:
        if normalize(w) == name:
            return w
    best = None
    for item in WHITELIST:
        item_n = normalize(item)
        # 前缀匹配：一方是另一方的前缀（OCR 截断，如"舰炮研发."）
        common = os.path.commonprefix([name, item_n])
        need = min(len(name), len(item_n)) - 1
        if len(common) >= max(3, need):
            if best is None or len(item_n) > len(normalize(best)):
                best = item
    # 前缀匹配到部件类时同样要金框裁决：T1 行的格子名被裁成
    # "舰载机"这类短名时不走部件规则，但数量/品质是灰框 T1 的
    if best is not None and quality != 'gold' \
            and any(normalize(best).startswith(head) for head in PART_HEADS):
        return None
    return best


class MaterialScanner(StorageUI):
    """
    材料仓库白名单扫描器：进入仓库-材料页，逐屏识别物品名与数量，
    只记录白名单物品，集齐或到底即停。

    材料页布局特点（实测）：
    - 列固定 7 列 x=140+159*c，卡片图标 124x124
    - 行是变高分区的（礼物卡区/标准区相位不同，且翻页有相位漂移），
      因此不依赖固定行网格，而是检测每个物品的"名称横条"
      （图标正下方的深蓝底白字横条）作为锚点
    - 名称横条 y -> 图标区 [y-124, y)，数量在图标区右下角
    """
    GRID_X0 = 140
    GRID_DX = 159
    GRID_COLS = 7
    CARD_H = 124
    ROW_PITCH = 178
    # 名称横条检测（深蓝底）。占比阈值放宽：外观装备箱等物品的
    # 名条不是深蓝底，混排行（如 装备研发图+外观装备箱+蓝图）
    # 的全宽蓝色占比会跌破 0.35，导致整行漏检
    BAR_BLUE_DIFF = 15
    BAR_BLUE_MAX = 180
    BAR_RED_MAX = 120
    BAR_COVER = 0.28
    BAR_MIN_H = 12
    # 名称横条有效区：低于此线进入底部页签遮挡区，读数不可靠
    # （实测 bar y=634 的行读数正常，页签区在 ~680 以下）
    BAR_MAX_Y = 650
    # 数量区域（相对图标区左上角），threshold=160 滤图标灰白噪声；
    # 上界不宜放宽，图标白色部分会被误读为前导数字
    AMOUNT_AREA = (55, 96, 123, 124)
    AMOUNT_THRESHOLD = 160
    # 翻页滑动：内容区上滑约一行。用慢速拖拽（游戏按松手速度区分
    # 拖拽/甩动），基本不产生惯性翻滚，步长稳定 ~190px ≈ 1.07 行，
    # 每行物品稳定跨 2~3 页出现，偶发的单页 OCR 误读由跨页票数共识排除
    SWIPE_P1 = (700, 450)
    SWIPE_P2 = (700, 260)
    SWIPE_DURATION = (0.85, 1.0)
    # 画面稳定判定（连续两帧几乎一致，防滑动残影）
    SETTLE_SLEEP = 0.4
    SETTLE_DIFF = 0.5
    MAX_PAGE = 60
    ICON_MIN_Y = 40

    def __init__(self, config, device=None, task='StorageScan'):
        super().__init__(config, device, task)
        self.ocr_name = Ocr([], lang='cnocr', letter=(255, 255, 255), threshold=128, name='SSName')
        # 第二遍更宽松的阈值，重试第一遍未匹配的格子（OCR 偶发吞字）
        self.ocr_name_retry = Ocr([], lang='cnocr', letter=(255, 255, 255), threshold=96, name='SSName2')
        self.ocr_amount = AmountOcr([], threshold=self.AMOUNT_THRESHOLD, name='SSAmount')
        # 跨页数量共识：(白名单名, 品质) -> {数量: 出现页数}
        self.votes = {}

    def wait_settled(self, timeout=None):
        """
        等待画面稳定（连续两帧几乎一致）。

        Returns:
            np.ndarray: 稳定后的截图
        """
        if timeout is None:
            timeout = Timer(8, count=16)
        timeout.reset()
        prev = None
        while 1:
            time.sleep(self.SETTLE_SLEEP)
            img = self.device.screenshot()
            if prev is not None:
                diff = np.abs(img.astype(np.int16) - prev.astype(np.int16)).mean()
                if diff < self.SETTLE_DIFF:
                    return img
            prev = img
            if timeout.reached():
                return img

    def detect_name_bars(self, image):
        """
        检测画面中的名称横条（物品名所在深蓝底横条），返回各行名条顶 y。

        横条会被长物品名中的白字拆断成多段；且部分分区（蓝图区等）
        行距与标准区不同，不能按固定行距外推，因此：
        1. 逐行统计深蓝像素占比，提取条段
        2. 相邻段（间距 <= 8）归为同一行，行位置取最长主段，
           避免白字拆断把行位置拉偏
        3. 等距验证剔除孤立噪声条（如礼物卡下缘的蓝条），
           行距按 1~2 行容差计（容忍中间行漏检）

        Args:
            image (np.ndarray): 截图

        Returns:
            list[int]: 名称横条顶 y 坐标，升序
        """
        region = image[:, self.GRID_X0:self.GRID_X0 + self.GRID_DX * self.GRID_COLS]
        r, g, b = region[:, :, 0].astype(int), region[:, :, 1].astype(int), region[:, :, 2].astype(int)
        dark_blue = (b > r + self.BAR_BLUE_DIFF) & (b > g + 10) & (b < self.BAR_BLUE_MAX) & (r < self.BAR_RED_MAX)
        ratio = dark_blue.mean(axis=1)
        segs = []
        in_seg = False
        start = 0
        for y in range(image.shape[0]):
            if ratio[y] > self.BAR_COVER and not in_seg:
                start = y
                in_seg = True
            elif ratio[y] <= self.BAR_COVER and in_seg:
                segs.append((start, y - 1))
                in_seg = False
        if in_seg:
            segs.append((start, image.shape[0] - 1))
        # 过滤太矮的段与顶部 UI 区
        segs = [seg for seg in segs
                if seg[1] - seg[0] >= self.BAR_MIN_H and seg[0] - self.CARD_H >= self.ICON_MIN_Y]
        if not segs:
            return []
        # 相邻段聚为一行，行位置取最长主段
        rows = []
        cluster = [segs[0]]
        for seg in segs[1:]:
            if seg[0] - cluster[-1][1] <= 8:
                cluster.append(seg)
            else:
                rows.append(max(cluster, key=lambda s: s[1] - s[0])[0])
                cluster = [seg]
        rows.append(max(cluster, key=lambda s: s[1] - s[0])[0])
        # 底部素材栏遮挡区的行不可读，丢弃（下一页会在更好位置重现）
        rows = [y for y in rows if y + 42 <= self.BAR_MAX_Y]
        # 逐格条带确认：外观箱/蓝图区行距不是 178px，不能用等距校验；
        # 行内至少 2 个格子自身能找到深蓝名条才算真行
        # （礼物卡下缘等孤立噪声条自然被否决，混排行的部分蓝格
        #   也足以确认；非白名单行即使保留，也会在匹配阶段被过滤）
        rows = [y for y in rows if sum(
            1 for ci in range(self.GRID_COLS)
            if self.cell_bar_top(image, self.GRID_X0 + ci * self.GRID_DX, y) is not None
        ) >= 2]
        return rows

    def cell_bar_top(self, image, x, y_guess, span=14):
        """
        细化单格名条顶位置。变高分区（礼物卡/蓝图区等）与标准
        材料行混排时，行锚点可能偏移十几像素，导致数量区裁剪
        错位（实测 2257 丢首位成 257）与品质采样污染；用该格
        自身的深蓝条精确定位。

        Args:
            image (np.ndarray): 截图
            x (int): 格子左上角 x
            y_guess (int): 行锚点 y（名条顶估计值）
            span (int): 允许的上下搜索半径

        Returns:
            int: 该格名条顶 y；找不到深蓝条返回 None
        """
        y0 = max(y_guess - span, 0)
        win = image[y0:y_guess + 44, x + 6:x + self.CARD_H - 6]
        if win.size == 0:
            return None
        r, g, b = win[:, :, 0].astype(int), win[:, :, 1].astype(int), win[:, :, 2].astype(int)
        db = (b > r + self.BAR_BLUE_DIFF) & (b > g + 10) & (b < self.BAR_BLUE_MAX) & (r < self.BAR_RED_MAX)
        ratio = db.mean(axis=1)
        good = np.where(ratio > 0.45)[0]
        if len(good) == 0:
            return None
        return y0 + int(good[0])

    def cell_band_rgb(self, image, x, icon_top):
        """
        采样图标上缘与左缘的边框带平均 RGB（品质色所在）。
        内容图案（如研发图纸的白色图纸画面）会污染整格均值，
        实测边框带区分度最大。
        """
        bands = [
            image[icon_top + 2:icon_top + 8, x + 10:x + self.CARD_H - 10],
            image[icon_top + 10:icon_top + self.CARD_H - 10, x + 2:x + 8],
        ]
        r = g = b = 0.0
        n = 0
        for reg in bands:
            if reg.size:
                r += reg[..., 0].mean()
                g += reg[..., 1].mean()
                b += reg[..., 2].mean()
                n += 1
        if not n:
            return None
        return r / n, g / n, b / n

    def cell_quality(self, image, x, icon_top):
        """
        检测格子图标的品质色调（金/紫蓝/其他）。
        材料页存在同名但品质不同的物品（如金/紫研发图纸），
        名条文字无法区分，用边框带的底色区分。

        Returns:
            str: 'gold' / 'purple' / 'other'
        """
        rgb = self.cell_band_rgb(image, x, icon_top)
        if rgb is None:
            return 'other'
        r, g, b = rgb
        if b > 150 and b > r + 30:
            return 'purple'
        if r > 150 and r > b + 50:
            return 'gold'
        return 'other'

    def scan_current_view(self, image):
        """
        扫描当前画面：以名称横条为锚点，OCR 物品名与数量。

        名字 OCR 会丢弃全黑（无文字）的标签图导致结果列表变短、
        与数量结果错位，因此先剔除无字格子再送 OCR。

        Args:
            image (np.ndarray): 截图

        Returns:
            dict: {(白名单名, 品质): amount}，品质 'gold'/'purple'/'other'；
            无行检出（页面处于过渡态/行都在遮挡区）返回 None
        """
        bars = self.detect_name_bars(image)
        if not bars:
            return None
        labels = []
        amounts = []
        cells = []  # (x, icon_top) 用于品质检测
        for y1 in bars:
            for ci in range(self.GRID_COLS):
                x = self.GRID_X0 + ci * self.GRID_DX
                # 单格名条细化：混排行（礼物卡区/变高分区）锚点有偏移
                y1c = self.cell_bar_top(image, x, y1)
                if y1c is None:
                    continue
                icon_top = y1c - self.CARD_H
                label = image[y1c + 2:y1c + 40, x:x + self.CARD_H]
                # 深蓝名条上无白字 → 标签区近乎纯色（空格子/行错位），
                # 跳过，保证名字与数量结果按索引对齐
                if int(label.max()) - int(label.min()) <= 30:
                    continue
                labels.append(label)
                amounts.append(image[icon_top + self.AMOUNT_AREA[1]:icon_top + self.AMOUNT_AREA[3],
                                     x + self.AMOUNT_AREA[0]:x + self.AMOUNT_AREA[2]])
                cells.append((x, icon_top))
        if not labels:
            return {}
        names = self.ocr_name.ocr(labels, direct_ocr=True)
        amts = self.ocr_amount.ocr(amounts, direct_ocr=True)
        if len(names) != len(labels) or len(amts) != len(labels):
            # cnocr 极端情况下仍可能丢结果，防御性对齐
            logger.warning('MaterialScanner: ocr length mismatch %d/%d/%d',
                           len(names), len(amts), len(labels))

        def quality_of(i):
            x, icon_top = cells[i]
            return self.cell_quality(image, x, icon_top)

        found = {}
        retry_idx = []
        for i, (n, a) in enumerate(zip(names, amts)):
            matched = match_whitelist(n, quality_of(i))
            if not matched:
                retry_idx.append(i)
                continue
            try:
                amount = int(a)
            except (ValueError, TypeError):
                continue  # 数量读不出，留给其他页补充
            if amount > 0:
                found[(matched, quality_of(i))] = amount
        # 第二遍宽松阈值重试未命中的格子
        if retry_idx:
            retry_labels = [labels[i] for i in retry_idx]
            retry_names = self.ocr_name_retry.ocr(retry_labels, direct_ocr=True)
            for i, n in zip(retry_idx, retry_names):
                matched = match_whitelist(n, quality_of(i))
                if not matched:
                    continue
                try:
                    amount = int(amts[i])
                except (ValueError, TypeError):
                    continue
                if amount > 0:
                    found[(matched, quality_of(i))] = amount
        return found

    def swipe_next(self, distance=None):
        p1, p2 = self.SWIPE_P1, self.SWIPE_P2
        if distance is not None:
            # 小步探测：缩短滑动向量但不进入顶部 UI 区
            p2 = (p1[0], max(p1[1] - distance, 60))
        self.device.swipe(p1, p2, duration=self.SWIPE_DURATION, name='StorageScanNext')

    def swipe_top(self):
        self.device.swipe((700, 150), (700, 600), duration=(0.3, 0.4), name='StorageScanTop')

    def reach_top(self):
        """
        反复下滑内容区直到顶部。滚动条位置检测在本页不可靠
        （顶部误报 0.99），改用内容判断：连续两次下滑后画面
        不再变化才视为到顶（单次不变可能是惯性滚动中误判）。
        """
        unchanged = 0
        for _ in range(40):
            self.device.screenshot()
            before = self.device.image.copy()
            self.device.click_record_clear()
            self.swipe_top()
            time.sleep(0.8)
            after = self.device.screenshot()
            diff = np.abs(after.astype(int) - before.astype(int)).mean()
            if diff < 2:
                unchanged += 1
                if unchanged >= 2:
                    return True
            else:
                unchanged = 0
        logger.warning('MaterialScanner: failed to reach top')
        return False

    def scan_material(self, whitelist=None):
        """
        扫描材料页，记录白名单物品数量。集齐白名单或到底即停。

        Args:
            whitelist (list[str]): 物品名列表，默认使用模块 WHITELIST

        Returns:
            dict: {物品名: 数量}，同名多品质物品已加品质后缀
        """
        global WHITELIST
        if whitelist:
            WHITELIST = whitelist
        self._storage_enter_material()
        self.reach_top()
        self._scan_pass(self.MAX_PAGE)
        # 顶部几行只在开头出现，若被遮挡区裁掉或当时误读则没有机会
        # 修正；扫到过物品但未集齐时回顶补扫几页，靠跨页票数共识纠正。
        # 一无所获（如小号没有白名单物品）不补扫，避免重复空扫
        if self.votes and len({name for name, _ in self.votes}) < len(WHITELIST):
            logger.info('MaterialScanner: top rescue pass')
            self.reach_top()
            self._scan_pass(8, label='r')
        return self.get_records()

    def _scan_pass(self, max_pages, label='p'):
        """
        从当前位置向下逐页扫描一程，终止条件（按优先级）：
        1. 集齐白名单
        2. 连续 3 页无新增白名单物品（物品都在靠前位置，
           再翻只是重复内容；小号没有白名单物品时快速停）
        3. 连续 3 页检不出名称横条（空仓库/页面到底）
        4. 滑动后画面不再变化（到底）
        5. 页数上限
        结果累积进 self.votes（跨页共识），调用方决定是否补扫。
        """
        unchanged = 0      # 到底判定：滑动后画面不变计数
        no_progress = 0    # 无新增物品计数
        no_bars = 0        # 检不出名称横条计数
        known = set()      # 本程已见过的 (名, 品质) 组合
        for _ in range(max_pages):
            # 长循环反复滑动属于合法场景，清空点击保护计数
            self.device.click_record_clear()
            image = self.wait_settled()
            found = self.scan_current_view(image)
            if found is None:
                # 无行检出：可能画面还在惯性滚动，重截一次再试
                image = self.wait_settled()
                found = self.scan_current_view(image)
            if found is None:
                no_bars += 1
                if no_bars >= 3:
                    logger.info('MaterialScanner: no name bars for 3 pages, stop')
                    return
                # 慢速小步再探，避免跳过本页内容
                self.swipe_next(distance=80)
                self.device.screenshot()
                continue
            no_bars = 0
            # 同一物品跨多页出现（翻页重叠），偶发 OCR 误读/品质
            # 截断误配（如金框部件T5 吞数字后形似部件T4）只会
            # 出现在单页；按出现页数投票取共识值
            for key, amount in found.items():
                self.votes.setdefault(key, {}).setdefault(amount, 0)
                self.votes[key][amount] += 1
            # 按白名单基础名计数（同名金/紫只算一种），集齐提前终止
            covered = len({name for name, _ in self.votes})
            logger.info('MaterialScanner: page found %s (covered %d/%d)',
                        found or '-', covered, len(WHITELIST))
            if covered >= len(WHITELIST):
                logger.info('MaterialScanner: whitelist complete, stop early')
                return
            # 连续多页无新增物品组合：白名单物品都在靠前位置，
            # 后面翻页只重复已知内容；小号仓库可能一个都没有
            new_keys = set(found) - known
            known |= new_keys
            if new_keys:
                no_progress = 0
            else:
                no_progress += 1
                if no_progress >= 3:
                    logger.info('MaterialScanner: no new items for 3 pages, '
                                'stop (covered %d/%d)', covered, len(WHITELIST))
                    return
            # 翻页并检测是否到底（滑动后画面不变即到底）
            self.swipe_next()
            next_image = self.wait_settled()
            diff = np.abs(next_image.astype(int) - image.astype(int)).mean()
            if diff < 3:
                unchanged += 1
                if unchanged >= 2:
                    logger.info('MaterialScanner: reached bottom, covered %d/%d',
                                covered, len(WHITELIST))
                    return
            else:
                unchanged = 0
            self.device.screenshot()

    def get_records(self):
        """
        整理扫描结果：
        - 尾缀合并：行位于屏幕边缘或锚点偏移时数量区会被裁掉首位
          （实测 2257 读成 257、2900 读成 900），互为"尾缀"的
          读数把票数并给更长值再计票
        - 品质按数量分组：品质偶发翻转（紫/其他 边框色相近）但数量
          一致的读数合并为一条；同一名字出现多个互不相同的数量
          （真正的金/紫同名物品）才分开，按主导品质加后缀

        Returns:
            dict: {物品名: 数量}
        """
        # name -> {amount: {quality: count}}
        by_name = {}
        for (name, quality), amounts in self.votes.items():
            merged = dict(amounts)
            for shorter in sorted(merged, key=lambda v: len(str(v))):
                if shorter <= 0:
                    continue
                for longer in list(merged):
                    if longer != shorter and len(str(longer)) > len(str(shorter)) \
                            and str(longer).endswith(str(shorter)):
                        merged[longer] += merged.pop(shorter)
                        break
            if len(merged) > 1:
                logger.info('StorageScan votes: %s(%s) %s', name, quality, dict(merged))
            for amount, count in merged.items():
                by_name.setdefault(name, {}).setdefault(amount, {})[quality] = count
        out = {}
        for name, groups in by_name.items():
            if len(groups) == 1:
                amount, quals = next(iter(groups.items()))
                quality = max(quals.items(), key=lambda kv: kv[1])[0]
                out[display_name(name, quality)] = amount
                continue
            for amount, quals in groups.items():
                quality, _ = max(quals.items(), key=lambda kv: kv[1])
                out[display_name(name, quality, suffix=True)] = amount
        return out

    def save_records(self, time: str = None):
        """
        保存扫描结果 JSON（历史记录追加模式）。
        按实例名分目录存储，多实例（大小号）互不干扰。

        Args:
            time (str): 记录时间串，None 时取当前时间

        Returns:
            str: 文件路径
        """
        folder = os.path.join(OUTPUT_FOLDER, self.config.config_name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'records.json')
        history = []
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except (ValueError, OSError):
                history = []
        history.append({
            'time': time if time is not None else datetime.now().isoformat(timespec='seconds'),
            'items': [
                {'name': k, 'amount': v}
                for k, v in sorted(self.get_records().items())
            ],
        })
        # 只保留最近 90 天记录
        history = history[-90:]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        logger.info(f'Storage scan saved: {path} ({len(self.get_records())} items)')
        return path

    def run(self):
        """
        任务入口：进入仓库扫描材料页并记录白名单物品数量。

        结束后必须调用 config.task_delay() 推迟 NextRun，
        否则调度器会因任务时间已过立刻再次触发，无限重复扫描。
        """
        if not self.device.app_is_running():
            logger.warning('Game is not running, start it')
            LoginHandler(config=self.config, device=self.device).app_start()

        try:
            self.ui_goto_storage()
            records = self.scan_material()
            logger.hr('Storage scan results', level=1)
            for name, amount in sorted(records.items()):
                logger.attr(name, amount)
            scan_time = datetime.now().isoformat(timespec='seconds')
            self.save_records(time=scan_time)
            # 外观装备统计与仓库扫描同一任务、同一时间戳；
            # 自身吞掉所有异常，任何失败都不影响仓库扫描结果与 task_delay。
            if self.config.EventGeneral_GearSkinStatistic:
                try:
                    from module.gear_skin.gear_skin import GearSkinScanner
                    result = GearSkinScanner(self.config, self.device).run(scan_time=scan_time)
                    if result:
                        logger.hr('Gear skin statistics', level=2)
                        logger.info(f'Gear skin statistic saved, see {OUTPUT_FOLDER}/{self.config.config_name}/gear_skin.json')
                except Exception as e:
                    logger.warning(f'GearSkinStatistic failed, storage scan result unaffected: {e}')
        except Exception:
            # 失败也要推迟（FailureInterval），防止调度器立刻重试死循环
            self.config.task_delay(success=False)
            raise
        # 成功：推迟到 SuccessInterval 之后（默认 1440 分钟 = 明天）
        self.config.task_delay(success=True)


if __name__ == '__main__':
    from module.config.config import AzurLaneConfig

    config = AzurLaneConfig('alas')
    scanner = MaterialScanner(config)
    scanner.device.screenshot()
    scanner.ui_goto_storage()
    records = scanner.scan_material()
    for name, amount in sorted(records.items()):
        print(f'{name}: {amount}')
    scanner.save_records()
