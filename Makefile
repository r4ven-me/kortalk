# Release automation: stage everything, commit, tag vX.Y.Z (read from
# pyproject.toml's [project] version), then push the branch and the tag.
# Pushing the tag is what triggers .github/workflows/publish.yml (PyPI).
#
# Usage:
#   make release                    # add, commit, tag and push in one go
#   make release MSG="Fix popup"    # custom commit message (default below)
#   make add / commit / tag / push  # run a single step on its own
#   make version                    # print the tag this would create

VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
TAG     := v$(VERSION)
MSG     ?= Release $(TAG)

.PHONY: release add commit tag push version

release: add commit tag push

version:
	@echo $(TAG)

add:
	git add -A

commit:
	git commit -m "$(MSG)"

tag:
	git tag $(TAG)

push:
	git push
	git push origin $(TAG)
