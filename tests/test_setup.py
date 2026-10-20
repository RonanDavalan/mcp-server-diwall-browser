"""L'assistant de premier lancement, sur un poste simulé : tables de montage, gestionnaire de paquets, terminal et programmes."""

from __future__ import annotations

import io
import json
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from conftest import ROOT_LINE, mountinfo_line

from mcp_server_diwall_browser import mount as mount_module
from mcp_server_diwall_browser import operator_profile, setup
from mcp_server_diwall_browser.mount import PROJECT_DIR_NAME, MountChecker, MountState

SESSION_DIR = "mcp-server-diwall-browser-session"
MOUNT_DEV, OTHER_DEV = 200, 300
PASSWORD_OPTIONS = ("-passfile", "-extpass", "-p", "-password")


@dataclass
class Poste:
    """Un poste simulé : ce que la commande lit de lui et les programmes qu'il lance, rejoués par des fonctions."""

    tmp: Path
    home: Path = field(init=False)
    runtime: Path = field(init=False)
    mountinfo: Path = field(init=False)
    swaps: Path = field(init=False)
    fuse: Path = field(init=False)
    terminal: bool = True
    programs: dict[str, str] = field(default_factory=dict)
    euid: int = field(default_factory=os.geteuid)
    mounts: dict[str, str] = field(default_factory=dict)
    other_lines: list[str] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    spawned: list[list[str]] = field(default_factory=list)
    listening: set[int] = field(default_factory=set)
    browsers: dict[int, list[str]] = field(default_factory=dict)
    refuse_mount: bool = False
    busy: bool = False
    browser_stays: bool = False
    runtime_type: str = "tmpfs"

    def __post_init__(self) -> None:
        self.home = self.tmp / "home"
        self.runtime = self.tmp / "run"
        self.mountinfo = self.tmp / "mountinfo"
        self.swaps = self.tmp / "swaps"
        self.fuse = self.tmp / "fuse"
        self.home.mkdir()
        self.runtime.mkdir(mode=0o700)
        self.fuse.write_text("")
        self.swaps.write_text("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
        self.programs = {name: f"/usr/bin/{name}" for name in ("gocryptfs", "fusermount3", "apt-get", "google-chrome", "zenity")}
        self.write_mountinfo()

    # --- ce que la commande lit ------------------------------------------------------------------

    def write_mountinfo(self) -> None:
        lines = [ROOT_LINE, f"30 22 0:40 / {os.path.realpath(self.runtime)} rw - {self.runtime_type} tmpfs rw"]
        lines += [mountinfo_line(point, kind) for point, kind in self.mounts.items()]
        lines += self.other_lines
        self.mountinfo.write_text("\n".join(lines) + "\n")

    def stat(self, path: str) -> os.stat_result:
        real = os.path.realpath(path)
        fields = list(os.stat(real))
        under = any(real == point or real.startswith(point + os.sep) for point in self.mounts)
        fields[2] = MOUNT_DEV if under else OTHER_DEV
        return os.stat_result(fields)

    def environment(self, **overrides: Any) -> setup.Environment:
        environ = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.tmp / "config"), "XDG_RUNTIME_DIR": str(self.runtime), "DISPLAY": ":0"}
        values = dict(
            environ=environ,
            home=self.home,
            euid=self.euid,
            mountinfo_path=str(self.mountinfo),
            swaps_path=str(self.swaps),
            fuse_path=str(self.fuse),
            which=lambda name: self.programs.get(name),
            is_terminal=lambda: self.terminal,
            is_crypt_device=lambda source: os.path.basename(source).startswith("luks-"),
            run_interactive=self.run_interactive,
            spawn_detached=self.spawn_detached,
            port_listening=lambda port: port in self.listening,
            find_browser_pids=self.find_browser_pids,
            stop_processes=self.stop_processes,
            mount_checker=MountChecker(mountinfo_path=str(self.mountinfo), euid=self.euid, stat_fn=self.stat),
        )
        values.update(overrides)
        return setup.Environment(**values)

    # --- ce que les programmes font ---------------------------------------------------------------

    def run_interactive(self, argv: Sequence[str]) -> int:
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "gocryptfs" and argv[1] == "-init":
            crypt = Path(argv[-1])
            crypt.mkdir(mode=0o700, exist_ok=True)
            (crypt / "gocryptfs.conf").write_text("{}")
            return 0
        if argv[0] == "gocryptfs":
            if self.refuse_mount:
                return 1
            self.mounts[os.path.realpath(argv[-1])] = "fuse.gocryptfs"
            self.write_mountinfo()
            return 0
        if argv[0] == "fusermount3":
            self.events.append("fusermount3")
            if self.busy:
                return 1
            point = os.path.realpath(argv[-1])
            self.mounts.pop(point, None)
            self.write_mountinfo()
            for entry in Path(point).iterdir():
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            return 0
        raise AssertionError(f"programme inattendu : {argv}")

    def spawn_detached(self, argv: Sequence[str]) -> int:
        self.spawned.append(list(argv))
        pid = 4000 + len(self.spawned)
        self.browsers[pid] = list(argv)
        return pid

    def find_browser_pids(self, profile_dir: str) -> list[int]:
        return [pid for pid, argv in self.browsers.items() if f"--user-data-dir={profile_dir}" in argv]

    def stop_processes(self, pids: list[int], timeout_s: float) -> bool:
        self.events.append("stop_browser")
        if self.browser_stays:
            return False
        for pid in pids:
            self.browsers.pop(pid, None)
        return True


