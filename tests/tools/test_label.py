import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tools import label as lb


def _r(turn_id: str, rating: str, pair: str = "hi-kn", entities: str = "") -> lb.Rating:
    return lb.Rating(turn_id=turn_id, pair=pair, side="a", rating=rating, entities=entities)


def _turns_file(tmp_path: Path, turns) -> Path:
    p = tmp_path / "turns.json"
    p.write_text(json.dumps(turns, ensure_ascii=False))
    return p


# --- summary math ---------------------------------------------------------------------


def test_percentages_per_pair():
    rs = [_r("1", "kept"), _r("2", "kept"), _r("3", "kept"), _r("4", "partly"), _r("5", "lost")]
    s = lb.summarize(rs)["hi-kn"]
    assert (s.n, s.kept, s.partly, s.lost) == (5, 3, 1, 1)
    assert s.pct_kept == 60.0
    assert s.pct_partly == 20.0
    assert s.pct_lost == 20.0


def test_pairs_are_separate():
    rs = [_r("1", "kept"), _r("2", "lost", pair="mr-hi")]
    out = lb.summarize(rs)
    assert out["hi-kn"].pct_kept == 100.0
    assert out["mr-hi"].pct_kept == 0.0
    assert list(out) == ["hi-kn", "mr-hi"]


def test_rerating_a_turn_counts_once_last_wins():
    rs = [_r("1", "lost"), _r("2", "kept"), _r("1", "kept")]
    s = lb.summarize(rs)["hi-kn"]
    assert s.n == 2
    assert s.kept == 2
    assert s.pct_kept == 100.0


def test_rounding_to_one_decimal():
    rs = [_r("1", "kept"), _r("2", "lost"), _r("3", "lost")]
    assert lb.summarize(rs)["hi-kn"].pct_kept == 33.3


def test_under_300_is_preliminary():
    rs = [_r(str(i), "kept") for i in range(299)]
    s = lb.summarize(rs)["hi-kn"]
    assert not s.final
    assert s.label == "preliminary, n=299"


def test_300_or_more_is_final():
    rs = [_r(str(i), "kept" if i % 10 else "partly") for i in range(300)]
    s = lb.summarize(rs)["hi-kn"]
    assert s.final
    assert s.label == "final, n=300"
    assert s.pct_kept == 90.0


def test_entity_accuracy_ignores_turns_without_entities():
    rs = [
        _r("1", "kept", entities="ok"),
        _r("2", "kept", entities="ok"),
        _r("3", "partly", entities="lost"),
        _r("4", "kept", entities=""),
    ]
    s = lb.summarize(rs)["hi-kn"]
    assert s.entity_n == 3
    assert s.entity_ok == 2
    assert s.pct_entities == 66.7


def test_entity_pct_is_none_when_no_entities():
    assert lb.summarize([_r("1", "kept")])["hi-kn"].pct_entities is None


def test_format_summary_never_says_final_under_300():
    text = lb.format_summary(lb.summarize([_r("1", "kept"), _r("2", "partly")]))
    assert "hi-kn" in text
    assert "50.0%" in text
    assert "preliminary, n=2" in text
    assert "final" not in text.replace("preliminary", "")


def test_format_summary_empty():
    assert "no ratings" in lb.format_summary({})


def test_invalid_rating_rejected():
    with pytest.raises(ValueError):
        lb.Rating(turn_id="1", pair="hi-kn", side="a", rating="great")
    with pytest.raises(ValueError):
        lb.Rating(turn_id="1", pair="hi-kn", side="a", rating="kept", entities="maybe")


# --- turn log loading -----------------------------------------------------------------


def test_load_turns_list_and_ids(tmp_path: Path):
    p = _turns_file(
        tmp_path,
        [
            {"call_id": "c_1", "seq": 1, "side": "a", "pair": "hi-kn", "src_text": "x",
             "tgt_text": "y"},
            {"turn_id": "custom", "side": "b", "pair": "hi-kn", "src_text": "p", "tgt_text": "q"},
            {"side": "a", "pair": "hi-kn", "src_text": "m", "tgt_text": "n"},
        ],
    )  # fmt: skip
    turns = lb.load_turns(p)
    assert [t.turn_id for t in turns] == ["c_1:a:1", "custom", "t2"]
    assert turns[0].src_text == "x" and turns[0].tgt_text == "y"


