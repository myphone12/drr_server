"""测试套件。

运行方式::

    .venv\\Scripts\\python.exe -m pytest -q
    # 或者（不装 pytest 时）
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

测试分三层：

``test_protocol.py``
    协议层：``>`` 分隔格式、``scr_phpdata`` 一致性、dataname 编解码。
``test_services.py``
    业务层：账号、成绩、存档、兑换卡、排行榜的直接调用。
``test_endpoints.py``
    HTTP 层：用 Flask 测试客户端按客户端的真实请求形态打通全部接口。
"""