@pytest.fixture
def poste(tmp_path: Path) -> Poste:
    return Poste(tmp_path)


def play(poste: Poste, *argv: str, **overrides: Any) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = setup.run(list(argv), poste.environment(**overrides), out, err)
    return code, out.getvalue(), err.getvalue()


def tree(root: Path) -> list[str]:
    return sorted(os.path.relpath(os.path.join(base, name), root) for base, dirs, files in os.walk(root) for name in dirs + files)


def profile_path(poste: Poste) -> Path:
    return poste.tmp / "config" / PROJECT_DIR_NAME / "profile.json"


def load(poste: Poste) -> operator_profile.Profile:
    return operator_profile.load_profile({operator_profile.ENV_VAR: str(profile_path(poste))})


def report_of(poste: Poste, **overrides: Any) -> dict[str, Any]:
    code, out, _err = play(poste, "check", "--json", **overrides)
    assert code == 0
    return json.loads(out)


def mounted_profile(poste: Poste) -> Path:
    """Un profil dont le volume est déjà ouvert, comme après `create`."""
    code, _out, err = play(poste, "create", "--crypt-dir", str(poste.tmp / "Main.enc"), "--mount-dir", str(poste.tmp / "Main"))
    assert code == 0, err
    return poste.tmp / "Main"


# --- check ---------------------------------------------------------------------------------------


def test_check_reads_the_machine_and_writes_nothing(poste: Poste) -> None:
    before = tree(poste.tmp)
    report = report_of(poste)
    assert tree(poste.tmp) == before
    assert poste.calls == [] and poste.spawned == []
    assert report["profile"]["state"] == "absent"
    assert report["volume"] is None
    assert report["fuse"] == {"device_present": True}
    assert report["terminal"] == {"interactive": True}
    assert report["swap"]["present"] is False
    assert report["confirmation_window"] == {"tool": "zenity"}
    assert report["browser"]["installed"] == ["google-chrome"]
    [row] = [row for row in report["mechanisms"] if row["supported_by_server"]]
    assert (row["mechanism"], row["platform"], row["available_here"], row["how_to_obtain"]) == ("gocryptfs", "linux", True, None)
    assert "create --temporary" in report["next_step"]["command"]


def test_check_without_a_terminal_gives_the_whole_report_and_creates_nothing(poste: Poste) -> None:
    poste.terminal = False
    before = tree(poste.tmp)
    code, out, err = play(poste, "check", "--json")
    assert code == 0 and err == ""
    report = json.loads(out)
    assert out.endswith("\n") and out.count("\n") == 1
    assert report["terminal"] == {"interactive": False}
    assert report["mechanisms"] and report["next_step"]["command"]
    assert tree(poste.tmp) == before and poste.calls == []


