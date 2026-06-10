run:
	devenv shell run-app

test:
	devenv shell run-tests

check-syntax:
	find src -name "*.py" -exec python3 -m py_compile {} +

lint:
	find src -name "*.py" -exec python3 -m py_compile {} +
