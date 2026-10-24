# -*- coding: utf-8 -*-
from module.config.utils import read_file, write_file

TEXTS = {
    'zh-CN': {
        'name': '魔改包辅助',
        'help': '使用魔改客户端时，每次进入游戏后自动打开辅助并隐藏灵动岛，'
                '避免悬浮窗挡住 MAA 的操作。\n'
                '由 ALAS 通过截图识别完成，需要模拟器支持 ALAS 截图（nemu_ipc / adbusrcap 等）。',
        'Enable': '启用启动辅助',
        'Password': '辅助密码',
        'Password_help': '魔改包的进入辅助密码，留空则使用辅助记住的密码。\n'
                         '流程会先清空密码框再输入，重复执行是安全的。',
    },
    'zh-TW': {
        'name': '魔改包輔助',
        'help': '使用魔改客戶端時，每次進入遊戲後自動開啟輔助並隱藏靈動島，'
                '避免懸浮窗擋住 MAA 的操作。\n'
                '由 ALAS 透過截圖識別完成，需要模擬器支援 ALAS 截圖（nemu_ipc / adbusrcap 等）。',
        'Enable': '啟用啟動輔助',
        'Password': '輔助密碼',
        'Password_help': '魔改包的進入輔助密碼，留空則使用輔助記住的密碼。\n'
                         '流程會先清空密碼框再輸入，重複執行是安全的。',
    },
    'en-US': {
        'name': 'Modded Client Assist',
        'help': 'When using a modded game client, opens the assist and hides its floating '
                'island after each game start, so the overlay does not block MAA.\n'
                'Done by ALAS via screen matching; requires an ALAS-supported screenshot method.',
        'Enable': 'Enable startup assist',
        'Password': 'Assist password',
        'Password_help': 'Password of the modded assist. Leave empty to use the remembered one.\n'
                         'The password box is cleared before typing, safe to run repeatedly.',
    },
    'ja-JP': {
        'name': '改造クライアント補助',
        'help': '改造クライアント使用時、ゲーム起動後に毎回アシストを開き、'
                'Dynamic Island を非表示にして MAA の操作を妨げないようにします。\n'
                'ALAS のスクリーンショット認識で実行します。',
        'Enable': '起動アシストを有効化',
        'Password': 'アシストパスワード',
        'Password_help': 'アシストのパスワード。空欄の場合は記憶済みパスワードを使用します。\n'
                         '入力前にパスワード欄をクリアするため、繰り返し実行しても安全です。',
    },
}

for lang, t in TEXTS.items():
    path = './submodule/AlasMaaBridge/module/config/i18n/%s.json' % lang
    data = read_file(path)
    data['MaaAssist'] = {
        '_info': {'name': t['name'], 'help': t['help']},
        'Enable': {'name': t['Enable'], 'help': ''},
        'Password': {'name': t['Password'], 'help': t['Password_help']},
    }
    write_file(path, data)
    print('i18n %s done' % lang)
