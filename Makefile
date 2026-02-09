SYNC_DIRS := rules agents skills
ROOT_FILES := CLAUDE.md settings.json
HOME_ROOT_FILES := $(patsubst %,$(HOME)/.claude/%,$(ROOT_FILES))

.PHONY: update sync-dirs tts/enable tts/disable

update: sync-dirs $(HOME_ROOT_FILES)

sync-dirs:
	@for dir in $(SYNC_DIRS); do \
		mkdir -p $(HOME)/.claude/$$dir; \
		rsync -av --delete .claude/$$dir/ $(HOME)/.claude/$$dir/; \
	done

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
