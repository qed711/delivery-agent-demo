# 交期智协 Agent · 公网模拟演示

Streamlit Community Cloud 入口：cloud_app.py，Python 3.12。

requirements.txt 安装依赖。默认自动载入模拟案例，各访客独立的临时数据，关闭/重启后不保证保留，请导出处理单。本发布包不包含真实业务数据、数据库或密钥；公网版本仅演示模拟数据，不开放真实数据上传。

在平台 Advanced settings → Secrets 单独配置：
```toml
DEEPSEEK_API_KEY = "填入你自己的密钥"
DEEPSEEK_MODEL = "deepseek-flash"
DEMO_AI_CODE = "设置一个仅分享给受邀体验者的演示口令"
```

可以暂不配置Secrets，所有本地计算功能仍能体验。AI功能同时要求服务端密钥、演示口令、访客输入正确口令和同意发送数据。单次会话最多5次AI分析，每次内部最多4次API请求；会话限制不是账户总预算，请在DeepSeek账户监控实际费用。演示口令只控制AI入口，不是整个网站的登录鉴权。

不要把真实密钥写入GitHub；secrets.example.toml 仅为空白示例。平台Secrets中的配置才会在云端生效。云端没有你原电脑的 .env 文件。

项目为单工序、每天生产、齐套开工和交期优先的模拟原型，不是通用工业排程系统。当前支持供应商延期、临时产能下降和紧急插单；所有默认业务案例完全虚构。

本地命令：python -m streamlit run cloud_app.py
