"""单工序、共享产能、全部齐套后开工的可解释情景比较。"""
from datetime import date,timedelta
import pandas as pd

def compare_plans(c,supply_id,delayed_date,first_date,first_quantity,hours,daily_capacity=8):
    delayed=date.fromisoformat(str(delayed_date));first=date.fromisoformat(str(first_date))
    supply=c.execute('SELECT quantity,available_date,supply_type FROM supplies WHERE supply_id=?',(supply_id,)).fetchone()
    if not supply or supply[2]!='purchase':raise ValueError('请选择采购供给')
    original=date.fromisoformat(supply[1])
    if delayed<=original or not original<=first<delayed:raise ValueError('首批日期需不早于原到货日，且早于延期日期')
    if type(first_quantity)!=int or not 0<=first_quantity<=supply[0]:raise ValueError('首批数量必须为采购数量范围内的整数')
    if type(daily_capacity)!=int or not 1<=daily_capacity<=24:raise ValueError('每日产能应为 1 至 24 整数小时')
    orders=c.execute('SELECT order_id,material_need_date,due_date,data_source FROM orders ORDER BY due_date,order_id').fetchall()
    if set(hours)!={o[0] for o in orders}:raise ValueError('必须提供每个订单的总加工工时')
    if any(type(h)!=int or not 1<=h<=720 for h in hours.values()):raise ValueError('订单总工时应为 1 至 720 整数小时')
    if sum(hours.values())>daily_capacity*3650:raise ValueError('当前原型仅支持十年内的累计产能，请缩小数据范围')
    alloc=c.execute('SELECT a.order_id,a.supply_id,a.allocated_quantity,s.material_id,s.available_date FROM allocations a JOIN supplies s USING(supply_id)').fetchall()
    demands=c.execute('SELECT order_id,material_id,required_quantity FROM demands').fetchall()
    # 首批只覆盖本采购已分配数量，按客户交期、订单编号顺序分配，不抢占其他供给。
    rank={o[0]:i for i,o in enumerate(orders)}
    early={};left=first_quantity
    for oid,sid,q,mat,day in sorted(alloc,key=lambda a:rank[a[0]]):
        if sid==supply_id:early[oid]=min(left,q);left-=early[oid]
    results=[];slots=[]
    for scenario in ['原计划基线','全部延期','分批到货（待供应商确认）']:
        used={}
        for oid,need,due,source in orders:
            ready=date.fromisoformat(need);missing=[]
            for doid,mat,required in demands:
                if doid!=oid:continue
                batches=[]
                for aoid,sid,q,amat,day in alloc:
                    if aoid!=oid or amat!=mat:continue
                    if sid!=supply_id or scenario=='原计划基线':batches.append((date.fromisoformat(day),q))
                    elif scenario=='全部延期':batches.append((delayed,q))
                    else:
                        n=early.get(oid,0)
                        if n:batches.append((first,n))
                        if q>n:batches.append((delayed,q-n))
                if sum(q for day,q in batches)<required:missing.append(mat);continue
                total=0
                for day,q in sorted(batches):
                    total+=q
                    if total>=required:ready=max(ready,day);break
            finish=None;start=None
            if not missing:
                remaining=hours[oid];day=ready
                while remaining:
                    free=daily_capacity-used.get(day,0)
                    take=min(free,remaining)
                    if take:
                        start=start or day;finish=day
                        used[day]=used.get(day,0)+take;remaining-=take
                        slots.append({'方案':scenario,'订单编号':oid,'日期':day.isoformat(),'加工小时':take})
                    if remaining:day+=timedelta(days=1)
            late=max(0,(finish-date.fromisoformat(due)).days) if finish else None
            results.append({'方案':scenario,'订单编号':oid,'来源':'模拟数据' if source=='simulation' else '真实数据','最早可开工日':None if missing else ready.isoformat(),'实际排程开始日':start.isoformat() if start else None,'预计完成日期':finish.isoformat() if finish else None,'客户交期':due,'预计逾期天数':late,'总加工小时':hours[oid],'每日共享产能小时':daily_capacity,'首批分配数量':early.get(oid,0) if scenario.startswith('分批') else 0,'判断':'物料分配不足，无法估算' if missing else ('预计逾期' if late else '预计按期'),'依据':'缺少物料分配：'+','.join(missing) if missing else '全部齐套后，按客户交期/编号优先占用最早可用产能；完成日期含当天。'})
    return pd.DataFrame(results),pd.DataFrame(slots,columns=['方案','订单编号','日期','加工小时'])
