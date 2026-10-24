import os
import json
import ctypes
from datetime import datetime

from cached_property import cached_property

# In order for MAA submodule to exexute,
# it is necessary to load runtime before module PIL
if os.name == 'nt':
    # This DLL is the dependency for next DLL
    # Try loading to avoid mix different versions of runtime
    try:
        ctypes.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32/vcruntime140_1.dll'))
    except Exception as e:
        print(e)

    # This DLL must be loaded due to conflict issues
    ctypes.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32/msvcp140.dll'))

    # These DLLS are other DLLS that MAA depends on
    # Try loading to avoid mix different versions of runtime
    try:
        ctypes.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32/msvcp140_1.dll'))
        ctypes.WinDLL(os.path.join(os.environ['SystemRoot'], 'System32/concrt140.dll'))
    except Exception as e:
        print(e)

from alas import AzurLaneAutoScript, EMULATOR_CLOSE_MIN_WAIT
from module.base.decorator import del_cached_property
from submodule.AlasMaaBridge.module.asst.package_override import apply_package_override
from module.config.config import AzurLaneConfig
from module.device.env import IS_WINDOWS
from module.exception import RequestHumanTakeover
from module.logger import logger
from submodule.AlasMaaBridge.module.asst.updater import Updater as MaaUpdater
from submodule.AlasMaaBridge.module.asst.utils import Version
from submodule.AlasMaaBridge.module.config.config import ArknightsConfig
from submodule.AlasMaaBridge.module.device.emulator import MaaEmulator
from submodule.AlasMaaBridge.module.handler.handler import AssistantHandler
from submodule.AlasMaaBridge.module.logger import log_callback


class FakeDevice:
    @staticmethod
    def empty_func(*args, **kwargs):
        pass

    def __getattr__(self, item):
        return FakeDevice.empty_func