def test_check_in_text_names_the_next_step_and_the_swap_without_inventing_its_encryption(poste: Poste) -> None:
    poste.swaps.write_text("Filename Type Size Used Priority\n/dev/sda3 partition 8388604 0 -2\n")
    code, out, _err = play(poste, "check")
    assert code == 0
    assert "espace d'échange" in out and "non déterminé" in out
    assert "mcp-server-diwall-browser-setup create --temporary" in out
    assert str(poste.home) not in out and "~/" in out


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        ("apt-get", "sudo apt install gocryptfs fuse3"),
        ("dnf", "sudo dnf install gocryptfs fuse3"),
        ("zypper", "sudo zypper install gocryptfs fuse3"),
        ("pacman", "sudo pacman -S gocryptfs fuse3"),
    ],
)
def test_check_gives_the_install_command_of_the_package_manager_it_finds(poste: Poste, manager: str, expected: str) -> None:
    poste.programs = {manager: f"/usr/bin/{manager}", "fusermount3": "/usr/bin/fusermount3"}
    row = report_of(poste)["mechanisms"][0]
    assert row["available_here"] is False
    assert row["how_to_obtain"] == expected
    assert report_of(poste)["package_manager"] == {"name": manager.replace("-get", "")}


def test_check_gives_no_sudo_to_root_and_names_the_project_when_no_manager_is_known(poste: Poste) -> None:
    poste.programs = {"apt-get": "/usr/bin/apt-get"}
    poste.euid = 0
    assert report_of(poste)["mechanisms"][0]["how_to_obtain"] == "apt install gocryptfs fuse3"
    poste.programs = {}
    row = report_of(poste)["mechanisms"][0]
    assert row["available_here"] is False
    assert "gocryptfs fuse3" in row["how_to_obtain"] and "guide" in row["how_to_obtain"]
    assert report_of(poste)["package_manager"] == {"name": None}


def test_a_post_without_gocryptfs_is_told_what_to_install_and_never_what_to_skip(poste: Poste) -> None:
    del poste.programs["gocryptfs"]
    report = report_of(poste)
    assert report["next_step"]["command"] == "sudo apt install gocryptfs fuse3"
    text = play(poste, "check")[1]
    assert "sudo apt install gocryptfs fuse3" in text
    assert "sans chiffrement" not in text and "en clair" not in text.replace("jamais en clair", "")


def test_a_post_without_dev_fuse_is_not_refused_dry(poste: Poste) -> None:
    poste.fuse.unlink()
    report = report_of(poste)
    assert report["fuse"] == {"device_present": False}
    assert report["mechanisms"][0]["available_here"] is False
    step = report["next_step"]
    assert "fuse" in step["why"].lower() and step["command"]
    assert "modprobe fuse" in step["command"] or "--device /dev/fuse" in step["why"]


def test_check_lists_the_closed_volumes_it_finds_and_the_ones_the_server_does_not_support(poste: Poste) -> None:
    crypt = poste.home / "Vaults" / "Old.enc"
    crypt.mkdir(parents=True)
    (crypt / "gocryptfs.conf").write_text("{}")
    poste.other_lines = [
        f"31 22 0:50 / {poste.home}/safe rw - ext4 /dev/mapper/luks-1234 rw",
        f"32 22 0:51 / {poste.home}/enc rw - ecryptfs {poste.home}/.private rw",
    ]
    poste.write_mountinfo()
    report = report_of(poste)
    assert report["volumes"]["closed"] == ["~/Vaults/Old.enc"]
    kinds = {row["mount_point"]: row["type"] for row in report["volumes"]["unsupported"]}
    assert kinds == {"~/safe": "device-mapper", "~/enc": "ecryptfs"}
    rows = {row["mechanism"]: row for row in report["mechanisms"]}
    assert rows["device-mapper (LUKS)"]["supported_by_server"] is False
    assert "gocryptfs" in rows["device-mapper (LUKS)"]["how_to_obtain"]
    assert "~/Vaults/Old.enc" in report["next_step"]["command"] and "--crypt-dir" in report["next_step"]["command"]


