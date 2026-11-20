from tcode_agent.server.workdirs import create_user_workdir, list_user_workdirs


def test_can_create_more_than_five_workspaces(tmp_path, monkeypatch):
    monkeypatch.setenv("TCODE_MAX_WORKSPACES_PER_USER", "1")
    root = str(tmp_path)
    for index in range(7):
        create_user_workdir(root, f"project-{index}")
    assert {row["name"] for row in list_user_workdirs(root)} >= {
        f"project-{index}" for index in range(7)
    }
