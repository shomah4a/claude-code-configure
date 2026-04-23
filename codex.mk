SHELL := bash

CODEX_ROOT_FILES := AGENTS.md
CODEX_SYNC_DIRS := skills
CODEX_HOME_ROOT_FILES := $(patsubst %,$(HOME)/.codex/%,$(CODEX_ROOT_FILES))
CLAUDE_RULE_FILES := $(wildcard .claude/rules/*.md)
CLAUDE_SKILL_FILES := $(wildcard .claude/skills/*/SKILL.md)
CLAUDE_SKILL_EXTRA_FILES := $(filter-out $(CLAUDE_SKILL_FILES),$(wildcard .claude/skills/*/*))
CLAUDE_AGENT_FILES := $(wildcard .claude/agents/*.md)
CODEX_SKILLS_STAMP := .codex/.skills.stamp

.PHONY: codex/generate codex/sync codex/clean

codex/generate: .codex/AGENTS.md $(CODEX_SKILLS_STAMP)
	@rm -rf .codex/rules

.codex/AGENTS.md: $(CLAUDE_RULE_FILES) codex.mk
	@mkdir -p $(dir $@)
	@cat $(CLAUDE_RULE_FILES) > $@

$(CODEX_SKILLS_STAMP): $(CLAUDE_SKILL_FILES) $(CLAUDE_SKILL_EXTRA_FILES) $(CLAUDE_AGENT_FILES) codex.mk
	@set -e; \
	rm -rf .codex/skills; \
	mkdir -p .codex/skills; \
	for src_dir in .claude/skills/*; do \
		[ -d "$$src_dir" ] || continue; \
		skill_name="$$(basename "$$src_dir")"; \
		case "$$skill_name" in codex-*) continue ;; esac; \
		src_skill="$$src_dir/SKILL.md"; \
		dst_dir=".codex/skills/$$skill_name"; \
		mkdir -p "$$dst_dir"; \
		find "$$src_dir" -mindepth 1 -maxdepth 1 -type d -exec cp -a {} "$$dst_dir"/ \;; \
		find "$$src_dir" -mindepth 1 -maxdepth 1 -type f ! -name 'SKILL.md' ! -name '*~' -exec cp -f {} "$$dst_dir"/ \;; \
		description="$$(awk 'BEGIN{front=0} NR==1 && $$0=="---"{front=1; next} front && $$0=="---"{exit} front && $$1=="description:"{sub(/^[^:]+:[ ]*/, "", $$0); print; exit}' "$$src_skill")"; \
		{ \
			printf -- '---\n'; \
			printf 'name: %s\n' "$$skill_name"; \
			printf 'description: %s\n' "$$description"; \
			printf -- '---\n\n'; \
			printf '# Claude Compatibility Skill\n\n'; \
			printf 'This skill was generated from `%s` so Codex can reuse the same workflow.\n\n' "$$src_skill"; \
			awk 'BEGIN{front=0} NR==1 && $$0=="---"{front=1; next} front && $$0=="---"{front=0; next} !front{print}' "$$src_skill" | \
				sed 's|\$${CLAUDE_SKILL_DIR}/|./|g'; \
		} > "$$dst_dir/SKILL.md"; \
	done; \
	for src in $(CLAUDE_AGENT_FILES); do \
		[ -f "$$src" ] || continue; \
		agent_name="$$(awk 'BEGIN{front=0} NR==1 && $$0=="---"{front=1; next} front && $$0=="---"{exit} front && $$1=="name:"{sub(/^[^:]+:[ ]*/, "", $$0); print; exit}' "$$src")"; \
		if [ -z "$$agent_name" ]; then \
			agent_name="$$(basename "$$src" .md)"; \
		fi; \
		original_description="$$(awk 'BEGIN{front=0} NR==1 && $$0=="---"{front=1; next} front && $$0=="---"{exit} front && $$1=="description:"{sub(/^[^:]+:[ ]*/, "", $$0); print; exit}' "$$src")"; \
		case "$$agent_name" in \
			defensive-planner) compat_description='Use when reviewing an implementation plan defensively, with focus on regression risk, blast radius, and safe rollout strategy.' ;; \
			implementation-safety-checker) compat_description='Use when reviewing completed implementation for regressions, requirement gaps, and release safety before considering the work done.' ;; \
			optimistic-evaluator) compat_description='Use when you need an optimistic confidence assessment for a technical answer or implementation decision.' ;; \
			pessimistic-evaluator) compat_description='Use when you need a cautious confidence assessment that emphasizes uncertainty, environment differences, and failure modes.' ;; \
			*) compat_description="$$original_description" ;; \
		esac; \
		dst_dir=".codex/skills/$$agent_name"; \
		mkdir -p "$$dst_dir"; \
		{ \
			printf -- '---\n'; \
			printf 'name: %s\n' "$$agent_name"; \
			printf 'description: %s\n' "$$compat_description"; \
			printf -- '---\n\n'; \
			printf '# Claude Compatibility Skill\n\n'; \
			printf 'This skill was generated from `%s` so Codex can reuse the same role definition.\n\n' "$$src"; \
			awk 'BEGIN{front=0} NR==1 && $$0=="---"{front=1; next} front && $$0=="---"{front=0; next} !front{print}' "$$src"; \
		} > "$$dst_dir/SKILL.md"; \
	done; \
	touch $@

codex/sync: codex/generate $(CODEX_HOME_ROOT_FILES)
	@set -e; \
	sync_tree() { \
		src="$$1"; \
		dst="$$2"; \
		files_equal() { \
			[ -f "$$2" ] || return 1; \
			[ "$$(cksum < "$$1")" = "$$(cksum < "$$2")" ]; \
		}; \
		mkdir -p "$$dst"; \
		find "$$src" -type d | while read -r dir; do \
			rel="$${dir#$$src/}"; \
			if [ "$$dir" = "$$src" ]; then \
				mkdir -p "$$dst"; \
			else \
				mkdir -p "$$dst/$$rel"; \
			fi; \
		done; \
		find "$$dst" -type f | while read -r dst_file; do \
			rel="$${dst_file#$$dst/}"; \
			src_file="$$src/$$rel"; \
			if [ ! -f "$$src_file" ]; then \
				rm -f "$$dst_file"; \
			fi; \
		done; \
		find "$$src" -type f | while read -r src_file; do \
			rel="$${src_file#$$src/}"; \
			dst_file="$$dst/$$rel"; \
			if ! files_equal "$$src_file" "$$dst_file"; then \
				cp -f "$$src_file" "$$dst_file"; \
			fi; \
		done; \
		find "$$dst" -depth -type d -empty -delete; \
	}; \
	for dir in $(CODEX_SYNC_DIRS); do \
		sync_tree ".codex/$$dir" "$(HOME)/.codex/$$dir"; \
	done

$(HOME)/.codex/%: .codex/%
	@mkdir -p $(dir $@)
	@if [ ! -f $@ ] || [ "$$(cksum < $<)" != "$$(cksum < $@)" ]; then \
		cp -f $< $@; \
		echo "Copied $< -> $@"; \
	fi

codex/clean:
	rm -rf .codex .codex.generated