class MaaDevice(FakeDevice):
    """
    MAA 插件专用的设备层。

    其余方法（app_stop、release_during_wait、stuck_record_clear 等）仍走 FakeDevice 的空实现，
    这里只额外提供模拟器存活检查与游戏运行状态判定，供调度器复用 ALAS 主模块的调度循环。
    """

    # MAA 的 resource/config.json 读不到时使用的兜底映射
    GAME_PACKAGE = {
        'Official': 'com.hypergryph.arknights',
        'Bilibili': 'com.hypergryph.arknights.bilibili',
        'YoStarEN': 'com.YoStarEN.Arknights',
        'YoStarJP': 'com.YoStarJP.Arknights',
        'YoStarKR': 'com.YoStarKR.Arknights',
        'txwy': 'tw.txwy.and.arknights',
    }

    def __init__(self, script=None):
        self.script = script
        self.config = None
        # 仅供日志区分「已在线」与「本次拉起」，不参与任何调度判断
        self.emulator_started = False

    @property
    def _config(self):
        if self.config is not None:
            return self.config
        if self.script is not None:
            return self.script.config
        return None

    def device_alive(self):
        """
        模拟器是否在线。非 Windows 平台无模拟器概念，直接视为在线。
        """
        config = self._config
        if not IS_WINDOWS or config is None:
            return True
        return MaaEmulator().alive(config.MaaEmulator_Serial)

    def game_package(self):
        """
        当前服务器对应的游戏包名。

        优先读 MAA 自己的 `<MaaPath>/resource/config.json` 里的 packageName 表（MAA 维护，更抗更新），
        读不到再用内置映射；两者都拿不到时返回 None，调用方应当跳过游戏判据而不是判定「游戏没开」。

        Returns:
            str | None:
        """
        config = self._config
        if config is None:
            return None

        client = config.MaaEmulator_PackageName
        package = self._package_from_resource(config.MaaEmulator_MaaPath, client)
        if package:
            return package

        package = self.GAME_PACKAGE.get(client)
        if package:
            return package

        logger.warning(f'无法确定 {client} 的游戏包名，跳过游戏运行状态检查')
        return None

    @staticmethod
    def _package_from_resource(path, client):
        if not path:
            return None

        file = os.path.join(path, 'resource', 'config.json')
        try:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.info(e)
            return None

        packages = data.get('packageName', None)
        if not isinstance(packages, dict):
            return None
        package = packages.get(client, None)
        if isinstance(package, str) and package:
            return package
        return None

    def game_alive(self, timeout=2):
        """
        游戏进程是否在运行。

        设备不在线时直接返回 False —— 此时 adb 必然失败，不能把它当成「游戏没开」，所以调用方
        必须先确认模拟器在线（emulator_check）再来问游戏。

        Returns:
            bool: True 表示在运行或无法判定（宁可漏判也不误判）
        """
        if not IS_WINDOWS:
            return True
        if not self.device_alive():
            logger.info('模拟器不在线，跳过游戏进程检查')
            return False

        config = self._config
        if config is None:
            return True
        package = self.game_package()
        if package is None:
            logger.info('包名未知，已跳过游戏进程检查')
            return True

        emulator = MaaEmulator()
        serial = config.MaaEmulator_Serial
        # pidof 在个别魔改镜像上不可用，用 ps 兜底；adb 抖动导致的一次失败重试一次
        for _ in range(2):
            try:
                if emulator.shell(serial, f'pidof {package}', timeout=timeout).strip():
                    return True
            except Exception as e:
                logger.info(e)
            try:
                if package in emulator.shell(serial, 'ps -A', timeout=timeout):
                    return True
            except Exception as e:
                logger.info(e)

        # 把判据本身写进日志，便于区分「游戏真没开」与「包名/镜像不对」
        logger.info(f'游戏进程未找到：{package}（client_type={config.MaaEmulator_PackageName}）')
        return False

    def emulator_check(self):
        """
        运行期设备检查：模拟器不在时拉起它，并重连 MaaCore。

        这里只关心 adb 是否可达；拉起后游戏有没有起来，交给游戏层的 game_alive() 判断。

        Returns:
            bool: False 表示无法恢复，调用方应 RequestHumanTakeover
        """
        if not IS_WINDOWS:
            return True

        config = self._config
        if config is None:
            return True

        self.emulator_started = False
        emulator = MaaEmulator()
        serial = config.MaaEmulator_Serial
        try:
            if emulator.alive(serial):
                return True

            logger.critical(f'未检测到模拟器 {serial}，正在尝试启动')
            instance = emulator.find(serial)
            if instance is None:
                logger.critical(f'未找到 serial 为 "{serial}" 的模拟器实例')
                instances = emulator.list_instances()
                if instances:
                    logger.critical(
                        '可用实例：' + '、'.join(
                            f'{name} -> {candidate}' for name, candidate in instances
                        )
                    )
                else:
                    logger.critical('未找到任何已安装的模拟器实例，请确认模拟器已安装')
                logger.critical('请检查 MAA 设置中的模拟器 serial')
                return False

            if instance.serial != serial:
                logger.warning(f'模拟器 serial 已变化：{serial} -> {instance.serial}')
                config.MaaEmulator_Serial = instance.serial
                serial = instance.serial

            if not emulator.start(instance):
                logger.critical(f'模拟器启动命令发送失败：{instance.name}（{instance.path}）')
                return False

            serial = emulator.wait_online(instance)
            if not serial:
                logger.critical(
                    f'模拟器启动超时，adb 未上线（serial={instance.serial}，'
                    f'超时 {MaaEmulator.WAIT_ONLINE_TIMEOUT}s）'
                )
                return False

            if serial != config.MaaEmulator_Serial:
                logger.warning(f'模拟器 serial 已变化：{config.MaaEmulator_Serial} -> {serial}')
                config.MaaEmulator_Serial = serial

            self.maa_reconnect()
            self.emulator_started = True
            logger.info(f'模拟器已重启：{serial}')
            return True
        except RequestHumanTakeover:
            # 连接 MaaCore 失败属于需要人工介入的判断，交给上层直接退出
            raise
        except Exception as e:
            logger.exception(e)
            logger.critical('启动模拟器失败')
            return False

    def emulator_stop_and_wait(self, timeout=60, deadline=None):
        """
        关闭模拟器并等待它从 adb 设备列表消失。

        MuMu12 的 shutdown_player 是异步的，不等它关完就进入等待期的话，
        下个任务拉起模拟器时会与仍在销毁的实例撞车。

        Args:
            timeout (int): 最长等待时间
            deadline (datetime): 调度截止时间，到达后立即返回，不占用调度时间

        Returns:
            bool: 模拟器是否已确认关闭
        """
        config = self._config
        if not IS_WINDOWS or config is None:
            return False

        emulator = MaaEmulator()
        serial = config.MaaEmulator_Serial
        instance = emulator.find(serial)
        if instance is None:
            logger.warning(f'未找到 serial 为 "{serial}" 的模拟器实例，无法关闭模拟器')
            return False

        if not emulator.stop(instance):
            logger.warning('模拟器关闭命令发送失败')
            return False

        return emulator.wait_offline(serial, timeout=timeout, deadline=deadline)


