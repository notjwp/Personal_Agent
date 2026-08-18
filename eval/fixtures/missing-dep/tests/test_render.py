from gradebook.render import render_table


def test_render_table_includes_headers_and_rows():
    out = render_table({"abe": [90, 100], "bea": [70, 80]})
    assert "Student" in out and "Grade" in out
    assert "abe" in out and "bea" in out


def test_render_table_handles_single_student():
    out = render_table({"abe": [100]})
    assert "abe" in out and "A" in out
