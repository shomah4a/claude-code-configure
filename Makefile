include codex.mk

CLAUDE_SYNC_DIRS := rules agents skills
CLAUDE_ROOT_FILES := CLAUDE.md settings.json
CLAUDE_HOME_ROOT_FILES := $(patsubst %,$(HOME)/.claude/%,$(CLAUDE_ROOT_FILES))

.PHONY: update sync-dirs claude/sync launch-servers tts/enable tts/disable

update: codex/generate claude/sync $(CLAUDE_HOME_ROOT_FILES) codex/sync $(CODEX_HOME_ROOT_FILES)

sync-dirs: claude/sync

claude/sync:
	@for dir in $(CLAUDE_SYNC_DIRS); do \
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