def test_a_declared_luks_volume_is_not_gocryptfs_never_mounted(poste: Poste) -> None:
    safe = poste.tmp / "safe"
    safe.mkdir()
    poste.other_lines = [f"31 22 0:50 / {safe} rw - ext4 /dev/mapper/luks-1234 rw"]
    poste.write_mountinfo()
    profile_path(poste).parent.mkdir(parents=True, mode=0o700)
    profile_path(poste).write_text(json.dumps({
        "schema_version": 1, "secrets_dir": str(safe), "allowed_origins": [],
        "browser": {"family": "chrome", "profile_dir": str(safe / "browser-profile")},
    }))
    os.chmod(profile_path(poste), 0o600)
    report = report_of(poste)
    assert report["profile"]["state"] == "valid"
    assert report["volume"]["state"] == "not_gocryptfs"
    assert "gocryptfs" in report["next_step"]["why"] and "LUKS" in report["next_step"]["why"]
    assert play(poste, "browser")[0] == 1 and poste.spawned == []


def test_check_with_a_fake_mountinfo_says_mounted_closed_and_unreadable(poste: Poste) -> None:
    mount_dir = mounted_profile(poste)
    assert report_of(poste)["volume"]["state"] == "mounted"
    assert "browser" in report_of(poste)["next_step"]["command"]
    poste.mounts.clear()
    poste.write_mountinfo()
    closed = report_of(poste)
    assert closed["volume"]["state"] == "closed" and closed["next_step"]["command"].endswith("setup open")
    poste.mountinfo.write_text("garbage\n")
    assert report_of(poste)["volume"]["state"] == "unreadable"
    assert mount_dir.exists()


def test_check_with_a_profile_that_cannot_be_read_says_why_and_shows_no_value(poste: Poste) -> None:
    profile_path(poste).parent.mkdir(parents=True, mode=0o700)
    profile_path(poste).write_text('{"schema_version": 1, "secrets_dir": "relative"}')
    os.chmod(profile_path(poste), 0o600)
    report = report_of(poste)
    assert report["profile"]["state"] == "invalid" and report["profile"]["reason"]
    assert "relative" not in json.dumps(report)


# --- create, open, browser, close sans terminal --------------------------------------------------


@pytest.mark.parametrize("subcommand", [["create"], ["create", "--temporary"], ["open"], ["browser"], ["close"]])
def test_the_commands_that_act_refuse_without_a_terminal_and_say_why(poste: Poste, subcommand: list[str]) -> None:
    poste.terminal = False
    before = tree(poste.tmp)
    code, out, err = play(poste, *subcommand)
    assert code == 1 and out == ""
    assert "no_terminal" in err and "mot de passe" in err
    assert tree(poste.tmp) == before and poste.calls == [] and poste.spawned == []


# --- create --------------------------------------------------------------------------------------


def test_create_makes_the_volume_then_the_profile_that_the_server_reads(poste: Poste) -> None:
    crypt, mount_dir = poste.tmp / "Main.enc", poste.tmp / "Main"
    code, out, err = play(poste, "create", "--crypt-dir", str(crypt), "--mount-dir", str(mount_dir))
    assert code == 0, err
    assert poste.calls == [["gocryptfs", "-init", str(crypt)], ["gocryptfs", str(crypt), str(mount_dir)]]
    assert "clé maître" in out and out.index("clé maître") < out.index("mcp-server-diwall-browser-setup browser")
    project = mount_dir / PROJECT_DIR_NAME
    for directory in (project, project / "credentials", project / "browser-profile"):
        assert directory.is_dir() and (directory.stat().st_mode & 0o777) == 0o700, directory
    profile = load(poste)
    assert (profile.secrets_dir, profile.secrets_crypt_dir) == (mount_dir, crypt)
    assert profile.allowed_origins == () and profile.confirm_each_change == () and profile.local_model_origins == ()
    assert profile.browser.family == "chrome" and profile.browser.debug_port == 9222
    assert Path(profile.browser.profile_dir) == project / "browser-profile" / "chrome"
    assert (profile_path(poste).stat().st_mode & 0o777) == 0o600 and (profile_path(poste).parent.stat().st_mode & 0o777) == 0o700
    state = poste.environment().mount_checker.check(profile.secrets_dir, profile.secrets_crypt_dir)
    assert (state.state, state.error_code) == (MountState.MOUNTED, None)


def test_create_never_gives_the_password_a_way_into_the_command_line_or_the_output(poste: Poste) -> None:
    code, out, err = play(poste, "create", "--crypt-dir", str(poste.tmp / "Main.enc"), "--mount-dir", str(poste.tmp / "Main"))
    assert code == 0
    for argv in poste.calls:
        assert not any(option in argv for option in PASSWORD_OPTIONS)
    assert "mot de passe" in out
    source = Path(setup.__file__).read_text()
    assert "getpass" not in source and "input(" not in source and "sys.stdin" not in source and "readline" not in source


