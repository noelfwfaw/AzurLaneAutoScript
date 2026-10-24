import re
from datetime import datetime, timedelta

import cv2
import numpy as np

import module.config.server as server

from module.base.timer import Timer
from module.base.utils import color_similar, get_color
from module.campaign.assets import (
    OCR_COIN,
    OCR_COIN_LIMIT,
    OCR_EVENT_PT,
    OCR_OIL,
    OCR_OIL_CHECK,
    OCR_OIL_LIMIT,
)
from module.config.deep import deep_get
from module.logger import logger
from module.log_res.log_res import LogRes
from module.ocr.ocr import Digit, Ocr
from module.ui.ui import UI

if server.server != 'jp':
    OCR_COIN = Digit(OCR_COIN, name='OCR_COIN', letter=(239, 239, 239), threshold=128)
else:
    OCR_COIN = Digit(OCR_COIN, name='OCR_COIN', letter=(201, 201, 201), threshold=128)


class PtOcr(Ocr):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, lang='azur_lane', alphabet='X0123456789', **kwargs)

    def pre_process(self, image):
        """
        Args:
            image (np.ndarray): Shape (height, width, channel)

        Returns:
            np.ndarray: Shape (width, height)
        """
        # MIN(r, g, b) of the inverted image == inverted MAX(r, g, b)
        r, g, b = cv2.split(image)
        cv2.max(r, g, dst=r)
        cv2.max(r, b, dst=r)
        cv2.bitwise_not(r, dst=r)
        # Remove background, 0-192 => 0-255
        cv2.convertScaleAbs(r, alpha=255 / 192, dst=r)
        return r


OCR_PT = PtOcr(OCR_EVENT_PT)


class CampaignStatus(UI):
    # Dashboard.Pt 的刷新间隔。PT 只在活动图主界面右上角可见（OCR_EVENT_PT
    # 区域 (1196, 109, 1280, 131)），OCR 有开销，普通刷图时每轮都读不划算。
    EVENT_PT_LOG_INTERVAL = timedelta(minutes=30)

    def get_event_pt(self, update=False):
        """
        Returns:
            int: PT amount, or 0 if unable to parse
        """
        pt = OCR_PT.ocr(self.device.image)

        res = re.search(r'X(\d+)', pt)
        if res:
            pt = int(res.group(1))
            logger.attr('Event_PT', pt)
            LogRes(self.config).Pt = pt
        else:
            logger.warning(f'Invalid pt result: {pt}')
            pt = 0
        if update:
            self.config.update()
        return pt

    def update_event_pt(self, interval=None):
        """
        把活动 PT 同步到仪表盘，与 EventGeneral.PtLimit 无关。

        Pages:
            in: page_event or page_sp（右上角能看到 PT 的界面）

        Returns:
            int: 本次读到的 PT；被节流跳过时返回缓存值（可能为 0）
        """
        interval = interval or self.EVENT_PT_LOG_INTERVAL
        # LogRes 是往 config.modified 写的，不会立刻进 config.data，
        # 只读 data 会让节流失效（每轮都 OCR）
        record = self.config.modified.get(
            'Dashboard.Pt.Record',
            deep_get(self.config.data, 'Dashboard.Pt.Record', default=None),
        )
        if isinstance(record, datetime) and record > datetime(2023, 1, 1):
            if datetime.now() - record < interval:
                return deep_get(self.config.data, 'Dashboard.Pt.Value', default=0)

        return self.get_event_pt()

    def get_coin(self, skip_first_screenshot=True, update=False):
        """
        Returns:
            int: Coin amount
        """
        _coin = {}
        timeout = Timer(1, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('Get coin timeout')
                break

            _coin = {
                'Value': self._get_num(OCR_COIN, 'OCR_COIN'),
                'Limit': self._get_num(OCR_COIN_LIMIT, 'OCR_COIN_LIMIT'),
            }
            if _coin['Value'] >= 100:
                break
        LogRes(self.config).Coin = _coin
        if update:
            self.config.update()

        return _coin['Value']

    def _get_oil(self):
        # Update offset
        _ = self.appear(OCR_OIL_CHECK)

        color = get_color(self.device.image, OCR_OIL_CHECK.button)
        if color_similar(color, OCR_OIL_CHECK.color):
            # Original color
            if server.server != 'jp':
                ocr = Digit(OCR_OIL, name='OCR_OIL', letter=(247, 247, 247), threshold=128)
            else:
                ocr = Digit(OCR_OIL, name='OCR_OIL', letter=(201, 201, 201), threshold=128)
        elif color_similar(color, (59, 59, 64)):
            # With black overlay
            ocr = Digit(OCR_OIL, name='OCR_OIL', letter=(165, 165, 165), threshold=128)
        else:
            logger.warning(f'Unexpected OCR_OIL_CHECK color')
            ocr = Digit(OCR_OIL, name='OCR_OIL', letter=(247, 247, 247), threshold=128)

        return ocr.ocr(self.device.image)

    def _get_num(self, _button, name):
        # Update offset
        _ = self.appear(OCR_OIL_CHECK)

        # 如果已经是 Digit 对象，直接使用
        if isinstance(_button, Digit):
            ocr = _button
        else:
            # 否则创建新的 Digit 对象
            color = get_color(self.device.image, OCR_OIL_CHECK.button)
            if color_similar(color, OCR_OIL_CHECK.color):
                # Original color
                if server.server != 'jp':
                    ocr = Digit(_button, name=name, letter=(247, 247, 247), threshold=128)
                else:
                    ocr = Digit(_button, name=name, letter=(201, 201, 201), threshold=128)
            elif color_similar(color, (59, 59, 64)):
                # With black overlay
                ocr = Digit(_button, name=name, letter=(165, 165, 165), threshold=128)
            else:
                logger.warning(f'Unexpected OCR_OIL_CHECK color')
                ocr = Digit(_button, name=name, letter=(247, 247, 247), threshold=128)

        return ocr.ocr(self.device.image)

    def get_oil(self, skip_first_screenshot=True, update=False):
        """
        Returns:
            int: Oil amount
        """
        _oil = {}
        timeout = Timer(1, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('Get oil timeout')
                break

            if not self.appear(OCR_OIL_CHECK, offset=(10, 2)):
                logger.info('No oil icon')
                continue

            _oil = {
                'Value': self._get_num(OCR_OIL, 'OCR_OIL'),
                'Limit': self._get_num(OCR_OIL_LIMIT, 'OCR_OIL_LIMIT'),
            }
            if _oil['Value'] >= 100:
                break
        LogRes(self.config).Oil = _oil
        if update:
            self.config.update()

        return _oil['Value']

    def is_balancer_task(self):
        """
        Returns:
             bool: If is event task but not daily event task
        """
        tasks = [
            'Event',
            'Event2',
            'Raid',
            'Coalition',
            'GemsFarming',
        ]
        command = self.config.Scheduler_Command
        if command in tasks:
            if self.config.Campaign_Event == 'campaign_main':
                return False
            else:
                return True
        else:
            return False
