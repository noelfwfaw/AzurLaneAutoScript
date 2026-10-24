"""
外观装备统计记录的读写。

文件与仓库扫描记录同目录：log/storage_scan/<config_name>/gear_skin.json
"""
import json
import os
import shutil

from deploy.atomic import atomic_write
from module.logger import logger

OUTPUT_FOLDER = 'log/storage_scan'
MAX_RECORDS = 90

FILE_NAME = 'gear_skin.json'


def record_path(config_name):
    return os.path.join(OUTPUT_FOLDER, config_name, FILE_NAME)


def load_records(config_name):
    """
    读历史记录。文件损坏时备份为 .bak 并重建。

    Returns:
        list: 记录列表
    """
    path = record_path(config_name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        records = data.get('records', []) if isinstance(data, dict) else []
        return records if isinstance(records, list) else []
    except (ValueError, OSError, AttributeError) as e:
        backup = path + '.bak'
        try:
            shutil.copyfile(path, backup)
        except OSError:
            pass
        logger.warning(f'Gear skin record is broken, backup to {backup}: {e}')
        return []


def append_record(config_name, record):
    """
    Returns:
        str: 文件路径
    """
    folder = os.path.join(OUTPUT_FOLDER, config_name)
    os.makedirs(folder, exist_ok=True)
    path = record_path(config_name)
    records = load_records(config_name)
    records.append(record)
    records = records[-MAX_RECORDS:]
    atomic_write(path, json.dumps({'records': records}, ensure_ascii=False, indent=2))
    logger.info(f'Gear skin statistic saved: {path}')
    return path
