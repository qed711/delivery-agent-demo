"""在固定分配与排程规则内枚举首批方案，不声称全局最优。"""
from datetime import date,timedelta
import pandas as pd
from src.planning import compare_plans

def recommend_recovery(c,target,supply_id,delayed_date,hours,daily_capacity=8):
    supply=c.execute("SELECT available_date FROM supplies WHERE supply_id=? AND supply_type='purchase'",(supply_id,)).fetchone()
    if not supply:raise ValueError('采购供给不存在')
    original=date.fromisoformat(supply[0]);delayed=date.fromisoformat(str(delayed_date))
    span=(delayed-original).days
    if not 1<=span<=31:raise ValueError('本模块暂支持 1 至 31 天的延期范围')
    base,_=compare_plans(c,supply_id,delayed,original,0,hours,daily_capacity)
    baseline=base[base['方案']=='全部延期'].set_index('订单编号')
    if target not in baseline.index:raise ValueError('订单不存在')
    result={'目标订单':target,'采购供给':supply_id,'延期日期':delayed.isoformat(),'假设':{'每日产能':daily_capacity,'订单总工时':hours,'规则':'单工序、每天工作、齐套开工、交期优先；首批按已有分配顺序覆盖；其他订单逾期天数不得增加'},'检查方案数':0,'状态':'','说明':''}
    if pd.isna(baseline.loc[target,'预计完成日期']):
        result.update(状态='数据不足',说明='目标订单物料分配不足，单纯提前本采购不能补齐，请先核查物料需求和分配。');return result
    if baseline.loc[target,'预计逾期天数']==0:
        result.update(状态='无需提前到货',说明='按当前假设，全部延期到货仍可保住该订单交期。');return result
    allocations=c.execute('SELECT a.allocated_quantity FROM allocations a JOIN orders o USING(order_id) WHERE a.supply_id=? ORDER BY o.due_date,o.order_id',(supply_id,)).fetchall()
    # 齐套模型下，只有完全覆盖下一订单分配时才可能改变开工日期。
    quantities=[];total=0
    for (q,) in allocations:total+=q;quantities.append(total)
    if len(quantities)*span>500:raise ValueError('候选方案超过 500 个，请缩小订单或延期范围')
    for qty in quantities:
        for offset in range(span-1,-1,-1):
            first=original+timedelta(days=offset)
            comparison,calendar=compare_plans(c,supply_id,delayed,first,qty,hours,daily_capacity)
            result['检查方案数']+=1
            plan=comparison[comparison['方案'].str.startswith('分批')].set_index('订单编号')
            if pd.isna(plan.loc[target,'预计逾期天数']) or plan.loc[target,'预计逾期天数']>0:continue
            if any(pd.notna(baseline.loc[oid,'预计逾期天数']) and (pd.isna(plan.loc[oid,'预计逾期天数']) or plan.loc[oid,'预计逾期天数']>baseline.loc[oid,'预计逾期天数']) for oid in baseline.index if oid!=target):continue
            effects=[]
            for oid in baseline.index:
                old=baseline.loc[oid,'预计逾期天数'];new=plan.loc[oid,'预计逾期天数']
                effects.append({'订单':oid,'全部延期时完成':baseline.loc[oid,'预计完成日期'] or '无法估算','推荐方案完成':plan.loc[oid,'预计完成日期'] or '无法估算','影响':'原有缺料，仍无法估算' if pd.isna(old) else (f'少晚 {int(old-new)} 天' if new<old else '逾期天数不变')})
            result.update(状态='找到可行方案',首批数量=qty,最晚首批日期=first.isoformat(),目标预计完成=plan.loc[target,'预计完成日期'],客户交期=plan.loc[target,'客户交期'],订单影响=effects,说明='在当前固定分配、交期优先和单次分批规则内，先最小化首批数量，再选择最晚可行到货日期；供应商能力尚未确认。')
            return result
    result.update(状态='当前规则内无可行方案',说明='已检查允许日期和有效首批数量。需要人工考虑增加产能、调整分配、其他供给或协商交期；不能据此认定所有现实方案均不可行。')
    return result
