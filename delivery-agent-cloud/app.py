from pathlib import Path
from datetime import date,timedelta
import json
import pandas as pd
import streamlit as st
from src.core import connect,read_package,replace_dataset,analyze_delay
from src.planning import compare_plans
from src.recovery import recommend_recovery
from src.scenarios import capacity_drop,urgent_order
from src.cases import snapshot,digest
from src.deepseek_agent import load_config,run_deepseek,AgentError
from src.runtime import public_mode,data_dir,cloud_model_config,allow_ai
ROOT=Path(__file__).parent
PUBLIC=public_mode()
st.set_page_config(page_title='交期智协 Agent',layout='wide')
with st.sidebar:
    st.header('交期智协')
    page=st.radio('工作区',['异常分析','处理记录'],label_visibility='collapsed')
    mode='模拟数据' if PUBLIC else st.radio('数据空间',['模拟数据','真实数据（需授权）'])
    source='simulation' if mode=='模拟数据' else 'real'
    workspace=data_dir(ROOT)
    db=workspace/('demo.db' if source=='simulation' else 'real.db')
    fresh=not db.exists()
    c=connect(db)
    if PUBLIC and fresh:replace_dataset(c,read_package(ROOT/'templates'/'模拟案例.zip'),'simulation')
    with st.expander('数据管理'):
        demo_choice=st.selectbox('模拟数据集',['基础案例','含余料插单案例'])
        if st.button('初始化模拟案例',disabled=source!='simulation'):
            replace_dataset(c,read_package(ROOT/'templates'/('模拟案例.zip' if demo_choice=='基础案例' else '插单演示.zip')),source)
            st.success('模拟数据已载入')
        if not PUBLIC:
            f=st.file_uploader('导入四表 ZIP',type=['zip'])
            confirmed=st.checkbox('确认替换当前数据快照')
            if st.button('校验并导入',disabled=not f or not confirmed):
                try:replace_dataset(c,read_package(f),source);st.success('导入成功')
                except Exception as exc:st.error(f'导入失败：{exc}')
    order_ids=[r[0] for r in c.execute('SELECT order_id FROM orders ORDER BY order_id')]
    if order_ids:
        with st.expander('生产基础参数'):
            capacity=st.number_input('每日共享产能（小时）',1,24,8)
            hours={oid:int(st.number_input(f'{oid} 总加工小时',1,720,32,key='hours_'+source+'_'+oid)) for oid in order_ids}
            st.caption('默认32小时仅为演示假设。')
key,model=cloud_model_config() if PUBLIC else load_config(ROOT.parent/'bookstore-agent')
st.title('交期智协 Agent')
st.caption('识别交付风险 · 推演可行方案 · 跟进人工决策')
if PUBLIC:st.caption('公网模拟演示 · 各访客数据独立 · 会话结束后不保证保留处理记录，请及时导出')
if not order_ids:
    st.info('从左侧“数据管理”载入模拟案例或导入数据，即可开始。')
    c.close();st.stop()
