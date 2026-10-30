"""mech score is the mean of the corpus and the product halves, so the
product stays in view: a product that falls moves the score."""
from mech import cli, corpus


def _score(monkeypatch, capsys, product, argv=()):
    monkeypatch.setattr(corpus, "run", lambda: {"caught": 9, "faults": 10, "false_alarms": 1})
    monkeypatch.setattr(cli, "product_score", lambda: product)
    assert cli.main(["score", *argv]) == 0
    return capsys.readouterr().out.splitlines()


def test_the_score_is_the_mean_of_corpus_and_product(monkeypatch, capsys):
    out = _score(monkeypatch, capsys, 60.0)
    assert out[-3:] == ["corpus: 80.00", "product: 60.00", "70.00"]


def test_a_falling_product_lowers_the_score(monkeypatch, capsys):
    assert float(_score(monkeypatch, capsys, 100.0)[-1]) > float(_score(monkeypatch, capsys, 50.0)[-1])


def test_no_pin_counts_the_product_as_zero(monkeypatch, capsys):
    assert _score(monkeypatch, capsys, None)[-1] == "40.00"


def test_corpus_alone(monkeypatch, capsys):
    assert _score(monkeypatch, capsys, 0.0, ["--corpus"])[-1] == "80.00"