def test_create_writes_the_profile_only_after_it_has_seen_the_volume_mounted(poste: Poste) -> None:
    poste.refuse_mount = True
    code, _out, err = play(poste, "create", "--crypt-dir", str(poste.tmp / "Main.enc"), "--mount-dir", str(poste.tmp / "Main"))
    assert code == 1 and "mount_failed" in err
    assert not profile_path(poste).exists()
    assert not (poste.tmp / "Main" / PROJECT_DIR_NAME).exists()


def test_create_refuses_to_replace_the_profile_of_a_usable_volume(poste: Poste) -> None:
    mounted_profile(poste)
    before = profile_path(poste).read_text()
    calls = len(poste.calls)
    code, _out, err = play(poste, "create", "--crypt-dir", str(poste.tmp / "Other.enc"), "--mount-dir", str(poste.tmp / "Other"))
    assert code == 1 and "profile_exists" in err
    assert profile_path(poste).read_text() == before and len(poste.calls) == calls
    assert not (poste.tmp / "Other.enc").exists()


def test_create_adopts_a_volume_that_already_exists_without_initialising_it(poste: Poste) -> None:
    crypt = poste.tmp / "Diwall.enc"
    crypt.mkdir()
    (crypt / "gocryptfs.conf").write_text("{}")
    code, out, err = play(poste, "create", "--crypt-dir", str(crypt), "--mount-dir", str(poste.tmp / "Diwall"))
    assert code == 0, err
    assert poste.calls == [["gocryptfs", str(crypt), str(poste.tmp / "Diwall")]]
    assert "clé maître" not in out
    assert load(poste).secrets_crypt_dir == crypt


def test_create_refuses_a_mount_point_that_is_not_empty_and_a_relative_path(poste: Poste) -> None:
    occupied = poste.tmp / "Main"
    occupied.mkdir()
    (occupied / "stray").write_text("x")
    code, _out, err = play(poste, "create", "--crypt-dir", str(poste.tmp / "Main.enc"), "--mount-dir", str(occupied))
    assert code == 1 and "mount_point_not_empty" in err and poste.calls == []
    code, _out, err = play(poste, "create", "--crypt-dir", "Main.enc", "--mount-dir", str(poste.tmp / "M"))
    assert code == 1 and "path_not_absolute" in err and poste.calls == []


def test_create_without_gocryptfs_refuses_with_the_install_command_and_creates_nothing(poste: Poste) -> None:
    del poste.programs["gocryptfs"]
    before = tree(poste.tmp)
    code, _out, err = play(poste, "create")
    assert code == 1 and "mechanism_missing" in err and "sudo apt install gocryptfs fuse3" in err
    assert tree(poste.tmp) == before and poste.calls == []


def test_create_picks_the_browser_it_finds_and_the_default_paths_are_under_the_home(poste: Poste) -> None:
    poste.programs["brave-browser"] = "/usr/bin/brave-browser"
    del poste.programs["google-chrome"]
    code, _out, err = play(poste, "create")
    assert code == 0, err
    profile = load(poste)
    assert profile.secrets_dir == poste.home / "Vaults" / "Main" and profile.secrets_crypt_dir == poste.home / "Vaults" / "Main.enc"
    assert profile.browser.family == "brave"
    assert (poste.home / "Vaults").stat().st_mode & 0o777 == 0o700


# --- la séance temporaire ------------------------------------------------------------------------


def test_the_temporary_session_lives_in_memory_and_says_what_it_costs_before_it_creates(poste: Poste) -> None:
    poste.swaps.write_text("Filename Type Size Used Priority\n/dev/zram0 partition 100 0 100\n")
    code, out, err = play(poste, "create", "--temporary")
    assert code == 0, err
    session = poste.runtime / SESSION_DIR
    assert poste.calls == [["gocryptfs", "-init", "-q", str(session / "crypt")], ["gocryptfs", str(session / "crypt"), str(session / "mount")]]
    cost = out[: out.index("mcp-server-diwall-browser-setup browser")]
    for words in ("sessions", "journal", "preuves", "perdu", "espace d'échange", "non déterminé", "create`"):
        assert words in cost, words
    profile = load(poste)
    assert profile.secrets_dir == session / "mount" and profile.secrets_crypt_dir == session / "crypt"
    assert Path(profile.browser.profile_dir) == session / "mount" / PROJECT_DIR_NAME / "browser-profile" / "chrome"
    assert os.path.dirname(os.fspath(profile.secrets_dir)).startswith(str(poste.runtime))


