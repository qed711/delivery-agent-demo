import json
import pandas as pd
from src.core import analyze_delay
from src.planning import compare_plans
from src.recovery import recommend_recovery
TOPICS=['订单概览','延期影响','方案比较','保交期推荐','产能下降分析','插单分析']
def records(frame):return json.loads(frame.to_json(orient='records',force_ascii=False))
def execute_tool(c,topic,target,context):
    if topic in ['产能下降分析','插单分析']:
        from src.scenarios import capacity_drop,urgent_order
        scenario=context.get('scenario',{})
        expected='capacity' if topic=='产能下降分析' else 'urgent'
        if scenario.get('type')!=expected:raise ValueError('请先在产能与插单页面填写当前情景')
        result=capacity_drop(c,context['hours'],context['capacity'],**scenario['params']) if expected=='capacity' else urgent_order(c,context['hours'],context['capacity'],scenario['params'])
        return {k:(records(v) if isinstance(v,pd.DataFrame) else v) for k,v in result.items()}
    orders=pd.read_sql_query('SELECT order_id,product_name,material_need_date,due_date,data_source FROM orders ORDER BY order_id',c)
    ids=orders.order_id.tolist()
    if target in ['全部','全店','所有订单']:selected=ids
    else:
        selected=[i for i in ids if i.casefold()==target.casefold()]
        if not selected:selected=[i for i in ids if i.casefold().endswith('-'+target.casefold())]
        if len(selected)!=1:raise ValueError('订单不唯一或不存在')
    origins=orders[orders.order_id.isin(selected)].copy()
    origins['data_source']=origins.data_source.replace({'simulation':'模拟数据','real':'真实数据'})
    report={'数据来源':records(origins),'当前参数':context,'限制':'单工序、每天生产、固定分配、齐套开工；分批到货须人工确认。'}
    sid=context['supply_id'];delayed=context['delayed_date']
    if topic=='订单概览':report['结果']=records(origins.rename(columns={'order_id':'订单编号','product_name':'产品','material_need_date':'物料需求日','due_date':'客户交期','data_source':'来源'}))
    elif topic=='延期影响':
        frame=analyze_delay(c,sid,delayed);report['结果']=records(frame[frame['订单编号'].isin(selected)])
        report['限制']+='物料风险不能直接推断交期延期；请调用方案比较核查产能影响。'
    elif topic=='方案比较':
        frame,_=compare_plans(c,sid,delayed,context['first_date'],context['first_quantity'],context['hours'],context['capacity'])
        report['结果']=records(frame[frame['订单编号'].isin(selected)])
    elif topic=='保交期推荐':
        if len(selected)!=1:raise ValueError('推荐必须指定一个目标订单')
        report['结果']=recommend_recovery(c,selected[0],sid,delayed,context['hours'],context['capacity'])
    else:raise ValueError('未知分析工具')
    return report
