.PHONY: install
install:
	poetry install --no-dev

.PHONY: install-dev
install-dev:
	poetry install

.PHONY: check-format
check-format:
	poetry run isort --check-only ./
	poetry run black --check ./

.PHONY: format
format:
	poetry run isort ./
	poetry run black ./

.PHONY: lint
lint:
	poetry run pylint ./

.PHONY: publish
publish:
	rm -rf credit-risk/_build/
	rm -rf docs/
	mkdir -p docs/
	jupyter-book build --execute credit-risk/
	cp -r credit-risk/_build/* docs/
	git add docs/ \
	&& git commit -m "Publish updates" \
	&& git push