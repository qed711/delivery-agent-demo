"""本地处理单：保存快照、记录人工转述的供应商反馈、复核，不执行采购。"""
import json,hashlib
from datetime import datetime,timezone
import pandas as pd
from src.core import FIELDS,connect,replace_dataset
from src.recovery import recommend_recovery
from src.planning import compare_plans

def encode(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False)
def digest(v):return hashlib.sha256(encode(v).encode()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def prepare(c):
    c.executescript("""CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,feedback TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS case_events(id INTEGER PRIMARY KEY,case_id INTEGER REFERENCES cases(id),time_utc TEXT NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL);""")

def snapshot(c):
    return {name:json.loads(pd.read_sql_query('SELECT * FROM '+name+' ORDER BY '+','.join(FIELDS[name]),c).to_json(orient='records',force_ascii=False)) for name in FIELDS}

def get_case(c,case_id):
    r=c.execute('SELECT id,status,payload,feedback,created_at FROM cases WHERE id=?',(case_id,)).fetchone()
    if not r:raise ValueError('处理单不存在')
    return {'编号':r[0],'状态':r[1],'方案':json.loads(r[2]),'反馈':json.loads(r[3]) if r[3] else None,'创建时间UTC':r[4]}

def create_case(c,target,supply_id,delayed_date,hours,capacity):
    prepare(c)
    recommendation=recommend_recovery(c,target,supply_id,delayed_date,hours,capacity)
    if recommendation['状态']!='找到可行方案':raise ValueError('仅可为找到的可行方案建立处理单')
    payload={'数据快照':snapshot(c),'目标订单':target,'采购供给':supply_id,'延期日期':str(delayed_date),'工时':hours,'每日产能':capacity,'推荐':recommendation}
    fingerprint=digest(payload)
    with c:
        existing=c.execute('SELECT id FROM cases WHERE fingerprint=?',(fingerprint,)).fetchone()
        if existing:return existing[0],False
        case_id=c.execute('INSERT INTO cases(fingerprint,status,payload,created_at) VALUES (?,?,?,?)',(fingerprint,'待供应商反馈',encode(payload),now())).lastrowid
        c.execute('INSERT INTO case_events(case_id,time_utc,action,detail) VALUES (?,?,?,?)',(case_id,now(),'建立处理单','保存分析快照；尚未发送或执行'))
    return case_id,True

def is_stale(c,case):return digest(snapshot(c))!=digest(case['方案']['数据快照'])

def record_feedback(c,case_id,agreed,first_date=None,quantity=0,note=''):
    case=get_case(c,case_id)
    if case['状态']=='已人工确认（未执行）':raise ValueError('已确认处理单不能覆盖反馈')
    if len(note)>500:raise ValueError('备注最多500字')
    payload=case['方案'];feedback={'供应商同意分批':bool(agreed),'备注':note,'记录时间UTC':now(),'录入方式':'人工转述，未对外核实','可确认':False}
    if agreed:
        temp=connect(':memory:')
        try:
            tables={n:pd.DataFrame(rows,columns=FIELDS[n]) for n,rows in payload['数据快照'].items()}
            source=payload['数据快照']['orders'][0]['data_source']
            replace_dataset(temp,tables,source)
            result,_=compare_plans(temp,payload['采购供给'],payload['延期日期'],first_date,quantity,payload['工时'],payload['每日产能'])
            plan=result[result['方案'].str.startswith('分批')].set_index('订单编号')
            baseline=result[result['方案']=='全部延期'].set_index('订单编号')
            target=payload['目标订单']
            feasible=pd.notna(plan.loc[target,'预计逾期天数']) and plan.loc[target,'预计逾期天数']==0
            worsened=[oid for oid in plan.index if pd.notna(baseline.loc[oid,'预计逾期天数']) and (pd.isna(plan.loc[oid,'预计逾期天数']) or plan.loc[oid,'预计逾期天数']>baseline.loc[oid,'预计逾期天数'])]
            feasible=bool(feasible and not worsened)
            feedback.update({'首批日期':str(first_date),'首批数量':quantity,'可确认':feasible,'复核结果':json.loads(plan.reset_index().to_json(orient='records',force_ascii=False)),'逾期增加订单':worsened})
            status='待人工确认' if feasible else '需重新协商'
        finally:temp.close()
    else:status='供应商未同意'
    with c:
        c.execute('UPDATE cases SET status=?,feedback=? WHERE id=?',(status,encode(feedback),case_id))
        c.execute('INSERT INTO case_events(case_id,time_utc,action,detail) VALUES (?,?,?,?)',(case_id,now(),'记录供应商反馈',encode(feedback)))
    return status

def approve(c,case_id):
    case=get_case(c,case_id)
    if case['状态']!='待人工确认' or not case['反馈'] or not case['反馈']['可确认']:raise ValueError('反馈方案未通过复核，不能确认')
    if is_stale(c,case):raise ValueError('业务数据已变化，请按最新数据重新建立处理单')
    with c:
        c.execute("UPDATE cases SET status='已人工确认（未执行）' WHERE id=?",(case_id,))
        c.execute('INSERT INTO case_events(case_id,time_utc,action,detail) VALUES (?,?,?,?)',(case_id,now(),'人工确认','确认的是本地方案记录；未发送供应商、未变更生产计划'))
