"""Local files only. Reports and predictions are immutable once registered."""
import json
import math
import os
import re
import shutil
import threading
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
REPORTS = DATA / 'reports'
BJ = timezone(timedelta(hours=8))
LOCK = threading.RLock()

@contextmanager
def process_lock():
    DATA.mkdir(parents=True, exist_ok=True)
    with LOCK:
        with (DATA / '.write.lock').open('a+b') as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def now():
    return datetime.now(BJ)

def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as handle:
            handle.write(payload)
            temp = Path(handle.name)
        try:
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def save_portfolio(value):
    if not isinstance(value, dict) or not isinstance(value.get('snapshot'), dict):
        raise ValueError('持仓必须包含 snapshot 对象。')
    if datetime.strptime(value['as_of'], '%Y-%m-%d').date() > now().date():
        raise ValueError('持仓快照日期不能来自未来。')
    for key in ('day_profit','day_return_pct','month_profit','total_assets_estimate'):
        number = value['snapshot'].get(key)
        if number is not None and not finite(number):
            raise ValueError(f'{key} 必须是有限数值或 null。')
    if value['snapshot'].get('total_assets_estimate') is not None and value['snapshot']['total_assets_estimate'] < 0:
        raise ValueError('持仓合计不能为负数。')
    for key in ('fund_allocation', 'asset_allocation', 'sector_allocation'):
        rows = value.get(key)
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError(f'{key} 必须是列表，最多200项。')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('name'), str):
                raise ValueError('每项持仓须有名称。')
            weight = row.get('weight_pct')
            if weight is not None and (not finite(weight) or not 0 <= weight <= 100):
                raise ValueError('占比必须在0到100之间。')
            amount = row.get('amount')
            if amount is not None and (not finite(amount) or amount < 0):
                raise ValueError('金额必须是非负有限数值。')
            code = row.get('code')
            if code and not re.fullmatch(r'\d{6}', str(code)):
                raise ValueError('基金代码应为6位数字；未核实请填 null。')
        if sum(r.get('weight_pct') or 0 for r in rows) > 100.1:
            raise ValueError(f'{key} 合计占比超过100%，请检查。')
    with process_lock():
        path = DATA / 'portfolio.json'
        if path.exists():
            backup = DATA / 'backups' / (now().strftime('%Y%m%d-%H%M%S-%f') + '-portfolio.json')
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        write(path, value)

def register_report(report):
    if not isinstance(report, dict):
        raise ValueError('报告必须是JSON对象。')
    for key in ('id', 'title', 'as_of', 'summary'):
        if not isinstance(report.get(key), str) or not report[key].strip():
            raise ValueError(f'报告缺少 {key}。')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', report['id']):
        raise ValueError('报告ID仅允许英文、数字、短横线和下划线。')
    stamp = datetime.fromisoformat(report['as_of'])
    if stamp.tzinfo is None or stamp > now() + timedelta(minutes=5):
        raise ValueError('报告时间必须带时区，且不得来自未来。')
    for key in ('market', 'outlooks', 'sources', 'limitations'):
        if not isinstance(report.get(key, []), list):
            raise ValueError(f'{key} 必须是列表。')
    for src in report.get('sources', []):
        if not isinstance(src, dict) or not str(src.get('url', '')).startswith('https://'):
            raise ValueError('来源必须提供 HTTPS 链接。')
    path = REPORTS / (report['id'] + '.json')
    with process_lock():
        if path.exists():
            raise ValueError('报告已存在。更正请使用新ID，不覆盖旧结论。')
        write(path, report)
    return path

def forecasts():
    return read(DATA / 'forecasts.json', [])

def statistics(items, current=None):
    current = current or now()
    done = [x for x in items if x.get('evaluation') is not None]
    hits = sum(x['evaluation']['hit'] is True for x in done)
    missing = [x for x in items if x.get('evaluation') is None and x.get('due_date') and x['due_date'] <= current.date().isoformat()]
    groups = {}
    for days in (7, 30, 60):
        rows = [x for x in done if x['horizon_days'] == days]
        groups[str(days)] = {'evaluated': len(rows), 'hits': sum(x['evaluation']['hit'] is True for x in rows)}
    return {'evaluated': len(done), 'hits': hits, 'accuracy_pct': round(100*hits/len(done), 1) if done else None,
            'pending': len(items)-len(done), 'by_horizon': groups,
            'due_unavailable':len(missing), 'awaiting_start':sum(not x.get('start_date') for x in items),
            'coverage_pct':round(100*len(done)/(len(done)+len(missing)),1) if done or missing else None,
            'note': '命中指代理ETF是否相对沪深300跑赢/跑输；不是赚钱概率。准确率仅针对可评估样本，到期缺数据另列；重叠样本不独立，未验证90%。'}

def state():
    rows = []
    for path in REPORTS.glob('*.json'):
        rows.append(read(path))
    rows.sort(key=lambda x: (x['as_of'], x['id']), reverse=True)
    items = forecasts()
    return {'portfolio': read(DATA / 'portfolio.json', {}), 'reports': rows[:100], 'forecasts': items,
            'stats': statistics(items), 'settings': read(DATA / 'settings.json', {}),
            'market_snapshot': read(DATA / 'market.json', {}), 'server_time': now().isoformat()}
