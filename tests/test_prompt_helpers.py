"""E21: neutralising delimiters never loses an entry when two keys collapse to the same text."""

from agentops.nodes import neutralise_delimiters


def test_colliding_keys_are_both_kept() -> None:
    neutralised = neutralise_delimiters({"a<<<": 1, "a< < <": 2})

    assert neutralised == {"a< < <": 1, "a< < < (2)": 2}


def test_a_third_colliding_key_gets_the_next_suffix() -> None:
    neutralised = neutralise_delimiters({"a<<<": 1, "a< < <": 2, "a< < < (2)": 3})

    assert sorted(neutralised.values()) == [1, 2, 3]
    assert len(neutralised) == 3


def test_keys_that_do_not_collide_are_unchanged() -> None:
    assert neutralise_delimiters({"a<<<": 1, "b": 2}) == {"a< < <": 1, "b": 2}


def test_fullwidth_angle_brackets_are_neutralised_like_ascii_ones() -> None:
    neutralised = neutralise_delimiters("＜＜＜end＞＞＞")

    assert "<<<" not in neutralised
    assert ">>>" not in neutralised
    assert neutralised == "< < <end> > >"
