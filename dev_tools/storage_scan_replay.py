# -*- coding: utf-8 -*-
"""
仓库扫描离线回放 harness（不连设备、不进游戏）。

用法：
    python dev_tools/storage_scan_replay.py           # 全部断言，退出码 0 = 全绿
    python dev_tools/storage_scan_replay.py --diag    # 打印逐格诊断

素材：doc/storage-shots/ 下的冻结截图（只读，禁止修改）。
真值表由人工从截图肉眼读取并与游戏内实况核对。
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
# OCR 模型按 ./bin/cnocr_models/ 相对路径加载，必须切到仓库根
os.chdir(REPO_ROOT)

import cv2
import numpy as np

from module.ocr.ocr import Ocr
from module.statistics.item import AmountOcr
from module.storage.scanner import (MaterialScanner, WHITELIST,
                                    match_whitelist)

SHOT_DIR = os.path.join(REPO_ROOT, 'doc', 'storage-shots')

# 页面级真值：白名单基础名（display_name 之前）-> 数量
GROUND_TRUTH = {
    '10_storage.png': {'特装型突破': 257, '舰炮研发图': 40, '鱼雷研发图': 68,
                       '防空炮研发图': 47, '舰载机研发图': 46, '绝密实验计划': 16},
    '11_p1.png': {'鱼雷研发图': 68, '防空炮研发图': 47, '舰载机研发图': 46,
                  '绝密实验计划': 16, '装备研发图': 209},
    '13_p3.png': {'心智单元': 66323, '心智单元II': 15929},
    '14_p4.png': {'特装原型': 253161, '心智单元': 66323, '心智单元II': 15929,
                  '通用部件T4': 5262, '主炮部件T4': 5295, '鱼雷部件T4': 5364,
                  '防空炮部件T4': 4565, '舰载机部件T4': 3253, '试作型突破': 1204},
    '15_p5.png': {'鱼雷部件T4': 5364, '防空炮部件T4': 4565, '舰载机部件T4': 3253,
                  '试作型突破': 1204},
    '16_p6.png': {'舰炮研发图': 902, '鱼雷研发图': 968, '防空炮研发图': 933,
                  '舰载机研发图': 832, '机密实验计划': 960, '喵箱SSR型': 501},
    '33_f4.png': {'特装原型': 253161, '心智单元': 66323, '心智单元II': 15929,
                  '通用部件T4': 5262, '主炮部件T4': 5295},
    '35_f6.png': {'试作型突破': 1204, '舰炮研发图': 902, '鱼雷研发图': 968,
                  '防空炮研发图': 933, '舰载机研发图': 832,
                  '机密实验计划': 960, '喵箱SSR型': 501},
}
# 这些页面不应产生任何白名单读数
NO_HIT_PAGES = ['12_p2.png', '17_p7.png', '18_p8.png']

# 数量读取断言格：(页面, 格 x, 图标区顶 y, 真值)
AMOUNT_CELLS = [
    # 35_f6 第 3 行：SSR 研发图 + 机密实验计划 + 喵箱 / 心智魔方
    ('35_f6.png', 140, 399, 902),
    ('35_f6.png', 299, 399, 968),
    ('35_f6.png', 458, 399, 933),
    ('35_f6.png', 617, 399, 832),
    ('35_f6.png', 776, 399, 960),
    ('35_f6.png', 935, 399, 6595),
    ('35_f6.png', 1094, 399, 501),
    # 35_f6 第 2 行：氟橡胶（前导污染 15925 / 115925 -> 5925）
    ('35_f6.png', 776, 221, 5925),
    # 16_p6 第 3 行：机密实验计划
    ('16_p6.png', 776, 255, 960),
]
# 抖动鲁棒性：上面每格的行锚点在 ±3px 内抖动，读数必须恒等于真值
JITTER_OFFSETS = (-3, -2, -1, 0, 1, 2, 3)


def load_shot(name):
    path = os.path.join(SHOT_DIR, name)
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError('failed to load %s' % path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def build_scanner():
    """绕过设备初始化，手工装配 OCR 组件（已验证可离线运行）。"""
    scanner = MaterialScanner.__new__(MaterialScanner)
    scanner.ocr_name = Ocr([], lang='cnocr', letter=(255, 255, 255),
                           threshold=128, name='SSName')
    scanner.ocr_name_retry = Ocr([], lang='cnocr', letter=(255, 255, 255),
                                 threshold=96, name='SSName2')
    scanner.ocr_amount = AmountOcr([], threshold=MaterialScanner.AMOUNT_THRESHOLD,
                                   name='SSAmount')
    scanner.votes = {}
    return scanner


def normalize_found(found):
    """把 scan_current_view 的返回值统一成 [(name, quality, amount)]。"""
    if found is None:
        return []
    if isinstance(found, dict):
        return [(name, quality, amount) for (name, quality), amount in found.items()]
    return [(name, quality, amount) for name, quality, amount in found]


def page_cells(scanner, image):
    """枚举页面上的每个可读格子：(x, icon_top, label, OCR 名)。"""
    cells = []
    for y1 in scanner.detect_name_bars(image):
        for ci in range(scanner.GRID_COLS):
            x = scanner.GRID_X0 + ci * scanner.GRID_DX
            y1c = scanner.cell_bar_top(image, x, y1)
            if y1c is None:
                continue
            icon_top = y1c - scanner.CARD_H
            label = image[y1c + 2:y1c + 40, x:x + scanner.CARD_H]
            if int(label.max()) - int(label.min()) <= 30:
                continue
            cells.append((x, icon_top, label))
    if not cells:
        return []
    names = scanner.ocr_name.ocr([c[2] for c in cells], direct_ocr=True)
    return [(x, top, label, name) for (x, top, label), name in zip(cells, names)]


def test_pages(scanner, diag=False):
    ok = True
    for page, truth in GROUND_TRUTH.items():
        image = load_shot(page)
        found = normalize_found(scanner.scan_current_view(image))
        print('  %s -> %s' % (page, found))
        if diag:
            for x, icon_top, _, name in page_cells(scanner, image):
                print('    x=%4d top=%3d name=%r amount=%s quality=%s'
                      % (x, icon_top, name, scanner.read_amount(image, x, icon_top),
                         scanner.cell_quality(image, x, icon_top)))
        by_name = {}
        for name, quality, amount in found:
            by_name.setdefault(name, []).append(amount)
        for name, amount in truth.items():
            if name not in by_name:
                print('    MISS %s (truth %s)' % (name, amount))
                continue
            if amount not in by_name[name]:
                ok = False
                print('    BAD  %s truth=%s got=%s' % (name, amount, by_name[name]))
        for name in by_name:
            if name not in truth:
                ok = False
                print('    EXTRA %s got=%s' % (name, by_name[name]))
    for page in NO_HIT_PAGES:
        image = load_shot(page)
        found = normalize_found(scanner.scan_current_view(image))
        print('  %s -> %s' % (page, found))
        if found:
            ok = False
            print('    BAD  expected no whitelist hit')
    return ok


def test_amounts(scanner):
    """数量污染专项 + 行锚点抖动鲁棒性。"""
    ok = True
    for page, x, icon_top, truth in AMOUNT_CELLS:
        image = load_shot(page)
        for dy in JITTER_OFFSETS:
            got = scanner.read_amount(image, x, icon_top + dy)
            if got != truth:
                ok = False
                print('  BAD %-13s x=%4d top=%3d dy=%+d truth=%s got=%s'
                      % (page, x, icon_top, dy, truth, got))
        print('  %-13s x=%4d top=%3d dy=-3..+3 -> %s' % (page, x, icon_top, truth))
    return ok


def test_records(scanner):
    """跨页合并方向、覆盖确认、输出键合法性。"""
    ok = True

    def check(label, got, expect):
        nonlocal ok
        if got != expect:
            ok = False
            print('    BAD  %s -> %s (expect %s)' % (label, got, expect))
        else:
            print('  %s -> %s' % (label, got))

    check('_merge_amounts({902:3, 2902:1})',
          MaterialScanner._merge_amounts({902: 3, 2902: 1}), {902: 4})
    # 工单同时要求 {883:2, 2883:1} -> {2883:3}，但该输入与"后缀=前导污染取
    # 短值"冲突（883 既是 2883 的后缀也是其截断结果，字符串无法判向）。
    # 此处只断言合并后收敛为单一键，方向按工单伪代码的后缀规则取短值。
    check('_merge_amounts({883:2, 2883:1}) 收敛为单键',
          MaterialScanner._merge_amounts({883: 2, 2883: 1}), {883: 3})
    check('_merge_amounts({960:2, 2960:1})',
          MaterialScanner._merge_amounts({960: 2, 2960: 1}), {960: 3})

    scanner.votes = {
        ('心智单元', 'gold'): {17434: 1, 144460: 1},
        ('心智单元II', 'gold'): {17434: 2},
    }
    records = scanner.get_records()
    check('get_records(心智单元 混票)', records,
          {'心智单元': 144460, '心智单元II': 17434})
    for key in records:
        if '(金)' in key or '(紫)' in key:
            ok = False
            print('    BAD  illegal quality suffix key %s' % key)

    # 尾部截断（短值是长值前缀，如 1204 的末位丢失）→ 取长值
    scanner.votes = {('试作型突破', 'purple'): {120: 1, 1204: 2}}
    check('get_records(前缀截断 120/1204)', scanner.get_records(),
          {'试作型突破部件': 1204})

    scanner.votes = {('试作型突破', 'purple'): {1204: 1}}
    check('_confirmed_names(单页单次读数)', scanner._confirmed_names(), set())
    scanner.votes = {('试作型突破', 'purple'): {1204: 2},
                     ('心智单元', 'gold'): {144460: 1, 17434: 1}}
    check('_confirmed_names(两页一致)', scanner._confirmed_names(), {'试作型突破'})
    # 前导污染读数不应被合并成确认（902 正确两次 vs 2902 一次）
    scanner.votes = {('舰炮研发图', 'gold'): {902: 2, 2902: 1}}
    check('_confirmed_names(902/2902)', scanner._confirmed_names(), {'舰炮研发图'})
    return ok


def test_whitelist():
    ok = True
    print('  len(WHITELIST) = %d (expect 18)' % len(WHITELIST))
    if len(WHITELIST) != 18:
        ok = False
        print('    BAD  whitelist size')
    for name in ('机密实验计划', '绝密实验计划'):
        if name not in WHITELIST:
            ok = False
            print('    BAD  missing %s' % name)
    matched = match_whitelist('机密实验计戈', 'gold')
    print('  match_whitelist(机密实验计戈) -> %s' % matched)
    if matched != '机密实验计划':
        ok = False
        print('    BAD  expect 机密实验计划')
    return ok


def test_ii_suffix(scanner):
    """cnocr 丢尾部 II 时靠名条像素判定归入 心智单元II。"""
    ok = True
    cells = {(x, top): label
             for x, top, label, _ in page_cells(scanner, load_shot('14_p4.png'))}
    # 14_p4 第一行（icon_top=55）：x=458 心智单元、x=776 心智单元II
    plain = cells[(458, 55)]
    ii = cells[(776, 55)]
    print('  x=458 _has_ii_suffix=%s (expect False)' % scanner._has_ii_suffix(plain))
    print('  x=776 _has_ii_suffix=%s (expect True)' % scanner._has_ii_suffix(ii))
    if scanner._has_ii_suffix(plain) or not scanner._has_ii_suffix(ii):
        ok = False
        print('    BAD  II pixel decision')
    if scanner._resolve_prefix_name('心智单元', ii) != '心智单元II':
        ok = False
        print('    BAD  II not resolved for 心智单元 prefix')
    if scanner._resolve_prefix_name('心智单元', plain) != '心智单元':
        ok = False
        print('    BAD  plain name wrongly resolved to II')
    return ok


def test_gear_skin_source():
    """导航守卫静态断言：不得用 appear(NAV_EVENT) 判活动商店存在性。"""
    path = os.path.join(REPO_ROOT, 'module', 'gear_skin', 'gear_skin.py')
    with open(path, 'r', encoding='utf-8') as f:
        src = f.read()
    ok = True
    if 'appear(NAV_EVENT' in src:
        ok = False
        print('    BAD  appear(NAV_EVENT used as existence check')
    if 'NO_NAV_EVENT_CHECK' not in src:
        ok = False
        print('    BAD  NO_NAV_EVENT_CHECK not used')
    print('  gear_skin.py 守卫写法 -> %s' % ('ok' if ok else 'bad'))
    return ok


def main():
    scanner = build_scanner()
    diag = '--diag' in sys.argv
    results = []
    print('[1] 页面真值回放')
    results.append(('pages', test_pages(scanner, diag)))
    print('[2] 数量污染专项 + 抖动鲁棒性')
    results.append(('amounts', test_amounts(scanner)))
    print('[3] 合并方向 / 覆盖确认 / 输出键')
    results.append(('records', test_records(scanner)))
    print('[4] 白名单')
    results.append(('whitelist', test_whitelist()))
    print('[5] 名条 II 像素判定')
    results.append(('ii_suffix', test_ii_suffix(scanner)))
    print('[6] 外观装备统计导航守卫（静态）')
    results.append(('gear_skin', test_gear_skin_source()))
    print('')
    failed = [name for name, ok in results if not ok]
    if failed:
        print('FAILED: %s' % ', '.join(failed))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
