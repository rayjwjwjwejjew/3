"""环境冒烟测试：确认第二阶段安装的核心包都能正常 import。

这些测试不是验证业务逻辑，而是作为"环境是否就绪"的健康检查。
如果任何一条失败，说明虚拟环境有问题，先解决环境再进入第三阶段。
"""

import sys


def test_python_version():
    """V1 锁 Python 3.11，运行时 >= 3.10。"""
    assert sys.version_info >= (3, 10), f"Python too old: {sys.version_info}"


def test_core_imports():
    import pandas
    import numpy
    import pyarrow
    import duckdb
    import matplotlib

    assert pandas.__version__.startswith("2.")
    assert numpy.__version__.startswith("1.") or numpy.__version__.startswith("2.")
    assert pyarrow.__version__.startswith("16.") or pyarrow.__version__.startswith("17.") or pyarrow.__version__.startswith("18.")


def test_data_source_import():
    """V1 选 baostock 作为数据源。"""
    import baostock
    assert baostock.__version__ is not None


def test_jupyter_kernel_registered():
    """Jupyter kernel 必须已注册 a_share_quant。"""
    import json
    import os
    kernel_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".venv", "share", "jupyter", "kernels", "a_share_quant", "kernel.json"
    )
    assert os.path.exists(kernel_path), f"kernel not found: {kernel_path}"
    with open(kernel_path) as f:
        spec = json.load(f)
    assert spec["display_name"].startswith("A-Share Quant")
