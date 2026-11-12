"""operator-edge берёт адреса из whitelist.yaml (12.11.2026, аудит).

Воркфлоу падал каждое воскресенье с 13.09 — девять раз подряд: «адреса не
заданы (HL_ACCOUNTS)». Секрета в репозитории нет, хотя комментарий в YAML
утверждал обратное. Все остальные прогоны читают адреса из whitelist.yaml;
этот единственный ждал переменную окружения.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "operator_edge_run", ROOT / "scripts" / "operator_edge_run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_falls_back_to_whitelist_accounts(monkeypatch, tmp_path):
    monkeypatch.delenv("HL_ACCOUNTS", raising=False)
    monkeypatch.delenv("HL_ADDRESS", raising=False)
    wl = tmp_path / "whitelist.yaml"
    wl.write_text('accounts:\n  - address: "0xabc"\n    label: main\n'
                  '  - address: "0xdef"\n    label: second\n')
    assert _mod()._addresses(wl) == ["0xabc", "0xdef"]


def test_env_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("HL_ACCOUNTS", "0x1, 0x2")
    wl = tmp_path / "whitelist.yaml"
    wl.write_text('accounts:\n  - address: "0xabc"\n')
    assert _mod()._addresses(wl) == ["0x1", "0x2"]


def test_real_whitelist_has_accounts(monkeypatch):
    monkeypatch.delenv("HL_ACCOUNTS", raising=False)
    monkeypatch.delenv("HL_ADDRESS", raising=False)
    assert _mod()._addresses()
