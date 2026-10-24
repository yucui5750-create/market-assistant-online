"""Read public quotes; a transparent momentum baseline, not a calibrated predictor."""
import concurrent.futures
import json
import re
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from storage import BJ, DATA, now, write, read, finite, register_report, forecasts, process_lock

WATCH = {
    'sh512480': '半导体', 'sh515880': '通信（含光通信，非纯CPO）',
    'sh512010': '医药', 'sh516160': '新能源', 'sz159928': '消费',
    'sh512800': '银行', 'sh510880': '红利（策略，不是行业）',
    'sz159941': '纳指QDII（场内价格，非纳指本身）',
}
BENCH = 'sh510300'
INDICES = ['sh000001', 'sz399001', 'sz399006', 'sh000300']
LABELS = {7: ('未来7天', 5), 30: ('未来1个月（30天）', 20), 60: ('未来2个月（60天）', 40)}

def get(url, encoding='utf-8'):
    request = Request(url, headers={'User-Agent': 'MarketAssistant/1.0 (personal research)'})
    with urlopen(request, timeout=15) as response:
        return response.read(5_000_000).decode(encoding)

def parse_quotes(text, current=None):
    current = current or now()
    result = []
    for code, payload in re.findall(r'v_([a-z]+\d+)="([^"]*)"', text):
        fields = payload.split('~')
        try:
            if len(fields) < 33:
                continue
            stamp = datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=BJ)
            value, prev, pct = float(fields[3]), float(fields[4]), float(fields[32])
            if not all(finite(x) for x in (value, prev, pct)) or value <= 0 or prev <= 0:
                continue
            if stamp > current + timedelta(minutes=5) or abs((value / prev - 1)*100 - pct) > .08:
                continue
            minutes = current.hour * 60 + current.minute
            expected = 14*60+55 if minutes >= 15*60 else (11*60+25 if 11*60+30 <= minutes < 13*60 else minutes-20)
            stale = stamp.date() < current.date() or (minutes >= 9*60+30 and stamp.hour*60+stamp.minute < expected)
            result.append({'code': code, 'name': fields[1], 'value': value, 'change_pct': pct,
                           'as_of': stamp.isoformat(), 'source_url': 'https://qt.gtimg.cn/q=' + code,
                           'source': '腾讯财经公开行情', 'historical': stamp.date() != current.date(),
                           'stale':stale, 'status':'历史/延迟数据，请核对原始时间' if stale else '通过时点检查（非交易所直连）'})
        except (ValueError, IndexError):
            continue
    return result

def history(code, current=None):
    current = current or now()
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,180,qfq'
    raw = json.loads(get(url))
    record = raw.get('data', {}).get(code, {})
    rows = record.get('qfqday') or []
    if not rows:
        raise ValueError('缺少明确的前复权历史，暂停比较；不混用未复权价格')
    clean = []
    for row in rows:
        day = datetime.strptime(row[0], '%Y-%m-%d').date()
        close = float(row[2])
        # Intraday daily candles are incomplete. Exclude today's bar until 15:10 Beijing.
        if day > current.date() or (day == current.date() and (current.hour, current.minute) < (15, 10)):
            continue
        if not finite(close) or close <= 0:
            raise ValueError('无效历史收盘价')
        clean.append({'date': str(day), 'close': close})
    if len({x['date'] for x in clean}) != len(clean) or clean != sorted(clean, key=lambda x:x['date']):
        raise ValueError('历史行情日期重复或乱序')
    if len(clean) < 41:
        raise ValueError('不足41个完整交易日，不能比较全部周期')
    return {'code': code, 'rows': clean, 'source_url': url, 'basis': '前复权收盘价变动；不是含费用的实际收益'}

def momentum(series, end, count, reference=None):
    rows = [x for x in series['rows'] if x['date'] <= end]
    if len(rows) <= count or rows[-1]['date'] != end:
        raise ValueError('样本缺失或日期不一致')
    if reference is not None:
        expected = [x['date'] for x in reference['rows'] if x['date'] <= end][-count-1:]
        if [x['date'] for x in rows[-count-1:]] != expected:
            raise ValueError('标的与基准观察窗口交易日期不一致')
    return (rows[-1]['close']/rows[-count-1]['close']-1)*100

def evaluate(items, histories, current=None):
    current = current or now()
    changed = 0
    for item in items:
        if item.get('evaluation') is not None:
            continue
        if item['code'] not in histories or BENCH not in histories:
            continue
        own = {r['date']:r['close'] for r in histories[item['code']]['rows']}
        bench = {r['date']:r['close'] for r in histories[BENCH]['rows']}
        # Evaluate only returns after registration. The signal observation close is NOT an entry.
        if not item.get('start_date'):
            starts = sorted(d for d in bench if item['eligible_from'] <= d <= str(current.date()))
            if not starts or starts[0] not in own:
                continue
            if (datetime.fromisoformat(starts[0]) - datetime.fromisoformat(item['eligible_from'])).days > 14:
                continue  # Do not silently move an old missing entry into the available history window.
            item['start_date'] = starts[0]
            item['due_date'] = (datetime.fromisoformat(starts[0]).date()+timedelta(days=item['horizon_days'])).isoformat()
            changed += 1
        if current.date().isoformat() < item['due_date']:
            continue
        dates = sorted(d for d in bench if item['due_date'] <= d <= str(current.date()))
        if not dates or dates[0] not in own or item['start_date'] not in own or item['start_date'] not in bench:
            continue
        day = dates[0]
        if (datetime.fromisoformat(day) - datetime.fromisoformat(item['due_date'])).days > 14:
            continue
        a = (own[day]/own[item['start_date']]-1)*100
        b = (bench[day]/bench[item['start_date']]-1)*100
        excess = a-b
        # A tie is never credited as a successful directional forecast.
        hit = excess > 0 if item['direction'] == 'outperform' else excess < 0
        item['evaluation'] = {'date':day, 'return_pct':round(a,4), 'benchmark_return_pct':round(b,4),
                              'excess_pct':round(excess,4), 'hit':hit, 'evaluated_at':current.isoformat(),
                              'source_url':histories[item['code']]['source_url'],
                              'method':'登记后首个完整基准交易日收盘作为起点；到期日（非交易日按基准顺延）收盘。标的缺当天则不评分。分子分母用同次前复权序列；未扣费用。'}
        changed += 1
    return changed

