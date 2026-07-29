.PHONY: help venv install lock test clean notebook

PYTHON ?= python3
VENV   ?= .venv
ACT    := source $(VENV)/bin/activate

help:
	@echo "V1 阶段常用命令："
	@echo "  make venv      创建虚拟环境"
	@echo "  make install   安装 requirements.txt 中的包"
	@echo "  make lock      导出当前已装包到 requirements.lock.txt"
	@echo "  make test      跑 pytest"
	@echo "  make notebook  启动 JupyterLab（已注册 a_share_quant kernel）"
	@echo "  make clean     清理缓存（不动数据）"

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip setuptools wheel

venv: $(VENV)/bin/python

install: venv
	$(VENV)/bin/pip install -r requirements.txt

lock: venv
	$(VENV)/bin/pip freeze > requirements.lock.txt

test: venv
	$(VENV)/bin/pytest tests/ -q

notebook: venv
	$(VENV)/bin/jupyter lab

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -prune -exec rm -rf {} +
