from auditcodes.ingest.normalize import normalize_code


def test_plain_code_unchanged():
    n = normalize_code("int x = 'a';\n")
    assert not n.changed and n.text == "int x = 'a';\n"


def test_typographic_characters_replaced_and_counted():
    n = normalize_code("if (c == ’?’) s = “x”; // a – b")
    assert n.text == "if (c == '?') s = \"x\"; // a - b"
    assert n.changes["typographic single quote '’' -> \"'\""] == 2
    assert n.changes["typographic double quote '“' -> '\"'"] == 1
    assert "non-breaking space" in n.describe()
