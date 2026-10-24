# -*- coding: utf-8 -*-
"""离线验证 AssistRunner 状态机（不启动设备、不启动 MAA）。

用 7 张真实截图按页面顺序喂给 mock device，
验证：主界面 -> 声明页(滑动) -> 同意 -> 密码页(输密码) -> 功能面板 -> 设置 -> 隐藏。
"""
import os
import sys
import types

sys.path.insert(0, os.path.abspath('.'))

import cv2

import submodule.AlasMaaBridge.module.handler.assist as assist_mod
# 跳过真实 sleep，测试瞬间完成
assist_mod.time.sleep = lambda s: None

SHOTS = r'.workbuddy/tmp/assist_shots'


def load(name):
    return cv2.imread('%s/%s.png' % (SHOTS, name))


class MockAdb(object):
    def __init__(self):
        self.cmds = []

    def shell(self, cmd, **kwargs):
        self.cmds.append(' '.join(cmd))


class MockDevice(object):
    def __init__(self, pages):
        self.pages = pages
        self.i = 0
        self.actions = []
        self.adb = MockAdb()
        self.config = None

    def screenshot(self):
        return self.pages[min(self.i, len(self.pages) - 1)]

    def click(self, button):
        self.actions.append(str(button))
        # 点击密码框不切换页面，其余点击推进一页
        if str(button) != 'ASSIST_PASSWORD_BOX':
            self.i += 1

    def swipe(self, p1, p2, **kwargs):
        self.actions.append('SWIPE')
        self.i += 1


class FakeConfig(object):
    MaaAssist_Enable = True
    MaaAssist_Password = '1072539774'


def case(title, pages, expect_actions):
    device = MockDevice(pages)
    runner = assist_mod.AssistRunner(FakeConfig(), device)
    runner.run()
    got = device.actions
    ok = got == expect_actions
    print('%s %s' % ('PASS' if ok else 'FAIL', title))
    print('   actions: %s' % got)
    if not ok:
        print('   expect : %s' % expect_actions)
    return ok


all_ok = True

# 1. 完整流程
all_ok &= case(
    '完整流程：主界面->声明->同意->密码->面板->设置->隐藏',
    [load(n) for n in
     ['decl_top', 'main_arrow', 'password', 'panel', 'main', 'settings', 'decl_top']],
    ['ASSIST_OPEN_ISLAND', 'SWIPE', 'ASSIST_AGREE_DECLARATION',
     'ASSIST_PASSWORD_BOX', 'ASSIST_ENTER_ASSIST',
     'ASSIST_OPEN_SETTINGS', 'ASSIST_HIDE_ISLAND'],
)

# 2. 首次声明已同意：密码页直达
all_ok &= case(
    '声明已同意过：主界面->密码页->面板->设置->隐藏',
    [load(n) for n in ['decl_top', 'panel', 'main', 'settings', 'decl_top']],
    ['ASSIST_OPEN_ISLAND', 'ASSIST_PASSWORD_BOX', 'ASSIST_ENTER_ASSIST',
     'ASSIST_OPEN_SETTINGS', 'ASSIST_HIDE_ISLAND'],
)

# 3. 面板已开着（上次没关）：直接进设置
all_ok &= case(
    '面板已打开：直接设置->隐藏',
    [load(n) for n in ['main', 'settings', 'decl_top']],
    ['ASSIST_OPEN_SETTINGS', 'ASSIST_HIDE_ISLAND'],
)

# 4. 灵动岛不存在（未安装魔改包 / 已彻底隐藏）
import numpy as np
all_ok &= case(
    '无灵动岛：直接跳过',
    [np.zeros((720, 1280, 3), dtype=np.uint8)],
    [],
)

# 5. 密码输入命令检查
device = MockDevice([load(n) for n in ['decl_top', 'panel', 'main', 'settings', 'decl_top']])
runner = assist_mod.AssistRunner(FakeConfig(), device)
runner.run()
pwd_cmds = [c for c in device.adb.cmds if 'text' in c]
key_cmds = [c for c in device.adb.cmds if 'keyevent' in c]
print('PASS 密码输入' if pwd_cmds == ['input text 1072539774'] and len(key_cmds) == 31
      else 'FAIL 密码输入', pwd_cmds, 'keyevent x%d' % len(key_cmds))
all_ok &= bool(pwd_cmds == ['input text 1072539774'] and len(key_cmds) == 31)

print('\n== ALL %s ==' % ('PASS' if all_ok else 'FAIL'))
