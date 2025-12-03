.PHONY: update tts-server

update: $(HOME)/.claude/CLAUDE.md $(HOME)/.claude/settings.json $(addprefix $(HOME)/,$(wildcard .claude/agents/*.md))

$(HOME)/.claude/CLAUDE.md: .claude/CLAUDE.md
	cp -f $< $@

$(HOME)/.claude/settings.json: .claude/settings.json
	cp -f $< $@

$(HOME)/.claude/agents/%.md: .claude/agents/%.md
	mkdir -p $(HOME)/.claude/agents
	cp -f $< $@

launch-servers:
	python3 tools/tool-launcher/launcher.py