class ArknightsAutoScript(AzurLaneAutoScript):
    # 本次「游戏未运行」是否已经插过一次开始唤醒；开始唤醒之后的第一个任务真正执行时重置。
    # 只作为初值，赋值都发生在实例上。
    _maa_wake_attempted = False

    @cached_property
    def device(self):
        return MaaDevice(self)

    @cached_property
    def config(self):
        try:
            config = ArknightsConfig(config_name=self.config_name)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @staticmethod
    def callback(self):
        pass

    @cached_property
    def asst(self):
        """
        跑 MAA 任务用的 MaaCore 实例。

        首次访问时会先插入一次「开始唤醒」，确保游戏已经启动。
        """
        return self.load_asst()

    def load_asst(self, check_task=True):
        """
        创建 MaaCore 实例，并完成 MaaCore 加载、回调注册与实例设置。

        Args:
            check_task (bool): 是否先检查并插入「开始唤醒」任务。
                只有任务队列清空后的等待期间才需要跳过——那时并不在任务里，
                触发 TaskEnd 会打断调度循环。

        Returns:
            Asst:
        """
        if check_task and self.config.task.command != 'Maa':
            self.config.task_call('MaaStartup', True)
            if self.config.task.command != 'MaaStartup':
                self.config.task_stop()

        # Fix MaaPath=*\MAA.exe
        if os.path.isfile(self.config.MaaEmulator_MaaPath):
            path = os.path.dirname(self.config.MaaEmulator_MaaPath)
            logger.info(f'MaaEmulator_MaaPath: {self.config.MaaEmulator_MaaPath} is revised to {path}')
            self.config.MaaEmulator_MaaPath = path

        logger.info(f'MAA安装路径：{self.config.MaaEmulator_MaaPath}')
        if not os.path.exists(self.config.MaaEmulator_MaaPath):
            logger.critical(
                f'未找到路径 {self.config.MaaEmulator_MaaPath}，请确认MAA已安装在该路径。'
                f'如果你是第一次使用MAA插件，需要自选安装MAA，并在 "MAA设置" - "MAA安装路径" 中填入MAA的安装路径')
            raise RequestHumanTakeover

        # 更新必须在加载 MaaCore 之前完成，否则 dll 被占用无法覆盖
        self.maa_update()

        # 包名映射必须在 AsstLoadResource() 之前注入：资源只在加载时读一次，
        # 而且 maa_update() 可能覆盖 resource/config.json，所以顺序不能反
        self.effective_package_name = apply_package_override(
            self.config.MaaEmulator_MaaPath,
            self.config.MaaEmulator_PackageName,
            self.config.MaaEmulator_CustomPackageName,
        )

        try:
            incremental_path = [os.path.join(self.config.MaaEmulator_MaaPath, './cache')]
            if self.config.MaaEmulator_PackageName in ["YoStarEN", "YoStarJP", "YoStarKR", "txwy"]:
                incremental_path.append(os.path.join(
                    self.config.MaaEmulator_MaaPath,
                    './resource/global/' + self.config.MaaEmulator_PackageName)
                )
                incremental_path.append(os.path.join(
                    self.config.MaaEmulator_MaaPath,
                    './cache/resource/global/' + self.config.MaaEmulator_PackageName)
                )
            # 不存在的增量目录会让 AsstLoadResource 返回 false，进而被当成资源加载失败
            incremental_path = [p for p in incremental_path if os.path.isdir(p)]
            AssistantHandler.load(self.config.MaaEmulator_MaaPath, incremental_path)
        except ModuleNotFoundError:
            logger.critical('找不到MAA，请检查安装路径是否正确')
            raise RequestHumanTakeover
        except OSError as e:
            # OSError: [WinError 126] 找不到指定的模块。
            if '[WinError 126]' in str(e):
                logger.exception(e)
                logger.critical(
                    f'无法导入MAA，请确认MAA已正确安装在 {self.config.MaaEmulator_MaaPath}'
                )
                raise RequestHumanTakeover
            else:
                raise

        # 资源已加载、但还没连过 MaaCore。在这里确认包确实存在，
        # 否则 MaaCore 会把 `am start` 失败当成连接层故障，白等 50 秒后报 Reconnect failed
        self.maa_verify_package()

        @AssistantHandler.Asst.CallBackType
        def callback(msg, details, arg):
            """
            Args:
                msg (int):
                details (bytes):
                arg (c_void_p):
            """
            d = json.loads(details.decode('utf-8', 'ignore'))
            try:
                m = AssistantHandler.Message(msg)
            except ValueError:
                # 新版 MaaCore 可能新增回调类型，未收录时不能让它把回调线程打挂
                logger.warning(f'未知的 MAA 回调类型 {msg}: {str(d)[:300]}')
                return
            log_callback(m, d)
            handler = AssistantHandler.ASST_HANDLER
            if handler:
                handler.callback_timer.reset()
                for func in handler.callback_list:
                    func(m, d)

        ArknightsAutoScript.callback = callback
        asst = AssistantHandler.Asst(callback)

        asst.set_instance_option(AssistantHandler.InstanceOptionType.touch_type, self.config.MaaEmulator_TouchMethod)
        asst.set_instance_option(AssistantHandler.InstanceOptionType.adblite_enabled, '0')
        if self.config.MaaEmulator_DeploymentWithPause:
            if self.config.MaaEmulator_TouchMethod == 'maatouch':
                asst.set_instance_option(AssistantHandler.InstanceOptionType.deployment_with_pause, '1')
            else:
                logger.critical('使用了不支持暂停下干员的触控方案')
                raise RequestHumanTakeover

        return asst

    def maa_update_options(self):
        """
        读取 MAA 更新设置。

        设置项属于 "Maa" 任务（MAA 设置页），不随当前运行的 MAA 任务绑定，
        因此使用 cross_get 直接读取配置文件。
        """
        config = self.config
        version_type = config.cross_get(keys='Maa.MaaUpdate.VersionType', default='Stable')
        if not isinstance(version_type, str) or version_type not in Version.__members__:
            version_type = 'Stable'
        return {
            'auto_resource': config.cross_get(keys='Maa.MaaUpdate.AutoUpdateResource', default=True),
            'auto_package': config.cross_get(keys='Maa.MaaUpdate.UpdatePackage', default=False),
            'version': Version[version_type],
            'client': config.MaaEmulator_PackageName,
        }

    def maa_update(self, force=False):
        """
        更新 MAA 任务资源（tasks.json）与 MAA 本体。

        必须在 AssistantHandler.load 之前调用，否则 MaaCore.dll 被占用无法覆盖。
        任何失败都只记录日志，不阻塞后续 MAA 任务。

        Args:
            force (bool): 忽略配置开关，强制检查更新（"更新 MAA" 工具调用）
        """
        path = self.config.MaaEmulator_MaaPath
        if not path or not os.path.isdir(path):
            return False

        options = self.maa_update_options()
        updater = MaaUpdater(path, options['version'])
        do_resource = force or options['auto_resource']
        do_package = force or options['auto_package']
        if not (do_resource or do_package or updater.has_pending()):
            return False

        logger.hr('MAA 更新检查', level=2)
        try:
            # 安装上次下载完成但未能安装的更新包（此时 MaaCore 尚未加载）
            updater.apply_pending()
            if do_resource:
                updater.update_resource(force=force, client=options['client'])
            if do_package:
                updater.check_package_update(apply_now=True)
            updater.clean_update_cache()
        except Exception as e:
            logger.exception(e)
            logger.warning('MAA 更新失败，将使用当前版本继续运行')
            return False
        return True

    def maa_asst(self):
        """
        取 MaaCore 实例，等待期间也能用。

        与 `asst` 的区别是不做「先跑开始唤醒」的检查，因此可以在任务之外调用。
        """
        # 与 `asst` 共用同一份缓存（cached_property 把结果存在实例 __dict__ 里）
        asst = self.__dict__.get('asst')
        if asst is not None:
            return asst

        asst = self.load_asst(check_task=False)
        self.__dict__['asst'] = asst
        return asst

    def maa_close_game_during_wait(self):
        """
        任务队列清空（等待下一个任务）时的收尾动作，
        对应 MAA 设置页「优化设置 - 完成所有任务后」。

        关游戏失败不需要在这里兜底，下个任务开始前的游戏判据会再补一次开始唤醒。
        """
        method = self.config.Optimization_WhenTaskQueueEmpty
        if method != 'close_game':
            logger.info('所有任务已完成，游戏停留在主页面待机')
            return

        # 设备不在线时不必连 MaaCore：那里会抛 RequestHumanTakeover，
        # 而「设备不在」在收尾路径上只是期望的终态，不是错误
        if not self.device.device_alive():
            logger.info('模拟器离线，无需关闭游戏')
            return

        logger.info('所有任务已完成，正在关闭游戏')
        try:
            handler = AssistantHandler(config=self.config, asst=self.maa_asst())
            handler.connect()
            handler.maa_start('CloseDown', {
                'client_type': self.config.MaaEmulator_PackageName,
            })
        except RequestHumanTakeover:
            # MaaCore 未连接，说明设备不在；这不是需要人工介入的错误
            logger.info('MaaCore 未连接，跳过关闭游戏（设备可能已离线）')
        except Exception as e:
            logger.exception(e)
            logger.warning('关闭游戏失败，下个任务开始前会重新唤醒游戏')

    def maa_reconnect(self):
        """
        模拟器重启后用新的 serial 重连 MaaCore。

        未加载过 MaaCore 时直接返回，交给首次任务里的 load_asst 处理。
        """
        asst = self.__dict__.get('asst')
        if asst is None:
            return
        handler = AssistantHandler(config=self.config, asst=asst)
        handler.connect()

    def maa_verify_package(self):
        """
        启动前确认设备上确实装了要用的包。

        MaaCore 把 `am start` 的失败当成连接层故障，会重连 5 轮、约 50 秒后才报
        `Disconnect: Reconnect failed`，把「包名不存在」伪装成「重连失败」。
        这里提前查一次，把 50 秒空转变成一条明确的错误。

        查询本身失败（设备不在线、adb 异常）时只告警不拦截：
        那不是「包名错误」，此时拦下来反而会误伤正常的等待流程。
        """
        target = self.__dict__.get('effective_package_name', None)
        if not target:
            return

        config = self.config
        serial = config.MaaEmulator_Serial
        try:
            output = MaaEmulator().shell(serial, 'pm list packages', timeout=10)
        except Exception as e:
            # 启动阶段设备可能还没起来，随后的启动检查会拉起它，这里不该喊 WARNING
            logger.info(f'无法查询设备 {serial} 的已安装包，跳过包名校验：{e}')
            return

        packages = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith('package:'):
                packages.append(line[len('package:'):].strip())
        if not packages:
            logger.info(f'设备 {serial} 未返回任何已安装包，跳过包名校验')
            return

        if target in packages:
            return

        custom = str(config.MaaEmulator_CustomPackageName or '').strip()
        related = [
            package for package in packages
            if any(key in package.lower() for key in ('arknights', 'hypergryph', 'yostar', 'txwy'))
        ][:5]
        logger.critical(f'设备 {serial} 上未安装包 {target}')
        logger.critical(
            f'当前「游戏服务器」为 {config.MaaEmulator_PackageName}，「自定义包名」={custom or "空"}'
        )
        if related:
            logger.critical(f'设备上看起来相关的包：{"、".join(related)}')
        else:
            logger.critical('设备上未找到任何疑似明日方舟的包')
        logger.critical(
            '若这就是你的客户端，请把它填入「MAA 设置 - 自定义包名」；否则请检查「游戏服务器」是否选错'
        )
        raise RequestHumanTakeover

    def maa_check_package_changed(self):
        """
        运行中改动「自定义包名」/「游戏服务器」后提示重启。

        资源一个进程只加载一次（Asst 是类级缓存），改配置后不重启不会重新注入包名映射。
        """
        signature = (
            self.config.MaaEmulator_PackageName,
            str(self.config.MaaEmulator_CustomPackageName or '').strip(),
        )
        last = self.__dict__.get('_maa_package_signature', signature)
        if signature != last:
            logger.warning('自定义包名已变更，需重启该 ALAS 实例才会重新加载 MAA 资源')
        self.__dict__['_maa_package_signature'] = signature

    def maa_stop_during_wait(self, deadline=None):
        """
        等待期内先关游戏再关模拟器，对应「完成所有任务后：关闭模拟器」。

        Args:
            deadline (datetime): 调度截止时间，到达后不再占用调度时间

        Returns:
            bool: True 表示模拟器已被关闭
        """
        # 设备不在线时不必连 MaaCore：那里会抛 RequestHumanTakeover，
        # 而「设备不在」在收尾路径上只是期望的终态，不是错误
        if not self.device.device_alive():
            logger.info('模拟器离线，无需关闭')
            return True

        try:
            handler = AssistantHandler(config=self.config, asst=self.maa_asst())
            handler.connect()
            handler.maa_start('CloseDown', {
                'client_type': self.config.MaaEmulator_PackageName,
            })
        except RequestHumanTakeover:
            # MaaCore 未连接，说明设备不在；这不是需要人工介入的错误
            logger.info('MaaCore 未连接，跳过关闭（设备可能已离线）')
        except Exception as e:
            logger.exception(e)
            logger.warning('关闭游戏失败，将继续关闭模拟器')

        # 二次兜底：关游戏期间模拟器可能已被自己或用户关掉
        if not self.device.device_alive():
            logger.info('模拟器已离线，无需再次关闭')
            return True

        stopped = self.device.emulator_stop_and_wait(deadline=deadline)
        if not stopped:
            # 关闭失败只降级为「关闭游戏」语义，不要中断等待流程
            logger.warning('关闭模拟器失败，将按关闭游戏处理')
        return stopped

    def _maa_watch_wait_loop(self, task):
        """
        护栏：连续进入等待分支、期间没有任何实质任务执行，判定为「关→启」自激循环。

        判据只能是「连续进入等待」，不能用「距上次关闭不足 N 秒」——用户关掉游戏后
        一分钟内手动加任务是正常操作，会被误杀。

        Args:
            task (Task): 本轮即将等待的任务
        """
        now = datetime.now()
        entries = self.__dict__.setdefault('_maa_wait_enters', [])
        entries.append((now, self.config.Optimization_WhenTaskQueueEmpty, task.command))
        # 只保留最近 10 分钟内的记录
        entries[:] = [entry for entry in entries if (now - entry[0]).total_seconds() < 600]

        if len(entries) < 3:
            return

        logger.critical('检测到调度在原地打转：10 分钟内连续 3 次进入等待分支，期间没有任何任务执行')
        for time, method, command in entries:
            logger.critical(f'等待记录 {time.strftime("%H:%M:%S")} 关闭方式={method} 待执行任务={command}')
        logger.critical('常见原因：等待分支里调用了 task_call()，把开始唤醒拉到现在执行')
        raise RequestHumanTakeover

    def maa_task_call_startup(self):
        """
        插入一次开始唤醒。

        task_call() 会强制把 Enable 置为 True，即使用户在 GUI 里禁用了开始唤醒也会被复活。
        用户显式禁用时不要偷偷改回去，直接停下让人处理。
        """
        if not self.config.cross_get(keys=['MaaStartup', 'Scheduler', 'Enable'], default=True):
            logger.critical('开始唤醒已被禁用（MAA 设置 - 开始唤醒），无法自动拉起游戏')
            logger.critical('请在 MAA 设置中启用「开始唤醒」，或手动启动游戏')
            raise RequestHumanTakeover

        self.config.task_call('MaaStartup')

    def maa_diagnose_game(self):
        """
        唤醒后游戏仍未运行时的补充诊断：把设备上疑似明日方舟的包列出来。

        失败只告警，绝不影响主流程。
        """
        config = self.config
        serial = config.MaaEmulator_Serial
        package = self.device.game_package()
        logger.critical(f'当前「游戏服务器」为 {config.MaaEmulator_PackageName}，判据包名 {package}')
        if not package:
            logger.critical('包名未知，已跳过游戏进程检查，请检查「游戏服务器」设置')
            return

        try:
            output = MaaEmulator().shell(serial, 'pm list packages', timeout=10)
        except Exception as e:
            logger.warning(f'无法查询设备 {serial} 的已安装包：{e}')
            return

        packages = []
        for line in output.splitlines():
            line = line.strip()
            if line.startswith('package:'):
                packages.append(line[len('package:'):].strip())
        related = [
            item for item in packages
            if any(key in item.lower() for key in ('arknights', 'hypergryph', 'yostar', 'txwy'))
        ][:5]
        if related:
            logger.critical(f'设备 {serial} 上疑似相关包：{"、".join(related)}')
        else:
            logger.critical(f'设备 {serial} 上未找到任何疑似明日方舟的包')

    def _maa_boot_check(self):
        """
        实例启动时的一次性设备就绪检查。

        只在进程生命周期内执行一次，且必须排在 start_watching() 之前：
        emulator_check() 最长会阻塞 180 秒（wait_online 超时），
        若排在监听基准之后，会重现「阻塞期间 GUI 操作被吞」的问题。

        注意：本检查只做「设备是否可达」，不做游戏判据。拉起后游戏没起来是正常的，
        交给任务边界的 game_alive() 判据补一次开始唤醒。

        close_emulator 的既定交互（不要优化掉）：启动 → 拉起模拟器 → 首个等待分支
        发现要等很久 → 立刻关闭。这与主模块 Device.__init__ 的行为一致，不额外加抑制。
        """
        if self.__dict__.get('_maa_boot_checked', False):
            return
        # 先置位：即使后面抛 RHT，也不会在同一个实例里反复拉起模拟器
        self.__dict__['_maa_boot_checked'] = True

        device = self.device
        device.config = self.config
        if not device.emulator_check():
            logger.critical(
                f'实例启动：模拟器无法就绪（serial={self.config.MaaEmulator_Serial}）'
            )
            logger.critical('请检查 MAA 设置中的模拟器 serial，或手动启动模拟器后重新运行')
            raise RequestHumanTakeover

        serial = self.config.MaaEmulator_Serial
        if device.emulator_started:
            logger.info(f'实例启动：模拟器已拉起 {serial}')
        else:
            logger.info(f'实例启动：模拟器已在线 {serial}')

    def get_next_task(self):
        """
        重写任务调度，支持「完成所有任务后」的收尾行为。

        ALAS 主模块的等待逻辑依赖 device.app_stop() 与 Restart 任务，
        MAA 插件既没有自己的 device 也没有 Restart 任务，所以：
        - 关闭游戏：等待期间由这里直接调用 MAA 的 CloseDown；
        - 关闭模拟器：CloseDown 之后再关闭模拟器，下个任务开始前重新拉起。

        任务到点后先确认模拟器在线（设备层），再确认游戏在运行（游戏层），
        游戏没起就先补一次开始唤醒。

        注意：等待分支里**不得**主动唤醒游戏。`task_call()` 的语义是「立刻执行」而不是
        「预排」，它会把 MaaStartup 的 NextRun 设为当前时间，而 MaaStartup 必然启动游戏，
        于是「等待 → 关游戏 → 立刻开游戏 → 队列又空 → 等待」会自激成环。
        唤醒只发生在任务到点后的边界判据里。
        """
        # 启动期设备就绪检查只做一次，且在任何阻塞/监听操作之前
        self._maa_boot_check()

        while 1:
            task = self.config.get_next()
            self.config.task = task
            self.config.bind(task)
            self.maa_check_package_changed()

            # 配置变更的基准必须在任何阻塞操作之前采集：
            # 关闭模拟器最长会阻塞 60 秒，阻塞期间 GUI 发起的 task_call 必须能被
            # 随后的 wait_until() 发现，否则会迟到整个任务周期
            self.config.start_watching()

            if task.next_run > datetime.now():
                self._maa_watch_wait_loop(task)
                logger.info(f'Wait until {task.next_run} for task `{task.command}`')
                self.is_first_task = False
                method = self.config.Optimization_WhenTaskQueueEmpty
                # 只取一次 now，避免两次采样跨秒导致阈值抖动
                wait_seconds = (task.next_run - datetime.now()).total_seconds()
                if method == 'close_emulator' and wait_seconds < EMULATOR_CLOSE_MIN_WAIT:
                    # 空档太短，按 stay_there 处理：不关游戏也不关模拟器
                    logger.info(
                        f'Wait only {int(wait_seconds)}s, skip closing emulator '
                        f'(threshold {EMULATOR_CLOSE_MIN_WAIT}s)'
                    )
                elif method == 'close_emulator':
                    logger.info(f'等待 {int(wait_seconds)}s，按「关闭模拟器」策略关闭')
                    self.maa_stop_during_wait(task.next_run)
                else:
                    self.maa_close_game_during_wait()
                self.device.release_during_wait()
                # 这里不许再写配置：等待分支内的写盘会改掉配置基准，
                # 让 wait_until() 把「自己写的」当成「用户改的」而立即返回 False
                if not self.wait_until(task.next_run):
                    del_cached_property(self, 'config')
                    continue

            # 任务到点，先确认模拟器在线；掉线时在这里拉起，避免带着死掉的模拟器进任务
            device = self.device
            device.config = self.config
            if not device.emulator_check():
                logger.critical('Request human takeover')
                raise RequestHumanTakeover

            # 设备在线但游戏没起（手动关闭、崩溃、模拟器刚重启），先补一次开始唤醒
            if task.command != 'MaaStartup':
                if not device.game_alive():
                    if self._maa_wake_attempted:
                        self.maa_diagnose_game()
                        logger.critical(
                            '开始唤醒后游戏仍未运行，请检查账号登录、游戏更新或 MAA 日志'
                        )
                        raise RequestHumanTakeover
                    logger.critical('游戏未运行，先执行开始唤醒')
                    self._maa_wake_attempted = True
                    self.maa_task_call_startup()
                    del_cached_property(self, 'config')
                    continue
                # 游戏在运行，说明上一次唤醒已经生效，下次掉线时重新允许插一次唤醒
                self._maa_wake_attempted = False

            # 有实质任务即将执行，说明调度没有在原地打转，清零等待计数器
            if task.command != 'MaaStartup':
                self.__dict__['_maa_wait_enters'] = []

            break

        AzurLaneConfig.is_hoarding_task = False
        return task.command

    def maa_startup(self):
        AssistantHandler(config=self.config, asst=self.asst).startup()
        # 魔改客户端进游戏后会有悬浮面板挡住识别区域，这里顺手关掉。
        # 失败只打日志，不影响后续任务（面板的问题让用户自己处理）
        self.maa_assist_island()

    def maa_assist_island(self):
        from submodule.AlasMaaBridge.module.handler.assist import MaaAssist
        MaaAssist(self.config).run()

    def maa_annihilation(self):
        AssistantHandler(config=self.config, asst=self.asst).fight()

    def maa_material(self):
        AssistantHandler(config=self.config, asst=self.asst).fight()

    def maa_fight(self):
        AssistantHandler(config=self.config, asst=self.asst).fight()

    def maa_recruit(self):
        AssistantHandler(config=self.config, asst=self.asst).recruit()

    def maa_infrast(self):
        AssistantHandler(config=self.config, asst=self.asst).infrast()

    def maa_mall(self):
        AssistantHandler(config=self.config, asst=self.asst).mall()

    def maa_award(self):
        AssistantHandler(config=self.config, asst=self.asst).award()

    def maa_roguelike(self):
        AssistantHandler(config=self.config, asst=self.asst).roguelike()

    def maa_reclamation_algorithm(self):
        AssistantHandler(config=self.config, asst=self.asst).reclamation_algorithm()

    def maa_data_update(self):
        AssistantHandler(config=self.config, asst=self.asst).data_update()

    def maa_oper_progress(self):
        AssistantHandler(config=self.config, asst=self.asst).oper_progress()


def loop(config_name):
    ArknightsAutoScript(config_name).loop()


def set_stop_event(e):
    ArknightsAutoScript.stop_event = e


def maa_copilot(config_name):
    script = ArknightsAutoScript(config_name)
    script.config.bind('MaaCopilot')
    handler = AssistantHandler(config=script.config, asst=script.asst)
    handler.connect()
    handler.copilot()


def maa_update(config_name):
    script = ArknightsAutoScript(config_name)
    script.config.bind('MaaUpdate')
    script.maa_update(force=True)
