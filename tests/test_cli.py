import pytest

from jobwatch.cli import build_parser, gh_annotate, main


def test_parser_accepts_repeated_source_filter():
    args = build_parser().parse_args(["run", "-s", "apple-pay", "-s", "alibaba-aidc"])
    assert args.source == ["apple-pay", "alibaba-aidc"]
    assert args.require_mail is False


def test_run_flags_default_to_safe_values():
    args = build_parser().parse_args(["run"])
    assert (args.dry_run, args.force, args.require_mail, args.source) == (False, False, False, None)


def test_annotation_is_silent_outside_github_actions(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    gh_annotate("error", "标题", "内容")
    assert capsys.readouterr().out == ""


def test_annotation_escapes_newlines_for_github(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    gh_annotate("error", "配置缺失", "第一行\n第二行\n100% 确定")

    out = capsys.readouterr().out.strip()
    assert out.startswith("::error title=配置缺失::")
    # 换行和百分号必须转义，否则注解会被截断或解析错
    assert "\n" not in out
    assert "%0A" in out
    assert "100%25" in out


def test_unknown_command_exits_with_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        main(["nope"])
    assert excinfo.value.code == 2


def test_missing_watchlist_is_reported_not_crashed(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("WATCHLIST", str(tmp_path / "missing.json"))
    monkeypatch.setattr("jobwatch.config.load_dotenv", lambda path=None: None)

    assert main(["status"]) == 1
    assert "找不到监测清单" in caplog.text
