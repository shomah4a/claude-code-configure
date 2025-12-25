CLAUDE_FILES := $(shell git ls-files .claude)
HOME_CLAUDE_FILES := $(patsubst .claude/%,$(HOME)/.claude/%,$(CLAUDE_FILES))

.PHONY: update

update: $(HOME_CLAUDE_FILES)

$(HOME)/.claude/%: .claude/%
	@mkdir -p $(dir $@)
	cp -f $< $@
	@echo "Copied $< -> $@"

launch-servers:
	python3 tools/tool-launcher/launcher.py
