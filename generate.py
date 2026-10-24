"""Create an allowlisted public snapshot from public market data only."""
import argparse
import json
from pathlib import Path
from market import refresh
from storage import DATA, ROOT, read, write, now, statistics

PUBLIC = ROOT / 'public' / 'data' / 'public-state.json'

def export(refresh_data=True):
    if refresh_data:
        report = refresh()
    else:
        paths = sorted((DATA/'reports').glob('baseline-*.json'))
        if not paths:
            raise ValueError('没有公开市场报告，先执行一次正常刷新。')
        report = read(paths[-1])
    if not report['market']:
        if PUBLIC.exists():
            old = read(PUBLIC)
            old['last_attempt_at'] = now().isoformat()
            old['update_error'] = '此次市场数据查询失败，保留上次报告。请检查报告原始时间。'
            write(PUBLIC, old)
            return old
        raise ValueError('无可用行情，不发布虚构报告。')
    reports = []
    keys = ('id','title','as_of','type','summary','market','outlooks','sources','limitations')
    # Cloud runners start fresh; retain earlier public reports from the committed snapshot.
    for r in read(PUBLIC, {}).get('reports', []):
        if isinstance(r, dict) and r.get('id'):
            reports.append({k:r[k] for k in keys if k in r})
    # Never read portfolio.json or personalized research reports.
    for path in sorted((DATA/'reports').glob('baseline-*.json'), reverse=True)[:30]:
        r = read(path)
        reports.append({k:r[k] for k in keys if k in r})
    reports = sorted({r['id']:r for r in reports}.values(), key=lambda r:r.get('as_of',''), reverse=True)[:30]
    items = read(DATA/'forecasts.json', [])
    published = now().isoformat()
    result = {'schema_version':1, 'published_at':published, 'server_time':published,
        'reports':reports,'forecasts':items,'stats':statistics(items),
        'settings':{'timezone':'Asia/Shanghai','daily_times':['12:00','16:00'],
                    'delivery':'计划更新网页；不等于手机通知，实际以报告时间为准',
                    'weekly':'公开价格趋势观察 · 7 / 30 / 60天；完整文字投研仍在原聊天'},
        'update_error':None}
    write(PUBLIC,result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--no-refresh',action='store_true')
    args=parser.parse_args()
    result=export(not args.no_refresh)
    print(json.dumps({'published_at':result['published_at'],'reports':len(result['reports']),'personal_data_uploaded':False}))
