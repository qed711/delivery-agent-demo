"""托管平台入口；本地继续使用 app.py。"""
import os,runpy
from pathlib import Path
os.environ['PUBLIC_DEMO']='1'
runpy.run_path(str(Path(__file__).with_name('app.py')),run_name='__main__')
