"""产能下降和插单的只读情景模拟；统一沿用交期优先、单工序齐套规则。"""
from datetime import date,timedelta
import pandas as pd
from src.core import connect,replace_dataset,FIELDS
from src.cases import snapshot

def schedule(c,hours,capacity,overrides=None):
    if type(capacity)!=int or not 1<=capacity<=24:raise ValueError('每日产能须为1至24整数小时')
    overrides=overrides or {}
    overrides={date.fromisoformat(str(k)):v for k,v in overrides.items()}
    if any(type(v)!=int or not 0<=v<=capacity for v in overrides.values()):raise ValueError('临时产能须为0至正常产能的整数')
    orders=c.execute('SELECT order_id,material_need_date,due_date,data_source FROM orders ORDER BY due_date,order_id').fetchall()
    if set(hours)!={o[0] for o in orders} or any(type(v)!=int or not 1<=v<=720 for v in hours.values()):raise ValueError('必须提供每单1至720整数小时的总加工工时')
    if len(orders)>100:raise ValueError('当前原型最多支持100个订单')
    used={};rows=[];slots=[]
    for oid,need,due,source in orders:
        ready=date.fromisoformat(need);missing=[]
        for mat,qty in c.execute('SELECT material_id,required_quantity FROM demands WHERE order_id=?',(oid,)):
            batches=c.execute('SELECT s.available_date,a.allocated_quantity FROM allocations a JOIN supplies s USING(supply_id) WHERE a.order_id=? AND s.material_id=? ORDER BY s.available_date,s.supply_id',(oid,mat)).fetchall()
            if sum(q for d,q in batches)<qty:missing.append(mat);continue
            total=0
            for d,q in batches:
                total+=q
                if total>=qty:ready=max(ready,date.fromisoformat(d));break
        finish=None
        if not missing:
            remaining=hours[oid];day=ready
            for _ in range(73000):
                cap=overrides.get(day,capacity);take=min(remaining,cap-used.get(day,0))
                if take:
                    used[day]=used.get(day,0)+take;remaining-=take;finish=day
                    slots.append({'订单':oid,'日期':day.isoformat(),'加工小时':take,'当天共享产能':cap})
                if not remaining:break
                day+=timedelta(days=1)
            if remaining:raise ValueError('排程范围过大，请缩小订单规模')
        rows.append({'订单':oid,'来源':'模拟数据' if source=='simulation' else '真实数据','客户交期':due,'完成日期':finish.isoformat() if finish else None,'逾期天数':max(0,(finish-date.fromisoformat(due)).days) if finish else None,'缺料':','.join(missing)})
    return pd.DataFrame(rows),pd.DataFrame(slots,columns=['订单','日期','加工小时','当天共享产能'])

def comparison(before,after):
    old=before.set_index('订单');rows=[]
    for r in after.to_dict('records'):
        oid=r['订单'];finish=r['完成日期'];new=r['逾期天数']
        baseline=old.loc[oid] if oid in old.index else None
        prior=baseline['完成日期'] if baseline is not None else None
        late=baseline['逾期天数'] if baseline is not None else None
        delta=int(new-late) if pd.notna(new) and pd.notna(late) else None
        movement=(date.fromisoformat(finish)-date.fromisoformat(prior)).days if finish and prior else None
        rows.append({'订单':oid,'来源':r['来源'],'客户交期':r['客户交期'],'原预计完成':prior or ('新增订单' if baseline is None else '缺料，无法估算'),'调整后完成':finish or '缺料，无法估算','调整后判断':'缺料，无法估算' if pd.isna(new) else ('预计按期' if new==0 else f'晚 {int(new)} 天'),'完成日后移天数':movement,'新增逾期天数':delta,'缺料物料':r['缺料']})
    return pd.DataFrame(rows)

