"""Tests for subagent availability and prompt exposure under local bash hardening."""

from deerflow.agents.lead_agent import prompt as prompt_module
from deerflow.subagents import registry as registry_module


def test_get_available_subagent_names_hides_bash_when_host_bash_disabled(monkeypatch) -> None:
    monkeypatch.setattr(registry_module, "is_host_bash_allowed", lambda: False)

    names = registry_module.get_available_subagent_names()

    assert names == ["general-purpose"]


def test_get_available_subagent_names_keeps_bash_when_allowed(monkeypatch) -> None:
    monkeypatch.setattr(registry_module, "is_host_bash_allowed", lambda: True)

    names = registry_module.get_available_subagent_names()

    assert names == ["general-purpose", "bash"]


def test_build_subagent_section_hides_bash_examples_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(prompt_module, "get_available_subagent_names", lambda: ["general-purpose"])

    section = prompt_module._build_subagent_section(3)

    # When bash is not available, it should not appear at all (aligned with Codex:
    # unavailable roles are omitted, not listed as disabled)
    assert "**bash**" not in section
    assert 'bash("npm test")' not in section
    assert 'read_file("/mnt/user-data/workspace/README.md")' in section
    assert "available tools (ls, read_file, web_search, etc.)" in section


def test_build_subagent_section_includes_bash_when_available(monkeypatch) -> None:
    monkeypatch.setattr(prompt_module, "get_available_subagent_names", lambda: ["general-purpose", "bash"])

    section = prompt_module._build_subagent_section(3)

    assert "For command execution (git, build, test, deploy operations)" in section
    assert 'bash("npm test")' in section
    assert "available tools (bash, ls, read_file, web_search, etc.)" in section


def test_build_subagent_section_contains_codex_style_dispatch_rules(monkeypatch) -> None:
    monkeypatch.setattr(prompt_module, "get_available_subagent_names", lambda: ["general-purpose"])

    section = prompt_module._build_subagent_section(3)

    assert "2+ independent, parallelizable sub-tasks" in section
    assert "Use direct tools for simple, single-step work" in section
    assert "self-contained" in section
    assert "objective, files or paths to inspect, constraints, and expected output" in section
    assert "Do not wrap a single operation" in section
    assert "at most 3 `task` calls" in section


def test_bash_subagent_prompt_mentions_workspace_relative_paths() -> None:
    from deerflow.subagents.builtins.bash_agent import BASH_AGENT_CONFIG

    assert "Treat `/mnt/user-data/workspace` as the default working directory for file IO" in BASH_AGENT_CONFIG.system_prompt
    assert "`hello.txt`, `../uploads/input.csv`, and `../outputs/result.md`" in BASH_AGENT_CONFIG.system_prompt


def test_general_purpose_subagent_prompt_mentions_workspace_relative_paths() -> None:
    from deerflow.subagents.builtins.general_purpose import GENERAL_PURPOSE_CONFIG

    assert "Treat `/mnt/user-data/workspace` as the default working directory for coding and file IO" in GENERAL_PURPOSE_CONFIG.system_prompt
    assert "`hello.txt`, `../uploads/input.csv`, and `../outputs/result.md`" in GENERAL_PURPOSE_CONFIG.system_prompt


def test_general_purpose_subagent_prompt_contains_large_file_editing_guidance() -> None:
    from deerflow.subagents.builtins.general_purpose import GENERAL_PURPOSE_CONFIG

    prompt = GENERAL_PURPOSE_CONFIG.system_prompt

    assert "HTML" in prompt
    assert "large files" in prompt
    assert "Default to `apply_patch`" in prompt
    assert "Prefer `apply_patch` over `write_file`" in prompt
    assert "`rg`" in prompt
    assert "`rg --files`" in prompt
    assert "append=True" in prompt
    assert "Do not overwrite or revert user changes" in prompt
    assert "final response" in prompt
    assert "Do not paste full HTML" in prompt