def test_the_temporary_session_refuses_a_runtime_directory_that_is_not_in_memory(poste: Poste) -> None:
    poste.runtime_type = "ext4"
    poste.write_mountinfo()
    before = tree(poste.tmp)
    code, _out, err = play(poste, "create", "--temporary")
    assert code == 1 and "runtime_dir_not_memory" in err
    assert tree(poste.tmp) == before and poste.calls == []
    poste.runtime_type = "tmpfs"
    poste.write_mountinfo()
    code, _out, err = play(poste, "create", "--temporary", environ={"HOME": str(poste.home)})
    assert code == 1 and "no_runtime_dir" in err


def test_closing_a_temporary_session_leaves_no_volume_and_the_next_create_keeps_the_sites(poste: Poste) -> None:
    assert play(poste, "create", "--temporary")[0] == 0
    document = json.loads(profile_path(poste).read_text())
    document["allowed_origins"] = ["https://example.invalid"]
    profile_path(poste).write_text(json.dumps(document))
    assert play(poste, "close")[0] == 0
    assert not (poste.runtime / SESSION_DIR).exists() and os.listdir(poste.runtime) == []
    assert poste.calls[-1][:2] == ["fusermount3", "-u"]
    assert report_of(poste)["volume"]["state"] == "never_initialized"
    code, _out, err = play(poste, "create", "--crypt-dir", str(poste.tmp / "Main.enc"), "--mount-dir", str(poste.tmp / "Main"))
    assert code == 0, err
    profile = load(poste)
    assert [entry.canonical for entry in profile.allowed_origins] == ["https://example.invalid"]
    assert profile.secrets_dir == poste.tmp / "Main"


# --- open ----------------------------------------------------------------------------------------


def test_open_hands_the_terminal_to_gocryptfs_and_checks_the_mount(poste: Poste) -> None:
    mount_dir = mounted_profile(poste)
    assert play(poste, "close")[0] == 0
    poste.calls.clear()
    code, out, err = play(poste, "open")
    assert code == 0, err
    assert poste.calls == [["gocryptfs", str(poste.tmp / "Main.enc"), str(mount_dir)]]
    assert play(poste, "open")[0] == 0 and len(poste.calls) == 1
    assert "déjà ouvert" in play(poste, "open")[1]


def test_open_refuses_a_failed_mount_and_a_profile_without_a_volume(poste: Poste) -> None:
    code, _out, err = play(poste, "open")
    assert code == 1 and "profile_absent" in err and "create" in err
    mounted_profile(poste)
    play(poste, "close")
    poste.refuse_mount = True
    code, _out, err = play(poste, "open")
    assert code == 1 and "mount_failed" in err


# --- browser -------------------------------------------------------------------------------------


def test_the_browser_starts_detached_on_the_profile_of_the_volume(poste: Poste) -> None:
    mounted_profile(poste)
    code, out, err = play(poste, "browser")
    assert code == 0, err
    profile = load(poste)
    assert poste.spawned == [["/usr/bin/google-chrome", f"--user-data-dir={profile.browser.profile_dir}", "--remote-debugging-port=9222"]]
    assert "remote-debugging-address" not in " ".join(poste.spawned[0])
    assert "Navigateur lancé" in out


def test_the_browser_is_never_started_outside_the_mounted_state(poste: Poste) -> None:
    code, _out, err = play(poste, "browser")
    assert code == 1 and "profile_absent" in err
    mounted_profile(poste)
    play(poste, "close")
    code, _out, err = play(poste, "browser")
    assert code == 1 and "volume_not_mounted" in err and "setup open" in err
    assert poste.spawned == []
    poste.mountinfo.write_text("garbage\n")
    assert play(poste, "browser")[0] == 1 and poste.spawned == []