options=c.execute("SELECT supply_id,material_id,available_date,quantity FROM supplies WHERE supply_type='purchase' ORDER BY supply_id").fetchall()
start_default=date.fromisoformat(c.execute('SELECT MIN(material_need_date) FROM orders').fetchone()[0])
if page=='处理记录':
    if options:
        with st.expander('建立处理单的采购与目标',expanded=True):
            mapping={r[0]:r for r in options};sid=st.selectbox('本次延期采购',list(mapping));row=mapping[sid];original=date.fromisoformat(row[2])
            changed=st.date_input('延期后到货日',value=original+timedelta(days=3),min_value=original+timedelta(days=1),key='case_delay_'+source+'_'+sid)
            target=st.selectbox('优先保障订单',order_ids)
    else:sid=None;target=order_ids[0];changed=start_default
    st.subheader('保存方案，跟进反馈')
    from src.cases import prepare as prepare_cases,create_case,get_case,record_feedback,approve,is_stale
    import json
    import pandas as pd
    prepare_cases(c)
    st.caption('处理单保留创建时的数据和参数。供应商反馈由你手动录入；此页面不发送消息、不下采购单、不修改生产计划。')
    if options and st.button('按当前参数建立处理单'):
        try:
            case_id,created=create_case(c,target,sid,changed,hours,int(capacity))
            st.success(f'已建立处理单 #{case_id}' if created else f'相同方案已有处理单 #{case_id}，未重复创建')
        except ValueError as exc:st.error(str(exc))
    case_ids=[r[0] for r in c.execute('SELECT id FROM cases ORDER BY id DESC')]
    if not case_ids:st.info('先选择采购与目标订单，再建立可行方案的处理单。')
    else:
        chosen=st.selectbox('查看处理单',case_ids,format_func=lambda v:f'处理单 #{v}')
        case=get_case(c,chosen);saved=case['方案'];rec=saved['推荐']
        st.write(f"**状态：{case['状态']}**")
        st.write(f"目标订单：{saved['目标订单']}｜采购：{saved['采购供给']}｜建议最晚 {rec['最晚首批日期']} 先到 {rec['首批数量']} 件")
        stale=is_stale(c,case)
        if stale:st.warning('业务数据已变化。本单只供历史查看和反馈记录，请按最新数据重新建立处理单，旧单不能继续确认。')
        if case['状态']!='已人工确认（未执行）':
            with st.form('supplier_feedback_'+source+'_'+str(chosen)):
                agreed=st.radio('供应商反馈（手动记录）',['同意提供首批','不同意分批'])
                promised_day=st.date_input('供应商承诺首批日期',value=date.fromisoformat(rec['最晚首批日期']))
                promised_qty=st.number_input('供应商承诺首批数量',min_value=0,value=int(rec['首批数量']),step=1)
                feedback_note=st.text_input('反馈备注（不要填写个人联系方式）',max_chars=500)
                submit_feedback=st.form_submit_button('保存反馈并重新核算')
            if submit_feedback:
                try:
                    record_feedback(c,chosen,agreed=='同意提供首批',promised_day,int(promised_qty),feedback_note)
                    c.close()
                    st.rerun()
                except ValueError as exc:st.error(str(exc))
        if case['反馈']:
            feedback=case['反馈']
            if feedback.get('复核结果'):
                view=[]
                for r in feedback['复核结果']:
                    late=r['预计逾期天数']
                    view.append({'订单':r['订单编号'],'预计完成':r['预计完成日期'] or '无法估算','复核判断':'缺料，无法估算' if late is None else ('预计按期' if late==0 else f'晚 {int(late)} 天')})
                st.dataframe(pd.DataFrame(view),hide_index=True)
            if case['状态']=='需重新协商':st.warning('反馈方案不能保住目标交期，或会增加其他订单逾期，请重新协商。')
        if case['状态']=='待人工确认' and not stale:
            checked=st.checkbox('我已核查供应商反馈和复核结果，确认保存此方案（未执行）',key='approve_'+source+'_'+str(chosen)+'_'+case['反馈']['记录时间UTC'])
            if st.button('人工确认方案',disabled=not checked):
                try:approve(c,chosen);c.close();st.rerun()
                except ValueError as exc:st.error(str(exc))
        events=pd.read_sql_query('SELECT time_utc AS 时间UTC,action AS 操作,detail AS 记录 FROM case_events WHERE case_id=? ORDER BY id',c,params=(chosen,))
        with st.expander('查看处理记录与保存的分析依据'):
            st.dataframe(events,hide_index=True)
            st.json(case)
        export=dict(case);export['处理记录']=json.loads(events.to_json(orient='records',force_ascii=False))
        st.download_button('下载完整处理单 JSON',json.dumps(export,ensure_ascii=False,indent=2).encode('utf-8'),f'异常处理单_{chosen}.json','application/json')


