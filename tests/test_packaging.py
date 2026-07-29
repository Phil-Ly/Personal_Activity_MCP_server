import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_pypi_metadata_describes_the_supported_distribution() -> None:
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert project["name"] == "pamcp"
    assert project["scripts"] == {"pamcp": "personal_activity_mcp.server:main"}
    assert project["readme"] == "README.md"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": "Phil-Ly"}]
    assert project["urls"] == {
        "Homepage": "https://github.com/Phil-Ly/Personal_Activity_MCP_server",
        "Repository": "https://github.com/Phil-Ly/Personal_Activity_MCP_server",
        "Issues": "https://github.com/Phil-Ly/Personal_Activity_MCP_server/issues",
    }
    assert "Operating System :: MacOS :: MacOS X" in project["classifiers"]


def test_runtime_dependencies_do_not_install_the_mcp_cli_extra() -> None:
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["dependencies"] == [
        "mcp>=1.27,<2",
        "pydantic>=2.11,<3",
    ]


def test_readme_documents_safe_manual_configuration() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "~/.config/pamcp/config.toml" in readme
    assert 'calendar_id = "Your Calendar Name"' in readme
    assert 'list_id = "Your Reminder List Name"' in readme
    assert "allow_write = false" in readme
    assert "/Users/" not in readme