def refresh():
    current = now()
    errors, quotes, histories = [], [], {}
    quote_url = 'https://qt.gtimg.cn/q=' + ','.join(INDICES + list(WATCH) + [BENCH])
    try:
        quotes = parse_quotes(get(quote_url, 'gbk'), current)
        if len(quotes) < len(INDICES) + len(WATCH) + 1:
            errors.append('部分行情缺失或未通过时间/涨跌幅校验；未填补。')
    except Exception as exc:
        errors.append('行情获取失败：' + str(exc))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(history, code, current):code for code in list(WATCH) + [BENCH]}
        for job in concurrent.futures.as_completed(jobs):
            code = jobs[job]
            try:
                histories[code] = job.result()
            except Exception as exc:
                errors.append(code + ' 历史行情不可用：' + str(exc))
    write(DATA / 'market.json', {'retrieved_at':current.isoformat(), 'quotes':quotes, 'histories':histories, 'errors':errors})
    report_id = current.strftime('baseline-%Y%m%d-%H%M%S-%f')
    outlooks, additions = [], []
    benchmark = histories.get(BENCH)
    end = benchmark['rows'][-1]['date'] if benchmark else None
    recent = end and (current.date() - datetime.fromisoformat(end).date()).days <= 4
    # Public-holiday stale snapshots remain readable but never produce new forecasts.
    if benchmark and recent:
        for days, (label, count) in LABELS.items():
            for code, sector in WATCH.items():
                if code not in histories:
                    continue
                if code == 'sz159941':
                    continue  # QDII share price + FX + premium is not a domestic industry comparison.
                try:
                    absolute = momentum(histories[code], end, count, benchmark)
                    excess = absolute - momentum(benchmark, end, count)
                except ValueError as exc:
                    errors.append(f'{code} {label} 暂停判断：{exc}')
                    continue
                direction = 'outperform' if excess > 1 else ('underperform' if excess < -1 else None)
                stance = '相对偏强，观察' if direction == 'outperform' else ('相对偏弱，谨慎' if direction else '暂不判断')
                outlooks.append({'horizon':label, 'sector':sector, 'stance':stance,
                    'rationale':f'截至{end}，代理ETF过去{count}个完整交易日涨跌{absolute:+.2f}%，相对沪深300ETF为{excess:+.2f}个百分点。仅作趋势延续研究假设。',
                    'invalidation':'相对强弱逆转、公司业绩或政策出现反向证据时，本判断失效；今日涨幅不等于未来回报。',
                    'confidence':'低：仅价格趋势，尚未加入基本面与估值核验；未验证预测能力',
                    'code':code, 'source_url':histories[code]['source_url']})
                if direction:
                    # At most one frozen baseline per instrument/horizon/trading date.
                    eligible = current.date() + timedelta(days=1 if current.hour >= 15 else 0)
                    additions.append({'id':f'{end}-{code}-{days}', 'report_id':report_id,
                        'registered_at':current.isoformat(), 'base_date':end, 'horizon_days':days,
                        'eligible_from':eligible.isoformat(), 'start_date':None, 'due_date':None,
                        'code':code, 'sector':sector, 'horizon':label, 'direction':direction,
                        'stance':stance, 'benchmark':BENCH, 'method':'momentum-v2', 'evaluation':None})
    else:
        errors.append('完整历史行情过旧或基准缺失，暂停生成新方向判断。')
    with process_lock():
        items = forecasts()
        evaluate(items, histories, current)
        ids = {x['id'] for x in items}
        items.extend(x for x in additions if x['id'] not in ids)
        write(DATA / 'forecasts.json', items)
    sources = [{'title':'腾讯财经公开行情（第三方，需复核）', 'url':quote_url, 'as_of':current.isoformat()}]
    sources += [{'title':WATCH.get(c,c)+' 历史价格', 'url':h['source_url'], 'as_of':h['rows'][-1]['date']} for c,h in histories.items()]
    limitations = ['本报告由公开行情和透明趋势规则生成，不是完整投研或买卖指令，不承诺90%准确率。',
        '未来7/30/60天是自然日预测窗口；过去5/20/40交易日仅为观察窗口，二者不可混淆。',
        '相对偏强可能仍然下跌；相对偏弱也可能上涨。样本仅含列出的代理ETF，不是全市场行业排名。',
        '通信ETF不是纯CPO；红利是投资策略；QDII场内价格含汇率/溢价影响，未纳入A股行业强弱评分。',
        '本报告仅讨论公开代理ETF，不读取任何用户的实际持仓或盈亏。',
        '行情保留来源原始时间；非当前日数据是最近交易日记录，不能称为今日实时。'] + errors
    report = {'id':report_id, 'title':'行情与行业观察 · 自动数据版', 'as_of':current.isoformat(),
        'type':'数据更新', 'summary':'已更新公开指数与ETF行情。下方是可复核的趋势基线；完整投研还需核验新闻、业绩与估值；本页不假装已完成这些核验。',
        'market':quotes, 'outlooks':outlooks, 'sources':sources, 'limitations':limitations}
    register_report(report)
    return report
