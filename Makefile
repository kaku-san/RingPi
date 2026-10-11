.PHONY: test leak

test:
	bash scripts/test.sh

leak:
	python3 scripts/leak-check.py
