"""
魔改客户端的启动辅助。

部分魔改客户端（如带「灵动岛」悬浮窗的版本）在进入游戏后会弹出一层悬浮面板：
灵动岛 →（免责声明 / 辅助密码）→ 辅助设置面板 → 隐藏灵动岛。
这套面板会挡住 MAA 的识别区域，所以在「开始唤醒」之后先把它关掉。

实现上不依赖主模块的 Device（MAA 插件的设备层是 FakeDevice），
直接用 adbutils 的 `screencap` + `input tap/swipe` 完成截图与点击。
"""
import os
import random
import time

import cv2
import numpy as np

from module.base.timer import Timer
from module.logger import logger
from submodule.AlasMaaBridge.module.device.emulator import MaaEmulator

ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'assets', 'assist'
)

# 模板文件名 -> 截图中的出现区域 (x1, y1, x2, y2)，分辨率 1280x720
TEMPLATES = {
    'assist_sun': (584, 16, 618, 52),
    'assist_agree': (428, 565, 700, 627),
    'assist_enter': (435, 238, 575, 300),
    'assist_gear': (839, 26, 884, 66),
    'assist_hide': (425, 427, 700, 479),
    'assist_panel': (430, 163, 620, 196),
}


def imread_gray(path):
    """
    读入灰度图。

    Windows 下 cv2.imread 不支持非 ASCII 路径（本仓库运行目录可能含中文，
    如 D:/360极速浏览器X下载/...），会静默返回 None；改走 np.fromfile + imdecode。

    Returns:
        np.ndarray | None:
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except Exception as e:
        logger.info(e)
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def _read_stream(stream):
    """
    adbutils 0.11 的 read_until_close() 会按 utf-8 解码，二进制截图不能用，
    这里直接读 socket。
    """
    conn = getattr(stream, 'conn', stream)
    conn.settimeout(10)
    data = b''
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _decode_screencap(data):
    """
    `screencap` 输出的是 12 字节头 + RGBA 像素，末尾还有一段全 0 的附加数据，
    宽高从头部读，像素从尾部倒着取。
    """
    header = np.frombuffer(data[0:12], dtype=np.uint32)
    width, height, _ = header
    image = np.frombuffer(data, dtype=np.uint8)
    image = image[-int(width * height * 4):].reshape(height, width, 4)
    return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)


class MaaAssist:
    """
    关闭魔改客户端的启动悬浮面板。
    """

    THRESHOLD = 0.85
    # 齿轮在主界面/声明页也有 0.79~0.86 的分数，必须和「面板标题」一起判
    THRESHOLD_GEAR = 0.90
    MAX_SWIPE = 4
    MAX_LOOPS = 40
    TIMEOUT = 150
    # 声明页一次滚到底部的滑动，参数来自实际截图
    SCROLL = (640, 600, 640, 300, 600)

    def __init__(self, config):
        self.config = config
        self.emulator = MaaEmulator()
        self.serial = config.MaaEmulator_Serial
        self.templates = {}

    def run(self):
        """
        Returns:
            bool: True 表示已确认隐藏（或本就无需处理）
        """
        if not self.config.MaaAssist_Enable:
            logger.info('辅助启动: disabled')
            return False

        missing = []
        for name in TEMPLATES:
            file = os.path.join(ASSETS, name + '.png')
            image = imread_gray(file)
            if image is None:
                # 区分「文件不存在」与「文件在但读不出来」，后者常见于非 ASCII 路径
                missing.append(file + ('（文件存在但无法解码）' if os.path.exists(file) else ''))
                continue
            self.templates[name] = image
        if missing:
            # 一次报全：逐个 return 会让用户补一个跑一次
            logger.warning(f'辅助启动: 缺少模板 {len(missing)} 个：' + '、'.join(missing))
            logger.warning(f'请确认 {ASSETS} 下有 6 个 png，且路径不含无法读取的字符')
            return False

        try:
            return self._run()
        except Exception as e:
            logger.warning(f'辅助启动: failed {e}')
            return False

    def _run(self):
        timer = Timer(self.TIMEOUT).start()
        opened = False
        clicked_hide = False
        swipe_times = 0

        for _ in range(self.MAX_LOOPS):
            if timer.reached():
                logger.warning(f'辅助启动: skipped（处理超时 {self.TIMEOUT}s）')
                return False

            image = self.screenshot()
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            score = {name: self.match(gray, name) for name in self.templates}

            # 没有按钮 = 灵动岛已经隐藏，这是唯一的完成判据
            busy = max(
                score['assist_agree'], score['assist_enter'],
                score['assist_panel'], score['assist_hide'],
            )
            if clicked_hide and busy < self.THRESHOLD:
                logger.info('HIDE_ISLAND 灵动岛已隐藏，辅助设置完成')
                return True

            if score['assist_hide'] >= self.THRESHOLD:
                self.click('assist_hide')
                clicked_hide = True
                continue
            if score['assist_agree'] >= self.THRESHOLD:
                logger.info(f'AGREE score={score["assist_agree"]:.3f}')
                self.click('assist_agree')
                continue
            if score['assist_enter'] >= self.THRESHOLD:
                logger.info(f'ENTER_ASSIST score={score["assist_enter"]:.3f}')
                self.click('assist_enter')
                continue
            if (score['assist_panel'] >= self.THRESHOLD
                    and score['assist_gear'] >= self.THRESHOLD_GEAR):
                logger.info(f'OPEN_SETTINGS score={score["assist_panel"]:.3f}')
                self.click('assist_gear')
                continue
            if score['assist_sun'] >= self.THRESHOLD:
                if opened:
                    if swipe_times >= self.MAX_SWIPE:
                        logger.warning('辅助启动: failed（声明页滑动 4 次仍未找到按钮）')
                        return False
                    swipe_times += 1
                    logger.info(f'声明页向下滚动 {swipe_times}/{self.MAX_SWIPE}')
                    self.swipe(*self.SCROLL)
                    continue
                logger.info(f'OPEN_ISLAND score={score["assist_sun"]:.3f}')
                self.click('assist_sun')
                opened = True
                continue

            logger.info('辅助启动: skipped（未检测到灵动岛）')
            return False

        logger.warning(f'辅助启动: failed（超过 {self.MAX_LOOPS} 轮）')
        return False

    def screenshot(self):
        """
        Returns:
            np.ndarray: BGR 图像，分辨率 1280x720
        """
        client = MaaEmulator.adb_client()
        stream = client.shell(self.serial, 'screencap', stream=True)
        try:
            data = _read_stream(stream)
        finally:
            try:
                stream.close()
            except Exception:
                pass

        # adb shell 可能把 0x0a 转成 0x0d0a，逐一尝试还原
        for transform in (
            lambda x: x,
            lambda x: x.replace(b'\r\n', b'\n'),
            lambda x: x.replace(b'\r\r\n', b'\n'),
        ):
            try:
                image = _decode_screencap(transform(data))
            except Exception:
                continue
            if image.shape[:2] != (720, 1280):
                logger.warning(f'辅助启动: 分辨率 {image.shape[:2]} 不是 1280x720，跳过')
                raise RuntimeError('unsupported resolution')
            return image

        raise RuntimeError('无法解码截图')

    def match(self, image, name):
        """
        Returns:
            float: 归一化相关系数，0~1
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        result = cv2.matchTemplate(gray, self.templates[name], cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])

    def click(self, name):
        x1, y1, x2, y2 = TEMPLATES[name]
        x = random.randint(x1 + 4, x2 - 4)
        y = random.randint(y1 + 4, y2 - 4)
        self.emulator.shell(self.serial, f'input tap {x} {y}')
        time.sleep(0.5)

    def swipe(self, x1, y1, x2, y2, duration):
        self.emulator.shell(self.serial, f'input swipe {x1} {y1} {x2} {y2} {duration}')
        time.sleep(1.0)
