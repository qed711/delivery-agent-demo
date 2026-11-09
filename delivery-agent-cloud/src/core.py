"""基于明确供料分配关系的物料延期模拟，不承诺最终交期。"""
import sqlite3
import io
import zipfile
from datetime import date
import pandas as pd

FIELDS={
 'orders':['order_id','product_name','order_quantity','material_need_date','due_date','data_source'],
 'demands':['order_id','material_id','required_quantity'],
 'supplies':['supply_id','material_id','supply_type','quantity','available_date'],
 'allocations':['order_id','supply_id','allocated_quantity'],
}
SCHEMA="""
CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY,product_name TEXT NOT NULL,order_quantity INTEGER NOT NULL CHECK(order_quantity>0),material_need_date TEXT NOT NULL,due_date TEXT NOT NULL,data_source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS demands(order_id TEXT REFERENCES orders(order_id),material_id TEXT,required_quantity INTEGER NOT NULL CHECK(required_quantity>0),PRIMARY KEY(order_id,material_id));
CREATE TABLE IF NOT EXISTS supplies(supply_id TEXT PRIMARY KEY,material_id TEXT NOT NULL,supply_type TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>=0),available_date TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS allocations(order_id TEXT REFERENCES orders(order_id),supply_id TEXT REFERENCES supplies(supply_id),allocated_quantity INTEGER NOT NULL CHECK(allocated_quantity>0),PRIMARY KEY(order_id,supply_id));
"""
def connect(path):
    c=sqlite3.connect(path);c.execute('PRAGMA foreign_keys=ON');c.executescript(SCHEMA);return c

def read_package(file):
    with zipfile.ZipFile(file) as z:
        if sum(i.file_size for i in z.infolist())>5_000_000: raise ValueError('解压后数据不能超过 5MB')
        tables={}
        for name in FIELDS:
            filename=name+'.csv'
            if z.namelist().count(filename)!=1: raise ValueError('压缩包根目录需有且仅有一个 '+filename)
            tables[name]=pd.read_csv(io.BytesIO(z.read(filename)),dtype=str,keep_default_na=False)
    return tables

def validate(tables,source):
    if source not in ('simulation','real'): raise ValueError('数据来源无效')
    result={}
    keys={'orders':['order_id'],'demands':['order_id','material_id'],'supplies':['supply_id'],'allocations':['order_id','supply_id']}
    numeric={'orders':['order_quantity'],'demands':['required_quantity'],'supplies':['quantity'],'allocations':['allocated_quantity']}
    for name,fields in FIELDS.items():
        if name not in tables or set(fields)-set(tables[name].columns): raise ValueError(name+' 缺少必需字段')
        d=tables[name][fields].fillna('').astype(str).apply(lambda x:x.str.strip())
        if d.empty and name!='allocations': raise ValueError(name+' 不能为空')
        if d.eq('').any().any(): raise ValueError(name+' 有空字段')
        if d.duplicated(keys[name]).any(): raise ValueError(name+' 编号重复')
        for col in numeric[name]:
            if not d[col].str.fullmatch(r'[0-9]+').all(): raise ValueError(name+' 数量必须为整数')
            d[col]=d[col].map(int)
            if (d[col]>1_000_000_000).any() or (d[col]<(0 if name=='supplies' else 1)).any(): raise ValueError(name+' 数量超出范围')
        for col in [x for x in fields if x.endswith('_date')]:
            try:
                if not d[col].str.fullmatch(r'\d{4}-\d{2}-\d{2}').all(): raise ValueError()
                d[col]=d[col].map(lambda v:date.fromisoformat(v).isoformat())
            except ValueError: raise ValueError(name+' 日期格式应为 YYYY-MM-DD') from None
        result[name]=d
    orders=result['orders'];demands=result['demands'];supplies=result['supplies'];alloc=result['allocations']
    if not orders.data_source.eq(source).all(): raise ValueError('订单数据来源与所选空间不一致')
    if (orders.material_need_date>orders.due_date).any(): raise ValueError('物料需求日期不能晚于交期')
    if not supplies.supply_type.isin(['stock','purchase']).all(): raise ValueError('供给类型应为 stock 或 purchase')
    order_ids=set(orders.order_id)
    if not set(demands.order_id)<=order_ids or not set(alloc.order_id)<=order_ids: raise ValueError('引用了不存在的订单')
    if set(demands.order_id)!=order_ids: raise ValueError('每个订单至少需要一条物料需求')
    if not set(alloc.supply_id)<=set(supplies.supply_id): raise ValueError('分配引用了不存在的供给')
    merged=alloc.merge(supplies,on='supply_id')
    totals=merged.groupby('supply_id').allocated_quantity.sum()
    caps=supplies.set_index('supply_id').quantity
    if any(v>caps[k] for k,v in totals.items()): raise ValueError('供给被超额分配，存在重复占用')
    demand_map=demands.set_index(['order_id','material_id']).required_quantity.to_dict()
    for key,value in merged.groupby(['order_id','material_id']).allocated_quantity.sum().items():
        if key not in demand_map or value>demand_map[key]: raise ValueError('分配物料不匹配或超过订单需求')
    return result

def replace_dataset(c,tables,source):
    d=validate(tables,source)
    with c:
        for name in ['allocations','demands','orders','supplies']:c.execute('DELETE FROM '+name)
        for name in ['orders','demands','supplies','allocations']:
            fields=FIELDS[name]
            c.executemany('INSERT INTO '+name+' VALUES ('+','.join('?' for _ in fields)+')',d[name].itertuples(index=False,name=None))

def analyze_delay(c,supply_id,new_date):
    new_date=date.fromisoformat(str(new_date)).isoformat()
    supply=c.execute('SELECT material_id,supply_type,available_date FROM supplies WHERE supply_id=?',(supply_id,)).fetchone()
    if not supply or supply[1]!='purchase': raise ValueError('请选择有效采购供给')
    if new_date<=supply[2]: raise ValueError('延期日期必须晚于原计划到货日期')
    rows=[]
    demands=c.execute('SELECT d.order_id,o.product_name,d.material_id,d.required_quantity,o.material_need_date,o.due_date,o.data_source FROM demands d JOIN orders o USING(order_id) ORDER BY d.order_id,d.material_id').fetchall()
    for oid,product,material,required,need,due,source in demands:
        allocations=c.execute('SELECT a.supply_id,a.allocated_quantity,s.available_date FROM allocations a JOIN supplies s USING(supply_id) WHERE a.order_id=? AND s.material_id=?',(oid,material)).fetchall()
        before=sum(q for sid,q,day in allocations if day<=need)
        after=sum(q for sid,q,day in allocations if (new_date if sid==supply_id else day)<=need)
        shortage_before=max(0,required-before);shortage_after=max(0,required-after)
        related=sum(q for sid,q,day in allocations if sid==supply_id)
        delta=shortage_after-shortage_before
        status='新增物料缺口' if delta>0 else ('原有缺口未增加' if shortage_after>0 else ('延期未跨物料需求日' if related else '未受本次延期影响'))
        rows.append({'订单编号':oid,'产品':product,'物料编号':material,'来源':'模拟数据' if source=='simulation' else '真实数据','需求数量':required,'物料需求日期':need,'客户交期':due,'延期前按时可用':before,'延期后按时可用':after,'原有缺口':shortage_before,'延期后缺口':shortage_after,'新增缺口':delta,'本采购分配数量':related,'风险判断':status,'依据':f'需求 {required}；按时可用 {before}→{after}；采购 {supply_id} 到货 {supply[2]}→{new_date}；按已有分配计算。'})
    return pd.DataFrame(rows)
