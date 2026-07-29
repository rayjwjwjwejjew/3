.PHONY: help venv install lock test clean notebook download clean-data self-test

PYTHON ?= python3
VENV   ?= .venv
ACT    := source $(VENV)/bin/activate

help:
	@echo "V1 阶段常用命令："
	@echo "  make venv        创建虚拟环境"
	@echo "  make install     安装 requirements.txt 中的包"
	@echo "  make lock        导出当前已装包到 requirements.lock.txt"
	@echo "  make test        跑 pytest"
	@echo "  make notebook    启动 JupyterLab（已注册 a_share_quant kernel）"
	@echo "  make download    阶段 4：从 baostock 拉原始数据（需联网）"
	@echo "  make clean-data  阶段 4：raw → processed"
	@echo "  make self-test   阶段 4：用 fixture 跑完 download+clean 管道（不联网）"
	@echo "  make clean       清理缓存（不动数据）"

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

download: venv
	$(VENV)/bin/python -m src download

clean-data: venv
	$(VENV)/bin/python -m src clean

self-test: venv
	$(VENV)/bin/python -m src self-test

backtest: venv
	$(VENV)/bin/python -m src backtest --initial-cash 1000000 --out results/nav.csv

validate: venv
	@if [ -z "$(ASOF)" ]; then echo "usage: make validate ASOF=YYYY-MM-DD"; exit 1; fi
	$(VENV)/bin/python -m src validate --asof $(ASOF)

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -prune -exec rm -rf {} +