def capacity_drop(c,hours,capacity,start,end,reduced):
    start=date.fromisoformat(str(start));end=date.fromisoformat(str(end))
    span=(end-start).days+1
    if not 1<=span<=31:raise ValueError('产能下降日期范围须为1至31天')
    if type(reduced)!=int or not 0<=reduced<capacity:raise ValueError('下降后产能须为0至正常产能减1的整数')
    before,_=schedule(c,hours,capacity)
    after,slots=schedule(c,hours,capacity,{start+timedelta(days=i):reduced for i in range(span)})
    detail=comparison(before,after)
    return {'类型':'临时产能下降','参数':{'开始日期':str(start),'结束日期':str(end),'正常产能':capacity,'下降后产能':reduced,'订单工时':hours},'受影响订单数':int((detail['完成日后移天数']>0).sum()),'新增逾期订单数':int((detail['新增逾期天数']>0).sum()),'订单影响':detail,'每日排程':slots,'结论':'按临时产能重新核算，包含产能恢复后的连带影响；未执行生产调整。'}

def urgent_order(c,hours,capacity,order):
    required={'order_id','product_name','order_quantity','material_id','required_quantity','material_need_date','due_date','hours'}
    if set(order)!=required:raise ValueError('插单字段不完整')
    for field in ['order_id','product_name','material_id']:
        if not isinstance(order[field],str) or not order[field].strip():raise ValueError('编号、产品和物料不能为空')
    for field in ['order_quantity','required_quantity','hours']:
        if type(order[field])!=int or not 1<=order[field]<=(720 if field=='hours' else 1_000_000_000):raise ValueError('插单数量和工时须为范围内正整数')
    if c.execute('SELECT 1 FROM orders WHERE order_id=?',(order['order_id'],)).fetchone():raise ValueError('插单编号已存在')
    need=date.fromisoformat(str(order['material_need_date']));due=date.fromisoformat(str(order['due_date']))
    if need>due:raise ValueError('物料需求日期不能晚于客户交期')
    before,_=schedule(c,hours,capacity)
    saved=snapshot(c);source=saved['orders'][0]['data_source']
    if any(o['data_source']!=source for o in saved['orders']):raise ValueError('数据来源混用，请核查')
    new={k:order[k] for k in FIELDS['orders'] if k!='data_source'};new['data_source']=source
    saved['orders'].append(new)
    saved['demands'].append({'order_id':order['order_id'],'material_id':order['material_id'],'required_quantity':order['required_quantity']})
    remaining=order['required_quantity'];allocated=[]
    supplies=c.execute('SELECT s.supply_id,s.available_date,s.quantity-COALESCE(SUM(a.allocated_quantity),0) AS free FROM supplies s LEFT JOIN allocations a USING(supply_id) WHERE s.material_id=? GROUP BY s.supply_id ORDER BY s.available_date,s.supply_id',(order['material_id'],)).fetchall()
    for sid,day,free in supplies:
        take=min(max(0,free),remaining)
        if take:
            saved['allocations'].append({'order_id':order['order_id'],'supply_id':sid,'allocated_quantity':take})
            allocated.append({'供给':sid,'可用日期':day,'分配数量':take});remaining-=take
        if not remaining:break
    temp=connect(':memory:')
    try:
        replace_dataset(temp,{n:pd.DataFrame(v,columns=FIELDS[n]) for n,v in saved.items()},source)
        new_hours=dict(hours);new_hours[order['order_id']]=order['hours']
        after,slots=schedule(temp,new_hours,capacity)
    finally:temp.close()
    detail=comparison(before,after);newrow=after.set_index('订单').loc[order['order_id']]
    harmed=detail[(detail['订单']!=order['order_id'])&(detail['新增逾期天数']>0)]
    unknown=before['完成日期'].isna().sum()
    if remaining:verdict='物料不足，暂不能判断可接单'
    elif newrow['逾期天数']>0:verdict='新单无法按所填交期完成，需协商交期'
    elif len(harmed):verdict='新单可按期，但增加原订单逾期，需人工取舍'
    elif unknown:verdict='新单预计按期，但部分原订单缺料，不能保证全部承诺'
    else:verdict='当前规则下新单可按期，且不增加原订单逾期'
    return {'类型':'紧急插单','参数':{'新订单':order,'每日产能':capacity,'原订单工时':hours},'结论':verdict,'缺料数量':remaining,'新增逾期原订单数':len(harmed),'订单影响':detail,'每日排程':slots,'新单物料分配':allocated,'限制':'仅用未分配供给，按可用日期优先；不抢占已分配物料。交期优先可影响原计划；未执行接单或分配。'}
