PYTHON ?= python3
.PHONY: all data figures tables pdf verify
.NOTPARALLEL:
all: verify
data:
	mkdir -p build
	$(PYTHON) scripts/build_data.py > build/family-summary.txt
tables:
	$(PYTHON) scripts/build_tables.py
figures: data
	$(PYTHON) scripts/build_figures.py
pdf: figures tables
	latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex > build/compile.txt 2>&1
	cp build/main.pdf LLM_Assisted_Quantum_Circuit_Optimisation.pdf
verify: pdf
	$(PYTHON) scripts/verify.py