def test_load_turns_accepts_object_with_turns_key_and_skips_backchannels(tmp_path: Path):
    p = _turns_file(
        tmp_path,
        {
            "turns": [
                {"side": "a", "pair": "hi-kn", "src_text": "haan", "tgt_text": "",
                 "backchannel": True},
                {"side": "a", "pair": "hi-kn", "src_text": "", "tgt_text": ""},
                {"side": "b", "pair": "hi-kn", "src_text": "ok", "tgt_text": "ಸರಿ"},
            ]
        },
    )  # fmt: skip
    turns = lb.load_turns(p)
    assert len(turns) == 1
    assert turns[0].tgt_text == "ಸರಿ"


def test_load_turns_requires_fields(tmp_path: Path):
    p = _turns_file(tmp_path, [{"side": "a", "src_text": "x", "tgt_text": "y"}])
    with pytest.raises(ValueError, match="pair"):
        lb.load_turns(p)


# --- ratings csv ----------------------------------------------------------------------


def test_ratings_csv_round_trip(tmp_path: Path):
    path = tmp_path / "ratings.csv"
    turn = lb.LabelTurn("c_1:a:1", "hi-kn", "a", "गेट पर", "ಗೇಟ್ ಬಳಿ")
    lb.append_rating(path, lb.Rating("c_1:a:1", "hi-kn", "a", "partly", "ok", "a, b", "r1"), turn)
    lb.append_rating(path, lb.Rating("c_1:a:2", "hi-kn", "a", "kept"), None)
    got = lb.read_ratings(path)
    assert [g.rating for g in got] == ["partly", "kept"]
    assert got[0].notes == "a, b"
    assert got[0].rater == "r1"
    assert got[0].entities == "ok"
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("turn_id,pair,side,rating,entities,notes,rater,rated_at")


def test_read_ratings_missing_file(tmp_path: Path):
    assert lb.read_ratings(tmp_path / "nope.csv") == []


# --- web app --------------------------------------------------------------------------


TURNS = [
    lb.LabelTurn("t0", "hi-kn", "a", "src0", "tgt0"),
    lb.LabelTurn("t1", "hi-kn", "b", "src1", "tgt1"),
]


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    ratings = tmp_path / "ratings.csv"
    return TestClient(lb.create_app(TURNS, ratings, rater="me")), ratings


def test_app_serves_page_with_shortcuts(tmp_path: Path):
    client, _ = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "kept" in r.text and "partly" in r.text and "lost" in r.text


def test_app_rate_flow_writes_csv_and_advances(tmp_path: Path):
    client, ratings = _client(tmp_path)
    state = client.get("/api/state").json()
    assert state == {"total": 2, "rated": 0, "next_index": 0}
    turn = client.get("/api/turn/0").json()
    assert turn["src_text"] == "src0" and turn["rating"] is None
    r = client.post(
        "/api/rate", json={"turn_id": "t0", "rating": "kept", "entities": "", "notes": "fine"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["next_index"] == 1
    assert body["summary"]["hi-kn"]["label"] == "preliminary, n=1"
    assert lb.read_ratings(ratings)[0].rater == "me"
    assert client.get("/api/turn/0").json()["rating"] == "kept"


def test_app_resumes_from_existing_ratings(tmp_path: Path):
    client, ratings = _client(tmp_path)
    client.post("/api/rate", json={"turn_id": "t0", "rating": "lost"})
    client2 = TestClient(lb.create_app(TURNS, ratings, rater="me"))
    assert client2.get("/api/state").json()["next_index"] == 1


def test_app_rejects_bad_input(tmp_path: Path):
    client, _ = _client(tmp_path)
    assert client.post("/api/rate", json={"turn_id": "zz", "rating": "kept"}).status_code == 404
    assert client.post("/api/rate", json={"turn_id": "t0", "rating": "meh"}).status_code == 422
    assert client.get("/api/turn/9").status_code == 404


def test_main_summary_only(tmp_path: Path, capsys):
    p = _turns_file(tmp_path, [{"side": "a", "pair": "hi-kn", "src_text": "x", "tgt_text": "y"}])
    ratings = tmp_path / "r.csv"
    lb.append_rating(ratings, lb.Rating("t0", "hi-kn", "a", "kept"), None)
    assert lb.main([str(p), "--ratings", str(ratings), "--summary"]) == 0
    assert "preliminary, n=1" in capsys.readouterr().out
