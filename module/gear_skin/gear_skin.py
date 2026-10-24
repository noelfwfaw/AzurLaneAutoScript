"""
活动外观装备统计。

随仓库扫描（StorageScan）任务一并执行：进入活动商店，打开外观装备箱的
「信息」弹窗，统计当期每种外观装备的拥有数量。

入口：
    GearSkinScanner(config, device).run(scan_time)

说明：
- 箱子的识别直接复用 module/shop_event 的规则（价格 2000、限购 10 == SkinBox），
  本模块不重新识别箱子、也不改动 module/shop_event 的任何行为。
- 任何异常都在 run() 最外层吞掉（只 warning），不影响仓库扫描主流程。
"""
import os
import re
from datetime import datetime

import cv2

from module.base.button import Button
from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import color_similarity_2d, crop, extract_letters, save_image
from module.config.utils import server_time_offset
from module.gear_skin.assets import *
from module.gear_skin.records import append_record
from module.logger import logger
from module.ocr.ocr import Ocr
from module.shop.assets import NAV_EVENT, NAV_GENERAL, SHOP_BUY_CONFIRM, SHOP_CLICK_SAFE_AREA
from module.shop.ui import ShopUI
from module.shop_event.assets import NO_NAV_EVENT_CHECK
from module.shop_event.clerk import EventShopClerk, ItemNotFoundError
from module.shop_event.ui import EVENT_SHOP_SCROLL
from module.ui.assets import SHOP_GOTO_MUNITIONS
from module.ui.page import page_munitions

OCR_EXCHANGE_DETAIL = Ocr(EXCHANGE_DETAIL_BUTTON, lang='cnocr', name='OCR_EXCHANGE_DETAIL')
OCR_EXCHANGE_TITLE = Ocr(EXCHANGE_TITLE_AREA, lang='cnocr', letter=(232, 200, 90),
                         name='OCR_EXCHANGE_TITLE')
OCR_EXCHANGE_CANCEL = Ocr(EXCHANGE_CANCEL, lang='cnocr', name='OCR_EXCHANGE_CANCEL')
OCR_INFO_TITLE = Ocr(INFO_TITLE_AREA, lang='cnocr', name='OCR_INFO_TITLE')

ERROR_FOLDER = os.path.join('log', 'storage_scan', 'err')


