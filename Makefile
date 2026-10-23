.PHONY: build test train run seed clean

build:          ## compile the C engine
	$(MAKE) -C engine

test: build     ## C tests + Python tests
	$(MAKE) -C engine test
	cd tests && python -m unittest -v test_engine test_app test_features test_admin test_concurrency

train:          ## retrain both models (synthetic data unless you pass CSVs)
	python ml/train_models.py

run: build      ## dev server with demo data
	SEED_ON_START=1 flask --app wsgi run --debug

seed: build
	flask --app wsgi seed

clean:
	$(MAKE) -C engine clean
	rm -rf instance/*.db*
