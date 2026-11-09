"""托管演示环境：访客隔离、服务端配置、AI演示口令。"""
import os,hmac,tempfile
from pathlib import Path
import streamlit as st

def secret(name,default=''):
    if name in os.environ:return os.environ[name]
    try:return str(st.secrets.get(name,default))
    except (FileNotFoundError,st.errors.StreamlitSecretNotFoundError):return default

def public_mode():return os.environ.get('PUBLIC_DEMO','0')=='1'

def data_dir(root):
    if not public_mode():
        folder=Path(root)/'data';folder.mkdir(exist_ok=True);return folder
    if '_demo_workspace' not in st.session_state:
        st.session_state['_demo_workspace']=tempfile.TemporaryDirectory(prefix='delivery_demo_')
    return Path(st.session_state['_demo_workspace'].name)

def cloud_model_config():return secret('DEEPSEEK_API_KEY'),secret('DEEPSEEK_MODEL','deepseek-flash')

def allow_ai():
    if not public_mode():return True
    code=secret('DEMO_AI_CODE')
    if not code:
        st.caption('公网演示可使用本地计算；AI解读尚未由站点管理员开放。');return False
    entered=st.text_input('AI演示访问口令',type='password',key='demo_ai_code')
    return bool(entered and hmac.compare_digest(entered.encode("utf-8"),code.encode("utf-8")))