class GearSkinScanner(EventShopClerk, ShopUI):
    def __init__(self, config, device=None, task=None):
        super().__init__(config, device, task)

    @cached_property
    def ocr_model(self):
        return Ocr((0, 0, 1, 1), lang='cnocr').cnocr

    def run(self, scan_time):
        """
        Args:
            scan_time (str): 仓库扫描记录的时间串，原样写进统计记录

        Returns:
            dict | None: 写入的记录，跳过或失败时 None
        """
        try:
            return self._run(scan_time)
        except Exception as e:
            logger.warning(f'GearSkinStatistic failed: {e}')
            return None

    def _run(self, scan_time):
        if not self.enter_event_shop():
            return None

        count, navbar = self.event_shop_tab_count_and_navbar
        logger.info(f'Gear skin: {count} event shop tab(s)')
        boxes = []
        for index in range(count):
            navbar.set(main=self, left=index + 1)
            try:
                items = self.scan_all()
            except (ItemNotFoundError, IndexError) as e:
                logger.warning(f'Gear skin: scan event shop tab {index + 1} failed: {e}')
                continue

            skin_boxes = [item for item in items if item.name == 'SkinBox']
            if not skin_boxes:
                logger.info(f'No gear skin box in event shop tab {index + 1}')
                continue
            for item in skin_boxes:
                box = self.scan_box(item)
                if box:
                    boxes.append(box)

        # 守卫三（内容层）：一个箱都没读到，不写空记录
        if not boxes:
            logger.info('No gear skin box found, skip gear skin statistics')
            return None

        slot_count = sum(box['slot_count'] for box in boxes)
        total = sum(skin['amount'] for box in boxes for skin in box['skins'])
        kinds = len(set(skin['name'] for box in boxes for skin in box['skins'] if skin['amount'] > 0))
        record = {
            'time': scan_time,
            'boxes': [{'box': box['box'], 'skins': box['skins']} for box in boxes],
            'slot_count': slot_count,
            'total': total,
            'kinds': kinds,
        }
        append_record(self.config.config_name, record)
        logger.info(f'Gear skin: {slot_count} slots, {kinds} kinds, total {total}')
        return record

    def enter_event_shop(self):
        """
        守卫一（导航层）与守卫二（时间层）。

        导航层直接复用 EventShop 任务的标准流程：先确认在补给道具页，
        再用 NO_NAV_EVENT_CHECK 判断活动商店是否存在。不能用 NAV_EVENT
        的外观比对判存在性——NAV_EVENT 的颜色是选中态颜色（实测未选中
        态 B 通道差 33 > 阈值 30），点击前必然判否。

        Returns:
            bool: False 表示非活动期间，应跳过
        """
        self.ui_goto_shop()
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
        if self.appear(NO_NAV_EVENT_CHECK, offset=(5, 5)):
            logger.info('No event shop currently, skip gear skin statistics')
            return False
        # check_button=NAV_EVENT 在点击后是选中态才能匹配，这正是其定义色的用途
        self.ui_click(NAV_EVENT, check_button=NAV_EVENT, appear_button=NAV_GENERAL)

        # 活动商店页加载完成前 OCR 可能读到半截文字，重试到超时
        timer = Timer(4, count=8).start()
        deadline = None
        while 1:
            deadline = self._get_event_deadline()
            if deadline is not None:
                break
            if timer.reached():
                logger.info('Failed to read event shop deadline, skip gear skin statistics')
                return False
            self.device.screenshot()

        server_now = datetime.now() - server_time_offset()
        if deadline <= server_now:
            logger.info(f'Event shop ended at {deadline}, skip gear skin statistics')
            return False
        return True

    def scan_box(self, item):
        """
        打开一个外观装备箱的信息弹窗并统计。

        Args:
            item (EventShopItem): name == 'SkinBox' 的商店物品

        Returns:
            dict | None: {'box': 箱名, 'skins': [{'name','amount'}], 'slot_count': int}
        """
        if item.scroll_pos is not None:
            EVENT_SHOP_SCROLL.set(item.scroll_pos, main=self)
        try:
            items = self.event_shop_get_items()
        except ItemNotFoundError as e:
            logger.warning(f'Gear skin: box items not found: {e}')
            return None
        candidates = [i for i in items if i.name == 'SkinBox']
        if not candidates:
            logger.warning(f'Gear skin: no SkinBox at scroll {item.scroll_pos}')
            return None
        target = min(candidates, key=lambda x: abs(x.button[0] - item.button[0]))

        self.device.click(target)

        box_name = ''
        detail = False
        timer = Timer(3, count=6).start()
        for _ in self.loop():
            if self.abort_if_buying():
                return None
            title = OCR_EXCHANGE_TITLE.ocr(self.device.image)
            if '装备箱' in title:
                box_name = title.strip()
            if '查看详情' in OCR_EXCHANGE_DETAIL.ocr(self.device.image):
                detail = True
                break
            if timer.reached():
                break
        if not detail:
            logger.warning(f'Gear skin: no 查看详情 button for {box_name or item}')
            self.save_error()
            return None

        self.click_area(EXCHANGE_DETAIL_BUTTON)

        opened = False
        timer = Timer(3, count=6).start()
        for _ in self.loop():
            if self.abort_if_buying():
                return None
            text = OCR_INFO_TITLE.ocr(self.device.image)
            if '信息' in text or 'INFORMATION' in text.upper():
                opened = True
                break
            if timer.reached():
                break
        if not opened:
            logger.warning(f'Gear skin: info popup not opened for {box_name}')
            self.save_error()
            self.close_exchange()
            return None

        if not box_name:
            box_name = f'SkinBox@{item.scroll_pos}'
        logger.hr(f'Gear skin: {box_name}', level=2)
        skins, slot_count = self.collect_skins()
        self.close_info()
        if not skins:
            logger.warning(f'Gear skin: no skin read from {box_name}')
            return None
        return {'box': box_name, 'skins': skins, 'slot_count': slot_count}

    def collect_skins(self):
        """
        回顶后逐屏 OCR，直到没有新增条目。

        Returns:
            tuple: (skins 列表, 槽位数)
        """
        self.back_to_top()
        counts = {}
        stall = 0
        timer = Timer(INFO_SCROLL_TIMEOUT).start()
        for _ in range(INFO_MAX_SCROLL):
            if timer.reached():
                logger.warning('Gear skin: info scan timeout')
                break

            frame = self.read_frame()
            progressed = False
            for key, count in frame.items():
                if count > counts.get(key, 0):
                    counts[key] = count
                    progressed = True
            stall = 0 if progressed else stall + 1
            if stall >= 2:
                break

            self.swipe_and_settle(INFO_SCROLL_FROM, INFO_SCROLL_TO)

        merged = {}
        slot_count = 0
        for (name, amount), slots in counts.items():
            merged[name] = merged.get(name, 0) + amount * slots
            slot_count += slots
        skins = [{'name': name, 'amount': amount} for name, amount in sorted(merged.items())]
        return skins, slot_count

    def read_frame(self):
        """
        Returns:
            dict: {(name, amount): 该帧内出现次数}
        """
        result = {}
        for x in INFO_COLUMNS:
            for key, count in self.read_column(x).items():
                result[key] = result.get(key, 0) + count
        return result

    def read_column(self, x):
        """
        按固定网格读取一列：金色「拥有:N」条带定位，名称取同格上方最近的白字。

        说明：cnocr 1.2.2 的 ocr() 只返回文本行、没有 box，因此这里用文字颜色
        投影代替 box 检测，配对规则仍是「上方 24px 内、同一列」。

        Args:
            x (int): 该列图标的左缘

        Returns:
            dict: {(name, amount): 1}
        """
        image = self.device.image
        x1 = x + INFO_COLUMN_OFFSET[0]
        x2 = x + INFO_COLUMN_OFFSET[1]
        area = (x1, INFO_LIST_AREA[1], x2, INFO_LIST_AREA[3])
        img = crop(image, area)

        name_bands = self.row_bands(color_similarity_2d(img, INFO_NAME_COLOR))
        amount_bands = self.row_bands(color_similarity_2d(img, INFO_AMOUNT_COLOR))

        result = {}
        for amount_band in amount_bands:
            paired = [b for b in name_bands
                      if -4 <= amount_band[0] - b[1] <= INFO_PAIR_MAX_GAP]
            if not paired:
                logger.warning(f'Gear skin: 拥有 条带无名称配对 x={x} y={amount_band[0]}')
                continue
            name_band = min(paired, key=lambda b: abs(amount_band[0] - b[1]))

            name = self.ocr_line(
                crop(img, (0, name_band[0] - 2, img.shape[1], name_band[1] + 2)),
                INFO_NAME_COLOR
            ).replace(' ', '')
            text = self.ocr_line(
                crop(img, (0, amount_band[0] - 1, img.shape[1], amount_band[1] + 2)),
                INFO_AMOUNT_COLOR
            )
            matched = re.search(r'(\d+)', text)
            if not matched or len(name) < 2:
                logger.warning(f'Gear skin: 无法识别格子 x={x} name={name!r} text={text!r}')
                continue

            key = (name, int(matched.group(1)))
            result[key] = result.get(key, 0) + 1
        return result

    @staticmethod
    def row_bands(mask):
        """
        把颜色相似度图按行投影成文字条带。

        Returns:
            list[tuple]: [(y1, y2), ...]
        """
        rows = (mask >= INFO_MASK_THRESHOLD).sum(axis=1)
        bands = []
        start = None
        for y, count in enumerate(rows):
            if count >= INFO_BAND_MIN_PIXELS:
                if start is None:
                    start = y
                continue
            if start is not None:
                bands.append([start, y])
                start = None
        if start is not None:
            bands.append([start, len(rows)])

        merged = []
        for band in bands:
            if merged and band[0] - merged[-1][1] <= 2:
                merged[-1][1] = band[1]
            else:
                merged.append(band)
        return [tuple(b) for b in merged
                if INFO_BAND_HEIGHT[0] <= b[1] - b[0] <= INFO_BAND_HEIGHT[1]]

    def ocr_line(self, image, letter):
        """
        单行 OCR。13px 小字先 2x 放大再识别。

        Returns:
            str:
        """
        gray = extract_letters(image, letter=letter, threshold=128)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
        chars = self.ocr_model.atomic_ocr_for_single_lines([gray], None)
        if not chars:
            return ''
        return ''.join(chars[0]).strip()

    def back_to_top(self):
        last = self.device.image
        for _ in range(3):
            self.swipe_and_settle(INFO_SCROLL_TOP_FROM, INFO_SCROLL_TOP_TO)
            if self.same_image(last, self.device.image):
                break
            last = self.device.image

    def swipe_and_settle(self, p1, p2):
        self.device.swipe(p1, p2, duration=(0.3, 0.5))
        last = None
        for _ in self.loop(timeout=2):
            if last is not None and self.same_image(last, self.device.image):
                break
            last = self.device.image.copy()

    @staticmethod
    def same_image(image1, image2):
        if image1 is None or image2 is None:
            return False
        return float(cv2.absdiff(image1, image2).mean()) < 1.0

    def click_area(self, area):
        self.device.click(Button(area, color=(255, 255, 255), button=area))

    def abort_if_buying(self):
        """
        误入购买流时立刻退出，禁止碰「兑换」按钮。

        只用 SHOP_BUY_CONFIRM（点击「兑换」后才会出现的二级确认对话框）判据：
        SHOP_BUY_CONFIRM_AMOUNT 的区域 (756,622,827,653) 正好落在兑换弹窗
        「兑换」按钮 ≈(727,608,890,662) 内部，用它判购买流会在正常兑换弹窗上
        误报，导致每个箱子都被中止。

        Returns:
            bool: True 表示已中止该箱
        """
        if self.appear(SHOP_BUY_CONFIRM, offset=(20, 40)):
            logger.warning('Gear skin: entered purchase flow, abort')
            self.device.click(SHOP_CLICK_SAFE_AREA)
            self.click_area(EXCHANGE_CANCEL)
            return True
        return False

    def close_info(self):
        self.click_area(INFO_CONFIRM)
        timer = Timer(3, count=6).start()
        for _ in self.loop():
            if '取消' in OCR_EXCHANGE_CANCEL.ocr(self.device.image):
                break
            if timer.reached():
                logger.warning('Gear skin: exchange popup not back after closing info')
                break
        self.close_exchange()

    def close_exchange(self):
        self.click_area(EXCHANGE_CANCEL)
        # 等兑换弹窗真的消失，避免残留弹窗污染下一个箱子的物品扫描
        timer = Timer(3, count=6).start()
        for _ in self.loop():
            if '取消' not in OCR_EXCHANGE_CANCEL.ocr(self.device.image):
                break
            if timer.reached():
                logger.warning('Gear skin: exchange popup still visible')
                break
        self.event_shop_handle_obstruct()

    def save_error(self):
        try:
            os.makedirs(ERROR_FOLDER, exist_ok=True)
            file = os.path.join(
                ERROR_FOLDER,
                'gear_skin_{}.png'.format(datetime.now().strftime('%Y%m%d_%H%M%S'))
            )
            save_image(self.device.image, file)
            logger.info(f'Gear skin: screenshot saved to {file}')
        except Exception as e:
            logger.warning(f'Gear skin: save screenshot failed {e}')
