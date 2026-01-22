CLAUDE_FILES := $(shell git ls-files .claude)
HOME_CLAUDE_FILES := $(patsubst .claude/%,$(HOME)/.claude/%,$(CLAUDE_FILES))

.PHONY: update tts/enable tts/disable

update: $(HOME_CLAUDE_FILES)

$(HOME)/.claude/%: .claude/%
	@mkdir -p $(dir $@)
	cp -f $< $@
	@echo "Copied $< -> $@"

launch-servers:
	python3 tools/tool-launcher/launcher.py

tts/enable:
	curl -X POST http://localhost:37721/enable

tts/disable:
	curl -X POST http://localhost:37721/disable
