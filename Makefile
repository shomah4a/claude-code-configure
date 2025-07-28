.PHONY: update

update: $(HOME)/.claude/CLAUDE.md $(HOME)/.claude/settings.json

$(HOME)/.claude/CLAUDE.md: .claude/CLAUDE.md
	cp -f $< $@

$(HOME)/.claude/settings.json: .claude/settings.json
	cp -f $< $@
