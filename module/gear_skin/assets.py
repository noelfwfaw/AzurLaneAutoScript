"""
外观装备「信息」弹窗的几何常量（1280x720 原图实测）。

坐标来源：doc/prompt-gearskin-statistic.md 第 2 节，2026-10-25 CN 服实测。
"""

# 兑换弹窗（点外观装备箱后出现）
EXCHANGE_DETAIL_BUTTON = (391, 228, 485, 250)  # 「查看详情」
EXCHANGE_TITLE_AREA = (500, 100, 780, 130)  # 箱名（黄字）
EXCHANGE_CANCEL = (402, 608, 565, 662)  # 「取消」，中心约 (483, 635)
# 「兑换」按钮 ≈ (727, 608, 890, 662)，本功能禁止点击

# 「信息 INFORMATION」弹窗
INFO_TITLE_AREA = (355, 170, 855, 215)  # 标题栏「信息」
INFO_CONFIRM = (555, 485, 730, 540)  # 「确定」
INFO_CLOSE = (865, 170, 925, 217)  # 右上角 X
INFO_LIST_AREA = (347, 252, 935, 470)  # 网格视口

# 滚动：swipe 向上拖（y 大 -> 小）为「向下翻」，回顶反之
INFO_SCROLL_FROM = (640, 440)
INFO_SCROLL_TO = (640, 280)
INFO_SCROLL_TOP_FROM = (640, 280)
INFO_SCROLL_TOP_TO = (640, 440)

# 网格：5 列，图标左缘 x
INFO_COLUMNS = (382, 490, 598, 705, 812)
# 单列 OCR 横向范围（相对图标左缘）。
# 名称以图标中心 x+36 居中，最长 7 字约 91px（x-10 ~ x+81）；
# 列距 107.5px，右边界取 x+96 可避免读入右邻列的名称。
INFO_COLUMN_OFFSET = (-18, 96)

# 文字颜色（名称白字、拥有数金字），用于替代 cnocr 不提供的 box 检测
INFO_NAME_COLOR = (222, 222, 219)
INFO_AMOUNT_COLOR = (202, 180, 65)
INFO_MASK_THRESHOLD = 200
INFO_BAND_MIN_PIXELS = 3
INFO_BAND_HEIGHT = (8, 30)
# 名称带在拥有数带上方多少像素内（含同格内 373-374 的重叠）
INFO_PAIR_MAX_GAP = 24

INFO_MAX_SCROLL = 12
INFO_SCROLL_TIMEOUT = 300