else:
    event=st.radio('本次发生了什么？',['供应商延期','临时产能下降','紧急插单'],horizontal=True)
    st.caption('01 确认异常　→　02 分析与推荐　→　03 人工跟进')
    if event=='供应商延期' and not options:
        st.info('当前没有采购供给，请切换其他场景。');c.close();st.stop()
    with st.expander('异常信息 · 点击修改',expanded=True):
        if event=='供应商延期':
            mapping={r[0]:r for r in options};x,y,z=st.columns(3)
            sid=x.selectbox('延期采购',list(mapping));row=mapping[sid];original=date.fromisoformat(row[2])
            changed=y.date_input('延期后到货日',value=original+timedelta(days=3),min_value=original+timedelta(days=1),key='delay_'+source+'_'+sid+'_'+row[2])
            target=z.selectbox('优先保障订单',order_ids)
            st.caption(f'原到货 {original} · 物料 {row[1]} · 数量 {row[3]} 件')
            event_params={'supply_id':sid,'delayed_date':str(changed),'target':target}
        else:
            if event=='临时产能下降':
                x,y,z=st.columns(3)
                loss_start=x.date_input('下降开始日',value=start_default)
                loss_end=y.date_input('恢复前最后一天',value=start_default+timedelta(days=1))
                reduced=z.number_input('期间每日可用小时（0表示停产）',0,int(capacity)-1,0)
                event_params={'start':str(loss_start),'end':str(loss_end),'reduced':int(reduced)}
                st.caption('日期首尾均包含，结束次日恢复正常产能；范围最多31天。')
            else:
                st.caption('不抢占已分配物料。暂支持新单一种关键物料，其余用料需另行确认；总需求数量不是单件用量。')
                x,y=st.columns(2)
                urgent_id=x.text_input('新订单编号',value='SIM-URG001')
                urgent_product=y.text_input('产品名称',value='模拟紧急订单')
                urgent_count=x.number_input('产品数量（件）',1,1_000_000_000,5)
                materials=[r[0] for r in c.execute('SELECT DISTINCT material_id FROM supplies ORDER BY material_id')]
                urgent_material=y.selectbox('关键物料编号',materials)
                urgent_required=x.number_input('该物料总需求（件）',1,1_000_000_000,5)
                urgent_hours=y.number_input('新单总加工小时',1,720,16)
                urgent_need=x.date_input('新单最早用料/开工日',value=start_default)
                urgent_due=y.date_input('新单承诺交期',value=start_default+timedelta(days=1))
                event_params={'order_id':urgent_id.strip(),'product_name':urgent_product.strip(),'order_quantity':int(urgent_count),'material_id':urgent_material,'required_quantity':int(urgent_required),'material_need_date':str(urgent_need),'due_date':str(urgent_due),'hours':int(urgent_hours)}

    context={'hours':hours,'capacity':int(capacity)}
    if event=='供应商延期':context.update(supply_id=sid,delayed_date=str(changed),first_date=str(original),first_quantity=0)
    else:context['scenario']={'type':'capacity' if event=='临时产能下降' else 'urgent','params':event_params}
    signature=digest({'data':snapshot(c),'context':context,'event':event,'params':event_params,'source':source,'model':model})
    if st.session_state.get('unified_signature')!=signature:
        st.session_state.pop('unified_result',None);st.session_state.pop('unified_answer',None)
        st.session_state['unified_signature']=signature
    with st.container(border=True):
        prompts={'供应商延期':'哪些订单会受影响？怎样保住目标订单交期？','临时产能下降':'产能下降会影响哪些承诺？下一步应该怎么处理？','紧急插单':'这张新单能接吗？会让哪些原订单晚交？'}
        question=st.text_area('希望 Agent 帮你判断什么？',value=prompts[event],height=90,key='question_'+source+'_'+event)
        ai_allowed=allow_ai()
        permission=st.checkbox('允许将问题、参数与分析结果发送至 DeepSeek（可能产生费用）',key='cloud_'+source)
        x,y=st.columns(2)
        ask=x.button('Agent 分析并解释',type='primary',disabled=not key or not permission or not ai_allowed)
        local=y.button('先看本地计算结果')
        if not key:st.caption('未读取到密钥，可先使用本地计算。')
    if ask and PUBLIC:
        used=st.session_state.get('_api_uses',0)
        if used>=5:st.error('本次演示会话的AI请求次数已用完，仍可使用本地计算。');ask=False
        else:st.session_state['_api_uses']=used+1
    if ask or local:
        st.session_state.pop('unified_result',None);st.session_state.pop('unified_answer',None)
        try:
            with st.spinner('正在核查物料、产能与交期…'):
                if event=='供应商延期':
                    r=recommend_recovery(c,target,sid,changed,hours,int(capacity))
                    plans,_=compare_plans(c,sid,changed,original,0,hours,int(capacity));delayed=plans[plans['方案']=='全部延期']
                    outcome={'类型':event,'推荐':r,'预计晚交订单':int((delayed['预计逾期天数']>0).sum()),'无法估算订单':int(delayed['预计逾期天数'].isna().sum())}
                elif event=='临时产能下降':outcome=capacity_drop(c,hours,int(capacity),**event_params)
                else:outcome=urgent_order(c,hours,int(capacity),event_params)
                st.session_state['unified_result']=outcome
            if ask:
                q=question+(f'\n当前优先目标订单：{target}' if event=='供应商延期' else '')
                with st.spinner('Agent 正在调用业务工具…'):
                    answer=run_deepseek(c,q,context,key=key,model=model)
                    st.session_state['unified_answer']={'question':question,'answer':answer}
        except (ValueError,AgentError) as exc:st.error(str(exc))
    outcome=st.session_state.get('unified_result');saved=st.session_state.get('unified_answer')
    if outcome:
        st.subheader('分析结论')
        st.caption(mode+' · 程序计算，非真实经营成效')
        if event=='供应商延期':
            x,y=st.columns(2);x.metric('全部延期后预计晚交',outcome['预计晚交订单']);y.metric('缺料、无法估算',outcome['无法估算订单'])
            r=outcome['推荐']
            if r['状态']=='找到可行方案':
                st.success(f"最晚 {r['最晚首批日期']} 先到 {r['首批数量']} 件，可保住 {target}。")
                st.caption(f"预计完成 {r['目标预计完成']}，客户交期 {r['客户交期']}；供应商承诺尚待确认。")
                st.dataframe(pd.DataFrame(r['订单影响']),hide_index=True)
                st.info('下一步：到“处理记录”保存方案，录入供应商反馈后复核。')
            else:st.info(r['状态']);st.write(r['说明'])
        else:
            x,y=st.columns(2)
            if event=='临时产能下降':x.metric('完成日后移订单',outcome['受影响订单数']);y.metric('逾期增加订单',outcome['新增逾期订单数'])
            else:x.metric('新单缺料（件）',outcome['缺料数量']);y.metric('逾期增加的原订单',outcome['新增逾期原订单数'])
            st.info(outcome['结论'])
            st.dataframe(outcome['订单影响'][['订单','客户交期','原预计完成','调整后完成','调整后判断']],hide_index=True)
            st.caption('下一步：核查受影响订单，再与生产或客户协商。尚未执行接单或生产调整。')
        if saved:
            st.subheader('Agent 建议');st.caption('本次问题：'+saved['question'])
            st.write(saved['answer']['回答']);st.caption(saved['answer']['数据核查提示'])
        with st.expander('核查依据与导出'):
            export={k:(json.loads(v.to_json(orient='records',force_ascii=False)) if isinstance(v,pd.DataFrame) else v) for k,v in outcome.items()}
            export['页面参数']=context
            if saved:export['Agent记录']=saved;st.json(saved['answer'])
            if event!='供应商延期':st.dataframe(outcome['每日排程'],hide_index=True)
            st.json(export)
            st.download_button('下载本次完整分析',json.dumps(export,ensure_ascii=False,indent=2).encode('utf-8'),'交付异常分析.json','application/json')
    else:st.caption('确认异常信息后，选择 Agent 分析或本地计算。')
    if event=='供应商延期':
        with st.expander('高级试算 · 手动比较分批方案'):
            x,y=st.columns(2)
            first_day=x.date_input('试算首批日期',value=original,min_value=original,max_value=changed-timedelta(days=1),key='trialday_'+source+'_'+sid+'_'+str(changed))
            first_qty=y.number_input('试算首批数量',0,int(row[3]),min(40,int(row[3])),key='trialqty_'+source+'_'+sid+'_'+str(row[3]))
            if st.button('计算并比较方案'):
                try:
                    comparison,calendar=compare_plans(c,sid,changed,first_day,int(first_qty),hours,int(capacity))
                    import pandas as pd
                    def completion_label(r):
                        if pd.isna(r['预计完成日期']):
                            return '缺料，无法估算'
                        status='按期' if r['预计逾期天数']==0 else f"晚 {int(r['预计逾期天数'])} 天"
                        return f"{r['预计完成日期']} · {status}"
                    simple=[]
                    for oid,group in comparison.groupby('订单编号',sort=True):
                        plans=group.set_index('方案')
                        baseline=plans.loc['原计划基线']
                        delayed_plan=plans.loc['全部延期']
                        split_plan=plans.loc['分批到货（待供应商确认）']
                        if pd.isna(delayed_plan['预计逾期天数']) or pd.isna(split_plan['预计逾期天数']):
                            benefit='先补齐缺料信息'
                        else:
                            saved=int(delayed_plan['预计逾期天数']-split_plan['预计逾期天数'])
                            benefit=f'少晚 {saved} 天' if saved>0 else (f'多晚 {-saved} 天' if saved<0 else '逾期天数不变')
                        simple.append({'订单':oid,'客户交期':baseline['客户交期'],
                                       '原计划完成':completion_label(baseline),
                                       '全部延期后完成':completion_label(delayed_plan),
                                       '分批到货后完成':completion_label(split_plan),
                                       '分批方案效果':benefit})
                    st.write('**哪张订单会晚交？分批到货有没有帮助？**')
                    st.dataframe(pd.DataFrame(simple),hide_index=True)
                    st.caption('每格显示“预计完成日期 · 是否晚交”。分批方案效果与“全部延期”相比，需供应商确认后才可执行。')
                    with st.expander('查看详细计算与每日产能分配'):
                        st.write('详细计算')
                        st.dataframe(comparison,hide_index=True)
                        st.write('每日产能分配')
                        st.dataframe(calendar,hide_index=True)
                    comparison['采购供给']=sid
                    comparison['延期后到货日期']=changed.isoformat()
                    comparison['假设首批日期']=first_day.isoformat()
                    comparison['假设首批总数量']=int(first_qty)
                    comparison['模型假设']='单工序共享产能；每天生产；全部齐套；交期优先；未含运输检验'
                    st.download_button('下载方案比较 CSV',comparison.to_csv(index=False).encode('utf-8-sig'),'交期方案比较.csv','text/csv')
                    st.download_button('下载每日产能分配 CSV',calendar.to_csv(index=False).encode('utf-8-sig'),'每日产能分配.csv','text/csv')
                except ValueError as exc:st.error(str(exc))
            st.caption('比较结果不修改采购日期、库存或分配关系。参数变化后请重新计算。')
            with st.expander('物料影响明细与列名解释'):
                result=analyze_delay(c,sid,changed.isoformat())
                st.dataframe(result,hide_index=True)
                st.caption('延期前/后按时可用：在物料需求日能拿到的已分配数量。原有缺口：原计划就缺的量；延期后缺口：延期后总缺量；新增缺口：本次延期额外造成的量。本采购分配数量：所选采购中分配给本订单的量。数量单位：件。')
                st.download_button('下载物料影响明细',result.to_csv(index=False).encode('utf-8-sig'),'延期影响明细.csv','text/csv')

with st.expander('适用范围'):
    st.caption('单工序、每日生产、齐套开工、交期优先、共享产能，不含换线运输检验。三个场景独立模拟。插单暂仅核算一种关键物料。结果供人工核查，未执行采购、接单或生产变更。')
c.close()
