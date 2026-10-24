'use strict';
// This adapter never sends a portfolio or personal report to a server.
const MarketAPI = (() => {
  const KEY = 'market-assistant:portfolio:v1';
  const BACKUP = KEY + ':previous';
  const blank = () => ({as_of: new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()),snapshot:{day_profit:null,day_return_pct:null,month_profit:null,total_assets_estimate:null},fund_allocation:[],sector_allocation:[],asset_allocation:[],notes:['持仓仅保存在本设备浏览器；换设备需要重新导入。公开网站不含个人持仓。']});
  function validate(p) {
    if(!p || typeof p!=='object' || Array.isArray(p) || !p.snapshot || typeof p.snapshot!=='object' || Array.isArray(p.snapshot)) throw Error('持仓须包含 snapshot 对象');
    if(typeof p.as_of!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(p.as_of)||!Number.isFinite(Date.parse(p.as_of))||new Date(p.as_of).toISOString().slice(0,10)!==p.as_of) throw Error('请输入有效快照日期 YYYY-MM-DD');
    const today = new Date(Date.now()+8*3600*1000).toISOString().slice(0,10);
    if(p.as_of>today) throw Error('快照日期不能来自未来');
    for(const key of ['day_profit','day_return_pct','month_profit','total_assets_estimate']) {
      const n=p.snapshot[key];
      if(n!=null&&(typeof n!=='number'||!Number.isFinite(n))) throw Error(key+' 应为数值或 null');
    }
    if(p.snapshot.total_assets_estimate!=null&&p.snapshot.total_assets_estimate<0)throw Error('持仓合计不能为负');
    for(const key of ['fund_allocation','sector_allocation','asset_allocation']) {
      if(!Array.isArray(p[key])||p[key].length>200)throw Error(key+' 必须是数组，最多200项');
      let total=0;
      for(const row of p[key]) {
        if(!row||typeof row.name!=='string')throw Error('每项必须有名称');
        if(row.weight_pct!=null&&(typeof row.weight_pct!=='number'||!Number.isFinite(row.weight_pct)||row.weight_pct<0||row.weight_pct>100))throw Error('占比须为0至100');
        if(row.amount!=null&&(typeof row.amount!=='number'||!Number.isFinite(row.amount)||row.amount<0))throw Error('金额须为非负数');
        if(row.code!=null&&row.code!==''&&!/^\d{6}$/.test(String(row.code)))throw Error('基金代码须为6位数字');
        total+=row.weight_pct||0;
      }
      if(total>100.1)throw Error('占比合计超过100%');
    }
    return p;
  }
  function localPortfolio(){const raw=localStorage.getItem(KEY);return raw?validate(JSON.parse(raw)):blank();}
  function save(p){validate(p);const value=JSON.stringify(p);if(value.length>1000000)throw Error('持仓文件过大');const old=localStorage.getItem(KEY);if(old)localStorage.setItem(BACKUP,old);localStorage.setItem(KEY,value);}
  function response(payload,status=200){return new Response(JSON.stringify(payload),{status,headers:{'Content-Type':'application/json'}});}
  async function request(path,options={}) {
    try {
      if(path==='/api/portfolio') {save(JSON.parse(options.body));return response({ok:true});}
      if(path==='/api/state'||path==='/api/refresh') {
        const url=new URL('./data/public-state.json',location.href);
        url.searchParams.set('v',String(Date.now()));
        const result=await fetch(url.href,{cache:'no-store',credentials:'omit',headers:{Accept:'application/json'}});
        if(!result.ok)throw Error('在线报告暂不可用（'+result.status+'），请稍后重试');
        const payload=await result.json();
        if(!Array.isArray(payload.reports))throw Error('在线报告格式有误');
        if(path==='/api/refresh')return response({ok:true,report:payload.reports[0]||{},published_at:payload.published_at});
        let portfolio; let warning='';
        try{portfolio=localPortfolio();}catch(error){portfolio=blank();warning='本机持仓未能读取：'+error.message+'。原记录未被覆盖，可重新导入。';}
        payload.portfolio=portfolio;
        payload.local_warning=warning;
        payload.server_time=new Date(Date.now()+8*3600*1000).toISOString().replace('Z','+08:00');
        return response(payload);
      }
      throw Error('不支持的操作');
    } catch(error) {return response({ok:false,error:error.message},400);}
  }
  function downloadPortfolio(){
    const blob=new Blob([JSON.stringify(localPortfolio(),null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='我的持仓.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return {request,validate,save,downloadPortfolio};
})();
