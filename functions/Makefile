FUNCTIONS := dynamic-html graph-pagerank gzip-compression

.PHONY: check test build-push-all verify-images clean-all $(FUNCTIONS)

check:
	@set -e; for function in $(FUNCTIONS); do \
	  $(MAKE) --no-print-directory -C $$function check; \
	done

test:
	@set -e; for function in $(FUNCTIONS); do \
	  $(MAKE) --no-print-directory -C $$function test; \
	done

build-push-all:
	@set -e; for function in $(FUNCTIONS); do \
	  $(MAKE) --no-print-directory -C $$function build-push-all; \
	done

verify-images:
	@set -e; for function in $(FUNCTIONS); do \
	  $(MAKE) --no-print-directory -C $$function verify-images; \
	done

clean-all:
	@set -e; for function in $(FUNCTIONS); do \
	  $(MAKE) --no-print-directory -C $$function clean-all; \
	done
