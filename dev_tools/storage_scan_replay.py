# -*- coding: utf-8 -*-
"""
仓库扫描离线回放 harness（不连设备、不进游戏）。

用法：
    python dev_tools/storage_scan_replay.py                  # 跑全部断言，退出码 0 = 全绿
    python dev_tools/storage_scan_replay.py --write-templates # 从冻结截图重提数字模板资产
    python dev_tools/storage_scan_replay.py --diag            # 打印逐格诊断

素材：doc/storage-shots/ 下的冻结截图（只读，禁止修改）。
真值表见 GROUND_TRUTH，由人工从截图肉眼读取并与游戏实况核对。
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
from module.storage.scanner import MaterialScanner, match_whitelist

SHOT_DIR = os.path.join(REPO_ROOT, 'doc', 'storage-shots')
TEMPLATE_PATH = os.path.join(REPO_ROOT, 'module', 'storage', 'amount_digits.json')

# 页面级真值：白名单基础名（display_name 之前）-> 数量
GROUND_TRUTH = {
    '10_storage.png': {'特装型突破': 257, '舰炮研发图': 40, '鱼雷研发图': 68,
                       '防空炮研发图': 47, '舰载机研发图': 46, '绝密实验计划': 16},
    '11_p1.png': {'鱼雷研发图': 68, '防空炮研发图': 47, '舰载机研发图': 46,
                  '绝密实验计划': 16, '装备研发图': 209},
    '14_p4.png': {'特装原型': 253161, '心智单元': 66323, '心智单元II': 15929,
                  '通用部件T4': 5262, '主炮部件T4': 5295, '鱼雷部件T4': 5364,
                  '防空炮部件T4': 4565, '舰载机部件T4': 3253, '试作型突破': 1204},
    '15_p5.png': {'鱼雷部件T4': 5364, '防空炮部件T4': 4565, '舰载机部件T4': 3253,
                  '试作型突破': 1204},
    '16_p6.png': {'舰炮研发图': 902, '鱼雷研发图': 968, '防空炮研发图': 933,
                  '舰载机研发图': 832, '喵箱SSR型': 501},
    '13_p3.png': {'心智单元': 66323, '心智单元II': 15929},
}
# 这些页面不应产生任何白名单读数
NO_HIT_PAGES = ['12_p2.png', '17_p7.png', '18_p8.png']
# 污染专项：非白名单格 机密实验计划（真值 960），(页面, 格 x, 图标区顶 y)
# 17_p7 顶行格子被页面上沿裁掉一部分，图标区顶为 29（名条顶 153）
POLLUTION_CASES = [('16_p6.png', 776, 255), ('17_p7.png', 776, 29)]

WHITE_THRESHOLD = 215
BAND_LEFT = 40
BAND_RIGHT = 124
DIGIT_TOP = 101
DIGIT_H = 18

_KNOWN_CACHE = None


def load_shot(name):
    path = os.path.join(SHOT_DIR, name)
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError('failed to load %s' % path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def build_scanner():
    """绕过设备初始化，手工装配 OCR 组件（人工已验证可离线运行）。"""
    scanner = MaterialScanner.__new__(MaterialScanner)
    scanner.ocr_name = Ocr([], lang='cnocr', letter=(255, 255, 255),
                           threshold=128, name='SSName')
    scanner.ocr_name_retry = Ocr([], lang='cnocr', letter=(255, 255, 255),
                                 threshold=96, name='SSName2')
    scanner.ocr_amount = AmountOcr([], threshold=MaterialScanner.AMOUNT_THRESHOLD,
                                   name='SSAmount')
    scanner.votes = {}
    scanner._templates = None
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


def digit_mask(image, x, icon_top):
    band = image[icon_top + DIGIT_TOP:icon_top + DIGIT_TOP + DIGIT_H,
                 x + BAND_LEFT:x + BAND_RIGHT]
    return ((band[:, :, 0] > WHITE_THRESHOLD) & (band[:, :, 1] > WHITE_THRESHOLD)
            & (band[:, :, 2] > WHITE_THRESHOLD))


def column_groups(mask):
    """白像素列分组（间隙 <=1 视为同组，避免字内笔画被拆断）。"""
    cols = mask.any(axis=0)
    groups = []
    start = None
    for i, v in enumerate(cols):
        if v and start is None:
            start = i
        elif not v and start is not None:
            groups.append((start, i - 1))
            start = None
    if start is not None:
        groups.append((start, len(cols) - 1))
    merged = []
    for group in groups:
        if merged and group[0] - merged[-1][1] <= 1:
            merged[-1] = (merged[-1][0], group[1])
        else:
            merged.append(group)
    return merged


def known_cells(scanner):
    """
    从冻结截图定位"真值已知"的格子，用于模板提取与逐格断言。
    (页面, x, icon_top, 真值)，真值来自 GROUND_TRUTH / 污染专项。
    """
    global _KNOWN_CACHE
    if _KNOWN_CACHE is not None:
        return _KNOWN_CACHE
    cells = []
    for page, truth in GROUND_TRUTH.items():
        image = load_shot(page)
        for x, icon_top, label, name in page_cells(scanner, image):
            matched = match_whitelist(name, scanner.cell_quality(image, x, icon_top))
            if not matched:
                continue
            if matched == '心智单元':
                # 页面上同名的两格（心智单元 / 心智单元II）用名条像素区分
                if scanner._has_ii_suffix(label):
                    matched = '心智单元II'
            if matched not in truth:
                continue
            cells.append((page, x, icon_top, truth[matched]))
        if page == '16_p6.png':
            # 机密实验计划（非白名单，真值 960，肉眼可核）
            cells.append((page, 776, 255, 960))
    _KNOWN_CACHE = cells
    return cells


def extract_templates(scanner):
    """从已知格提取 0-9 数字模板：从右往左按真值配对，遇宽组（卡面画）即停。"""
    raw_instances = {}
    skipped = []
    for page, x, icon_top, truth in known_cells(scanner):
        mask = digit_mask(load_shot(page), x, icon_top)
        groups = column_groups(mask)
        digits = str(truth)[::-1]
        assigned = 0
        for gi, (a, b) in enumerate(reversed(groups)):
            if gi >= len(digits):
                break
            width = b - a + 1
            if width > 13:
                # 与卡面白画粘连的组不可靠，放弃该位及其左侧
                break
            raw_instances.setdefault(digits[gi], []).append(mask[:, a:b + 1])
            assigned += 1
        if assigned < len(digits):
            skipped.append('%s x=%d top=%d %d(%d/%d)' % (page, x, icon_top, truth,
                                                        assigned, len(digits)))
    return raw_instances, skipped


def write_templates(raw_instances):
    payload = {}
    for digit, masks in sorted(raw_instances.items()):
        seen = []
        rows_list = []
        for mask in masks:
            rows = [''.join('1' if v else '0' for v in row) for row in mask]
            key = tuple(rows)
            if key in seen:
                continue
            seen.append(key)
            rows_list.append(rows)
        payload[digit] = rows_list
    with open(TEMPLATE_PATH, 'w', encoding='utf-8') as f:
        import json
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print('templates written: %s' % TEMPLATE_PATH)
    for digit in sorted(payload):
        print('  %s: %d instance(s) %s' % (digit, len(payload[digit]),
                                           [len(r[0]) for r in payload[digit]]))


def test_known_cells(scanner):
    ok = True
    cells = known_cells(scanner)
    print('  %d cells' % len(cells))
    for page, x, icon_top, truth in cells:
        got = scanner.read_amount(load_shot(page), x, icon_top)
        if got != truth:
            ok = False
            print('  BAD %-14s x=%4d top=%3d truth=%s got=%s'
                  % (page, x, icon_top, truth, got))
    return ok


def test_pollution(scanner):
    ok = True
    for page, x, icon_top in POLLUTION_CASES:
        got = scanner.read_amount(load_shot(page), x, icon_top)
        print('  %s 机密实验计划 (x=%d, icon_top=%d) -> %s (truth 960)'
              % (page, x, icon_top, got))
        if got != 960:
            ok = False
            print('    BAD  except 960')
    return ok


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


def test_get_records(scanner):
    scanner.votes = {
        ('心智单元', 'gold'): {17434: 1, 144460: 1},
        ('心智单元II', 'gold'): {17434: 2},
    }
    records = scanner.get_records()
    print('  get_records -> %s' % records)
    expected = {'心智单元': 144460, '心智单元II': 17434}
    ok = records == expected
    if not ok:
        print('    BAD  expect %s' % expected)
    for key in records:
        if '(金)' in key or '(紫)' in key:
            ok = False
            print('    BAD  illegal quality suffix key %s' % key)
    # 截断尾缀合并：43/243 -> 243，票数合并后确认
    scanner.votes = {('试作型突破', 'purple'): {43: 1, 243: 1}}
    if scanner.get_records() != {'试作型突破部件': 243}:
        ok = False
        print('    BAD  suffix merge -> %s' % scanner.get_records())
    # 多页一致才算确认：单页单次读数不确认
    scanner.votes = {('试作型突破', 'purple'): {1204: 1}}
    if scanner._confirmed_names():
        ok = False
        print('    BAD  single read should not be confirmed')
    scanner.votes = {('试作型突破', 'purple'): {1204: 2},
                     ('心智单元', 'gold'): {144460: 1, 17434: 1}}
    if scanner._confirmed_names() != {'试作型突破'}:
        ok = False
        print('    BAD  confirmed names -> %s' % scanner._confirmed_names())
    return ok


def test_ii_suffix(scanner):
    """R1：cnocr 丢尾部 II 时靠名条像素判定归入 心智单元II。"""
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


def main():
    scanner = build_scanner()
    if '--debug-cell' in sys.argv:
        i = sys.argv.index('--debug-cell')
        page, x, icon_top = sys.argv[i + 1], int(sys.argv[i + 2]), int(sys.argv[i + 3])
        mask = digit_mask(load_shot(page), x, icon_top)
        print('groups %s' % column_groups(mask))
        for row in mask:
            print('  ' + ''.join('#' if v else '.' for v in row))
        print('read_amount -> %s' % scanner.read_amount(load_shot(page), x, icon_top))
        tpls = scanner.digit_templates()
        print('_match_amount -> %s' % scanner._match_amount(load_shot(page), x, icon_top, tpls))
        return 0
    if '--write-templates' in sys.argv:
        raw, skipped = extract_templates(scanner)
        write_templates(raw)
        for item in skipped:
            print('  skipped partial: %s' % item)
        return 0
    diag = '--diag' in sys.argv
    results = []
    print('[0] 已知数量格逐格读取')
    results.append(('known', test_known_cells(scanner)))
    print('[1] 页面真值回放')
    results.append(('pages', test_pages(scanner, diag)))
    print('[2] 数量污染专项')
    results.append(('pollution', test_pollution(scanner)))
    print('[3] get_records / 覆盖确认 单元断言')
    results.append(('records', test_get_records(scanner)))
    print('[4] 名条 II 像素判定')
    results.append(('ii_suffix', test_ii_suffix(scanner)))
    print('')
    failed = [name for name, ok in results if not ok]
    if failed:
        print('FAILED: %s' % ', '.join(failed))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