def test_the_browser_that_already_answers_is_not_started_twice(poste: Poste) -> None:
    mounted_profile(poste)
    poste.listening.add(9222)
    code, out, _err = play(poste, "browser")
    assert code == 0 and "déjà" in out and poste.spawned == []


def test_the_browser_needs_a_browser_and_a_screen(poste: Poste) -> None:
    mounted_profile(poste)
    del poste.programs["google-chrome"]
    code, _out, err = play(poste, "browser")
    assert code == 1 and "browser_missing" in err and poste.spawned == []
    poste.programs["google-chrome"] = "/usr/bin/google-chrome"
    code, _out, err = play(poste, "browser", environ={"HOME": str(poste.home), "XDG_CONFIG_HOME": str(poste.tmp / "config")})
    assert code == 1 and "no_display" in err and poste.spawned == []


# --- close ---------------------------------------------------------------------------------------


def test_close_stops_the_browser_before_it_unmounts_and_never_unmounts_lazily(poste: Poste) -> None:
    mount_dir = mounted_profile(poste)
    play(poste, "browser")
    assert poste.browsers
    code, out, err = play(poste, "close")
    assert code == 0, err
    assert poste.events == ["stop_browser", "fusermount3"]
    assert poste.calls[-1] == ["fusermount3", "-u", str(mount_dir)]
    assert not poste.browsers
    assert report_of(poste)["volume"]["state"] == "closed"
    source = Path(setup.__file__).read_text()
    assert '"-z"' not in source and "'-z'" not in source and '"-uz"' not in source


def test_close_does_not_unmount_while_the_browser_is_still_there(poste: Poste) -> None:
    mounted_profile(poste)
    play(poste, "browser")
    poste.browser_stays = True
    code, _out, err = play(poste, "close")
    assert code == 1 and "browser_still_running" in err
    assert poste.events == ["stop_browser"] and report_of(poste)["volume"]["state"] == "mounted"


def test_close_says_what_to_do_when_the_volume_is_busy(poste: Poste) -> None:
    mounted_profile(poste)
    poste.busy = True
    code, _out, err = play(poste, "close")
    assert code == 1 and "volume_busy" in err
    assert report_of(poste)["volume"]["state"] == "mounted"
    assert all("-z" not in argv for argv in poste.calls)


def test_close_of_a_volume_that_is_not_open_says_so(poste: Poste) -> None:
    mounted_profile(poste)
    play(poste, "close")
    code, out, _err = play(poste, "close")
    assert code == 0 and "déjà fermé" in out and poste.calls[-1][0] == "fusermount3" and poste.calls.count(poste.calls[-1]) == 1


# --- ce que la commande ne récrit pas ----------------------------------------------------------


def test_the_command_imports_the_mount_detection_and_does_not_rewrite_it() -> None:
    source = Path(setup.__file__).read_text()
    assert "from .mount import" in source
    assert "fuse.gocryptfs" not in source and "def parse_mountinfo" not in source and "class MountChecker" not in source and "def check(" not in source
    assert setup.MountChecker is mount_module.MountChecker


def test_the_default_environment_is_the_real_one() -> None:
    env = setup.production_environment()
    assert env.mountinfo_path == "/proc/self/mountinfo" and env.fuse_path == "/dev/fuse" and env.swaps_path == "/proc/swaps"
    assert isinstance(env.mount_checker, MountChecker)


def test_an_unknown_subcommand_or_option_is_a_usage_error_that_repeats_nothing(poste: Poste) -> None:
    for argv in (["frobnicate"], ["check", "--password=secret-value"], ["create", "--temporary", "--crypt-dir", "/x"]):
        code, out, err = play(poste, *argv)
        assert code == 2 and out == "" and "secret-value" not in err
        assert "usage" in err


def test_the_bare_command_is_the_read_only_report(poste: Poste) -> None:
    before = tree(poste.tmp)
    code, out, err = play(poste)
    assert code == 0 and err == ""
    assert out == play(poste, "check")[1]
    assert "Prochaine étape" in out and tree(poste.tmp) == before and poste.calls == []


def test_main_is_declared_as_a_console_script() -> None:
    text = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    assert 'mcp-server-diwall-browser-setup = "mcp_server_diwall_browser.setup:main"' in text
    assert callable(setup.main)
